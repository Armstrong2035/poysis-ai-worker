# AWS Migration Runbook

Migration from Railway + Supabase → AWS ECS Fargate + RDS + Bedrock.

**Estimated time:** 2–4 hours for a first-time setup, mostly waiting on AWS provisioning.  
**Risk:** Low — Railway stays live until the final cutover step. You can abort at any point before that.

---

## Prerequisites

Before you start, have these ready:

- AWS account with Activate credits applied
- AWS CLI installed and configured (`aws configure`)
- Node.js installed (CDK requires it: `npm install -g aws-cdk`)
- Python 3.11+
- Docker Desktop running
- GitHub repo with Actions enabled
- Your current `.env` file (for the values you'll paste into Secrets Manager)

---

## Phase 1 — AWS account one-time setup

### 1.1 Enable Bedrock model access

In the AWS Console:

1. Go to **Amazon Bedrock → Model access** (in `us-east-1`)
2. Click **Manage model access**
3. Enable:
   - `Amazon Titan Embeddings V2` (for the classify block embedder)
   - `Anthropic Claude 3.5 Haiku` (for topic categorization)
   - `OpenAI GPT-5.6 Luna` (for chat — under the OpenAI provider section)
4. Click **Save changes** — access is usually granted within a few minutes

### 1.2 Create an IAM role for GitHub Actions (OIDC)

This lets the CI/CD pipeline authenticate to AWS without storing long-lived keys in GitHub.

```bash
# 1. Create the OIDC identity provider (one-time per account)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# 2. Create the trust policy file
cat > /tmp/github-trust.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:YOUR_GITHUB_ORG/poysis-ai-worker:*"
      },
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      }
    }
  }]
}
EOF

# 3. Create the deploy role
aws iam create-role \
  --role-name PoisysGitHubDeploy \
  --assume-role-policy-document file:///tmp/github-trust.json

# 4. Attach permissions (ECR push + ECS deploy)
aws iam attach-role-policy \
  --role-name PoisysGitHubDeploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

aws iam attach-role-policy \
  --role-name PoisysGitHubDeploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

# 5. Note the role ARN — you'll add it to GitHub Secrets
aws iam get-role --role-name PoisysGitHubDeploy --query 'Role.Arn' --output text
```

### 1.3 Add the role ARN to GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | The ARN from step 1.2 above |

---

## Phase 2 — Deploy infrastructure (CDK)

```bash
cd infra
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Bootstrap CDK in your account/region (one-time)
cdk bootstrap aws://YOUR_ACCOUNT_ID/us-east-1

# Preview what will be created
cdk diff --all

# Deploy all four stacks (~10–15 minutes)
cdk deploy --all --require-approval never
```

When it finishes, CDK prints outputs like:

```
PoisysNetwork:  VpcId = vpc-0abc...
PoisysData:     RdsEndpoint = poysis-rds.xxxx.us-east-1.rds.amazonaws.com
PoisysData:     RdsSecretArn = arn:aws:secretsmanager:...
PoisysSecrets:  AppSecretsArn = arn:aws:secretsmanager:...
PoisysApp:      AlbDns = PoisysApp-xxx.us-east-1.elb.amazonaws.com
PoisysApp:      EcrRepoUri = 123456789.dkr.ecr.us-east-1.amazonaws.com/poysis-worker
PoisysApp:      EcsClusterName = poysis
PoisysApp:      EcsServiceName = poysis-worker
```

Save these — you'll need them in the next steps.

### 2.1 Update the ECS service name in the CI/CD pipeline

The stack now sets this deterministically to `poysis-worker`; no workflow edit is required. The illustrative block below is obsolete.

Open `.github/workflows/deploy.yml` and update the `ECS_SERVICE` env var with the exact service name from the CDK output above:

```yaml
env:
  ECS_SERVICE: PoisysApp-FargateService-xxxx   # ← paste exact name here
```

---

## Phase 3 — Populate secrets

Fill in the real values for every `REPLACE_ME` in the secret. The easiest way is the AWS Console:

1. Go to **Secrets Manager → poysis/app → Retrieve secret value → Edit**
2. Paste in all your values from the current `.env` file

Or via CLI (paste the full JSON in one shot):

```bash
aws secretsmanager put-secret-value \
  --secret-id poysis/app \
  --secret-string '{
    "OPENAI_API_KEY": "sk-...",
    "BEDROCK_API_KEY": "...",
    "GEMINI_API_KEY": "...",
    "DEEPSEEK_API_KEY": "...",
    "LLAMA_CLOUD_API_KEY": "...",
    "GOOGLE_CLIENT_ID": "...",
    "GOOGLE_CLIENT_SECRET": "...",
    "GOOGLE_REDIRECT_URI": "http://YOUR_ALB_DNS/auth/google/callback",
    "YOUTUBE_API_KEY": "...",
    "YT_DLP_PROXY": "...",
    "NANGO_BASE_URL": "...",
    "NANGO_SECRET_KEY": "...",
    "CLIENT_URL": "http://your-frontend-url/workspace",
    "MCP_SERVER_URL": "http://YOUR_ALB_DNS/mcp",
    "WORKER_BASE_URL": "http://YOUR_ALB_DNS",
    "RESEND_API_KEY": "...",
    "CONSOLIDATION_SYNC_KEY": "generate-a-random-secret-here",
    "POYSIS_ADMIN_USER_IDS": "...",
    "SEED_WORKSPACE_ID": "...",
    "POYSIS_SEED_USER_ID": "...",
    "WORKSPACE_ID": "...",
    "OPENROUTER_API_KEY": "...",
    "VERTEX_API_KEY": "..."
  }'
```

> **Important:** Update `GOOGLE_REDIRECT_URI` in Google Cloud Console to include the new ALB URL.
> Go to **Google Cloud Console → APIs → Credentials → OAuth 2.0 Client → Authorized redirect URIs**
> and add `http://YOUR_ALB_DNS/auth/google/callback`.

### 3.1 Update the EventBridge sync Lambda

The Lambda now reads `CONSOLIDATION_SYNC_KEY` from `poysis/app` at invocation time. Do not set the key in the Lambda environment; the legacy commands below are obsolete.

After deploying, update the `CONSOLIDATION_SYNC_KEY` in the sync Lambda's environment:

```bash
# Get the Lambda function name
FUNC=$(aws lambda list-functions --query "Functions[?starts_with(FunctionName,'PoisysApp-SyncCron')].FunctionName" --output text)

# Update its env var with the real key
aws lambda update-function-configuration \
  --function-name $FUNC \
  --environment "Variables={WORKER_URL=http://YOUR_ALB_DNS,CONSOLIDATION_SYNC_KEY=your-real-key}"
```

---

## Phase 4 — Database migration

### 4.1 Get the RDS connection string

```bash
# Fetch RDS credentials from Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id poysis/rds \
  --query 'SecretString' \
  --output text | python -c "
import sys, json
s = json.load(sys.stdin)
print(f'postgresql://{s[\"username\"]}:{s[\"password\"]}@{s[\"host\"]}:{s[\"port\"]}/{s[\"dbname\"]}')
"
```

### 4.2 Run the migration

```bash
# Set both connection strings
export SUPABASE_DIRECT_CONNECTION_STRING="postgresql://..."   # your current Supabase DSN
export RDS_CONNECTION_STRING="postgresql://..."               # from step 4.1

# Step 1: Create schema (tables, indexes, extensions)
python scripts/migrate_db.py --step schema

# Step 2: Migrate all data (this can take a while for the vectors table)
python scripts/migrate_db.py --step data
```

To resume after an error on a specific table:

```bash
python scripts/migrate_db.py --step data --table vectors
```

### 4.3 Build the HNSW vector index

Run this after all data is migrated — building on a full dataset is much faster than incremental:

```bash
psql $RDS_CONNECTION_STRING -c "
CREATE INDEX CONCURRENTLY vectors_embedding_hnsw
ON vectors USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
"
```

> This can take 10–30 minutes on a large vectors table. `CONCURRENTLY` means it won't lock the table.

### 4.4 Verify row counts

```bash
psql $RDS_CONNECTION_STRING -c "
SELECT relname AS table, n_live_tup AS rows
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
"
```

Compare against Supabase — counts should match.

---

## Phase 5 — First deploy

Push to `main` to trigger the GitHub Actions pipeline, or trigger it manually:

```bash
git add -A
git commit -m "chore: AWS migration"
git push origin main
```

Watch the pipeline in **GitHub → Actions**. It will:
1. Build the Docker image
2. Push to ECR
3. Force a new ECS deployment
4. Wait for the service to stabilize

### 5.1 Check the app is healthy

```bash
ALB_DNS="YOUR_ALB_DNS"

# Health check
curl http://$ALB_DNS/ping
# Expected: {"status":"ok"}

# Basic API check
curl http://$ALB_DNS/
# Expected: {"message":"Poysis Worker API is online","mode":"multi-tenant"}
```

### 5.2 Check CloudWatch logs

```bash
aws logs tail /ecs/poysis-worker --follow
```

Look for the startup line: `[STARTUP] Reaped N orphaned 'running' consolidation job(s)`

---

## Phase 6 — Cutover

Once the app is healthy on AWS:

1. **Update your frontend** — change `NEXT_PUBLIC_WORKER_URL` (or equivalent) from the Railway URL to the ALB DNS
2. **Update Google OAuth redirect URI** — add the ALB URL to authorized redirect URIs in Google Cloud Console (you may have done this in Phase 3 already)
3. **Update Nango webhook URL** — if Nango is configured to call back to the worker, update it to the ALB URL
4. **Monitor for 30 minutes** — watch CloudWatch logs and verify consolidation jobs, chat, and OAuth flows work
5. **Decommission Railway** — once satisfied, delete the Railway service

---

## Phase 7 — Post-cutover cleanup

```bash
# Remove dead code from docker-compose.yml (Qdrant service is unused)
# The file is safe to delete or simplify to just the api service for local dev.

# Remove Supabase env vars from any remaining .env files:
#   SUPABASE_PRODUCT_URL
#   SUPABASE_SERVICE_ROLE_KEY
#   SUPABASE_PUBLISHABLE_KEY
# These are no longer used — the supabase SDK has been removed.
```

Remove from `requirements.txt` if you no longer need local dev Gemini fallback:
```
google-generativeai
llama-index-llms-google-genai
```

---

## Architecture reference

```
Internet
   │
   ▼
ALB (public subnet, port 80)
   │
   ▼
ECS Fargate task (PUBLIC subnet, 1vCPU / 2GB, public IP for outbound)
  ├── gunicorn + uvicorn (1 worker, port 8000)
  ├── Secrets injected from Secrets Manager at start
  ├── Logs → CloudWatch /ecs/poysis-worker
  └── IAM task role → Bedrock (InvokeModel)
   │
   ├──► RDS PostgreSQL 16 + pgvector (isolated subnet, port 5432)
   │      db.t4g.small, 100GB gp3, 7-day backup
   │
   └──► AWS Bedrock / external APIs (direct outbound via public IP, no NAT)
          - Titan Embeddings V2    (classify block)
          - Claude 3.5 Haiku       (topic categorization)
          - GPT-5.6 Luna via OAI   (chat / RAG)

EventBridge (every 6h)
   └──► Lambda → POST /consolidation/sync

GitHub Actions (on push to main)
   └──► ECR push → ECS force-new-deployment
```

---

## Cost estimate

| Service | Config | Estimated monthly cost |
|---|---|---|
| ECS Fargate | 1 task × 1vCPU / 2GB | ~$30 |
| RDS db.t4g.small | 100GB gp3, single-AZ | ~$25 |
| ALB | per hour + LCU | ~$20 |
| NAT Gateway | **eliminated** — ECS in public subnets | $0 |
| ECR | image storage | <$5 |
| Secrets Manager | 2 secrets | <$2 |
| CloudWatch Logs | 1 month retention | ~$5 |
| Bedrock | usage-based | depends on traffic |
| **Total (infra only)** | | **~$87/month** |

AWS Activate credits will cover this. Bedrock model calls are usage-based and billed against credits at the Bedrock rates in `BEDROCK_MIGRATION.md`.

**Scale-up triggers** — bump these if you hit limits:
- OOM kills in ECS → increase to `cpu=2048, memory=4096` in `app_stack.py`
- RDS `FreeableMemory` < 200 MB consistently → upgrade to `db.t4g.medium` in `data_stack.py`
- Both changes are a `cdk deploy PoisysApp` / `cdk deploy PoisysData` away — no data migration needed for RDS resize (just a brief restart)

---

## Troubleshooting

**ECS task fails to start (stops immediately)**
- Check CloudWatch logs: `aws logs tail /ecs/poysis-worker`
- Most common cause: missing secret key in `poysis/app`. The container exits if a required env var is absent.

**Database connection refused**
- Confirm the ECS security group is allowed through the RDS security group (CDK sets this up, but check if you deployed DataStack and AppStack separately with a long gap).
- Check that `DB_HOST`, `DB_PORT` etc. are populated: look at the running task's environment in the ECS console.

**Bedrock AccessDeniedException**
- Model access hasn't been enabled yet (Phase 1.1) — or the task role is missing the Bedrock policy. CDK adds it automatically; check the `TaskRole` in IAM.

**GitHub Actions: `role-to-assume` error**
- The OIDC provider or trust policy wasn't set up correctly. Re-run Phase 1.2 and verify the `repo:` condition matches your exact org/repo name.

**Vectors returning 0 results after migration**
- The HNSW index may not exist yet on RDS. Run the `CREATE INDEX` command from Phase 4.3.
- Alternatively, the namespace in the vectors table may differ — confirm rows exist: `SELECT DISTINCT namespace FROM vectors LIMIT 20;`
