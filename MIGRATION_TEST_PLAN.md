# AWS Migration — Test Plan

**Status at start:** ECS runs 1/1 and is stable. The ALB answers `/ping` with 200. RDS is available. The log shows health checks only — no user request has reached the deployment. The database connection is unproven.

**Budget:** $25,000 in AWS credits. The run-rate is approximately $95 each month. Cost is not a constraint on testing. Test freely and leave the stack running.

**ALB:** `http://Poisys-Farga-oLETQu7c2XUK-349888908.us-east-1.elb.amazonaws.com`

Set this first. Every command below uses it:

```bash
export ALB="http://Poisys-Farga-oLETQu7c2XUK-349888908.us-east-1.elb.amazonaws.com"
```

The phases are in risk order. Each phase has an exit test. Do not start a phase until the phase before it passes. A failure in an early phase makes all later results meaningless.

---

## Phase 0 — The database connection

This is the largest unknown. `/ping` returns 200 without a database, so the current green status proves nothing.

**Test**

Call any endpoint that reads a table. A workspace or sources endpoint is enough:

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: <your-user-id>" \
  "$ALB/sources/list?workspace_id=<your-workspace-id>"
```

Watch the log at the same time:

```bash
aws logs tail /ecs/poysis-worker --follow
```

**Pass:** the response is 200 or an empty result. No connection error appears in the log.

**Fail — and what it means**

| Symptom | Cause |
|---|---|
| Timeout, no log line | The security group blocks ECS to RDS on port 5432 |
| `could not connect to server` | `DB_HOST` is wrong, or RDS is unreachable from the task subnet |
| `password authentication failed` | The `poysis/rds` secret does not match the instance |
| `relation "..." does not exist` | The schema migration never ran — go to Phase 1 |

**Cost:** $0.

---

## Phase 1 — The data migration

`scripts/migrate_db.py` is committed, but there is no evidence it ran. Confirm the schema and the row counts.

RDS sits in an isolated subnet, so you cannot reach it from your laptop. Use one of these:

- **ECS Exec** into the running task, then use `psql` or a short Python script
- A temporary bastion host in a public subnet
- An RDS query through an endpoint you add to the app

**Test**

```sql
SELECT relname AS table_name, n_live_tup AS rows
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
```

**Pass:** the tables exist. The row counts match Supabase. The `vectors` table is not empty.

**Also confirm the extension and the index:**

```sql
SELECT extname FROM pg_extension WHERE extname = 'vector';
SELECT indexname FROM pg_indexes WHERE tablename = 'vectors';
```

The HNSW index is Phase 4.3 of the runbook. Retrieval returns zero results without it, or it becomes very slow. This is a common cause of a "working" deployment that finds nothing.

**Cost:** $0.

---

## Phase 2 — The integration test suite

The repository already has integration tests. They run against a live server, so you can point them at the ALB. This is the highest value for the least work.

```bash
WORKER_URL="$ALB" pytest tests/ -v
```

Start with the authorization tests, because a tenancy fault is the worst kind to find later:

```bash
WORKER_URL="$ALB" pytest tests/test_auth_isolation.py -v
```

**Pass:** the tests give the same result against the ALB as against your local server.

**Note:** some tests may assume local state. Record which tests fail for an environment reason, and which fail for a real reason. Do not treat them the same.

**Cost:** near $0.

---

## Phase 3 — Ingestion

Use a small source. Do not start with a large corpus.

1. Connect one Google Drive folder that holds 3 to 5 documents, or one short YouTube video.
2. Start a snapshot.
3. Watch the SSE progress stream.
4. Watch the log for the batch and embedding lines.

**Pass:** the job completes. New rows appear in `consolidation_indexed_files` and in `vectors`.

**Watch for these:**

- **An OOM kill.** The task has 2 GB. Clustering uses BERTopic, UMAP and HDBSCAN, which need a lot of memory. If the task restarts during the job, raise `cpu=2048, memory=4096` in [app_stack.py:122](infra/poysis_infra/app_stack.py#L122).
- **An outbound network failure.** The task has a public IP and no NAT Gateway. Confirm that Google, YouTube and your proxy are all reachable.
- **The YouTube proxy.** `YT_DLP_PROXY` was needed on Railway because the datacenter IP was blocked. The AWS IP is new and may also be blocked. Test this from AWS, not from your laptop.

**Cost:** small. Embeddings bill to OpenAI, not to AWS.

---

## Phase 4 — Retrieval and chat

This is the first phase that spends money on Bedrock.

```bash
curl -s -N -X POST "$ALB/chat/stream" \
  -H "X-User-ID: <your-user-id>" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"<id>","message":"<a question about the documents you ingested>"}'
```

**Pass:** the answer streams back. The sources are correct. The answer uses the documents from Phase 3 and does not invent content.

**Then confirm Bedrock is really in use:**

```bash
aws ce get-cost-and-usage \
  --time-period Start=<today>,End=<tomorrow> \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}'
```

A non-zero number proves the chat path reaches Bedrock. A zero means the request went to an external provider instead. Bedrock is $0 today, so this test tells you something you do not yet know.

**Also test:** clustering, and the topic and story output. This is the Claude Haiku path in `categorizer.py`.

**Cost:** a few dollars at most for a small corpus.

---

## Phase 5 — The MCP endpoint

Connect Claude.ai to `$ALB/mcp/<workspace_id>` as a custom connector.

**Pass:** Claude lists the three tools, and `retrieve_from_knowledge_base` returns real content.

**Expect a problem here.** Claude.ai requires HTTPS for remote connectors. Your ALB serves HTTP only. This phase probably needs the ACM certificate and the Route 53 domain first. If so, move that work forward from the WAFR remediation list, because it blocks your main product interface.

**Cost:** $0 beyond the inference.

---

## Phase 6 — The scheduled sync

The EventBridge rule fires every 6 hours and calls a Lambda.

```bash
aws logs tail /aws/lambda/PoisysApp-SyncCron5A48D1D2-i3qUP30574mL --since 24h
```

**Pass:** the log shows `sync OK: 200`. It does not show an authorization failure or a timeout.

You can invoke the Lambda directly instead of waiting 6 hours:

```bash
aws lambda invoke --function-name PoisysApp-SyncCron5A48D1D2-i3qUP30574mL /dev/stdout
```

**Cost:** $0.

---

## Phase 7 — Failure behaviour

Only start this after Phases 0 to 6 pass. You are testing recovery, not function.

1. **Task restart.** Stop the running task. Confirm that ECS starts a new one and that the ALB recovers.
2. **Deployment rollback.** Push a broken image. Confirm that the circuit breaker rolls back, as configured at [app_stack.py:237](infra/poysis_infra/app_stack.py#L237).
3. **Orphaned jobs.** Stop the task during a snapshot. Restart it. Confirm that the startup reaper marks the stale job. Note that no `[STARTUP]` line appears in the log today, so this code path may not run.
4. **Auto-scaling.** Send load until CPU passes 70%. Confirm a second task starts. Remember that a second task doubles your Fargate cost.

---

## Cost control during testing

Cost is not urgent with $25,000 in credits. Keep these for later, or if you pause the project:

- **Stop the stack between test sessions.** Set the ECS desired count to 0. This saves the Fargate cost but not the RDS or ALB cost.
  ```bash
  aws ecs update-service --cluster poysis --service poysis-worker --desired-count 0
  ```
- **Stop RDS when you are not testing.** An RDS instance can stop for up to 7 days. RDS is your largest single cost.
- **Set a budget alarm at $50.** This warns you before the credits run out.
- **Do not leave auto-scaling to run unattended** after a load test.

The ALB costs approximately $20 each month and cannot be stopped. Delete it only if you pause the project completely.

---

## Order summary

| Phase | Proves | Cost | Blocks |
|---|---|---|---|
| 0 | The database connection works | $0 | Everything |
| 1 | The data migrated, the index exists | $0 | Retrieval |
| 2 | The API behaves as before | ~$0 | — |
| 3 | Ingestion works on AWS | Small | Retrieval |
| 4 | Retrieval, chat and Bedrock work | A few $ | MCP |
| 5 | The MCP interface works | $0 | Probably needs HTTPS |
| 6 | The scheduled sync works | $0 | — |
| 7 | Recovery works | Small | — |
