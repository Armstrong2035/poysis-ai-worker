# AWS WAFR Credit Application — Poysis.com

**Company:** Poysis
**Workload:** Poysis AI Worker — multi-tenant knowledge consolidation & retrieval platform
**AWS Account ID:** `440783445469`
**Region:** `us-east-1`
**Status:** Migrated from Railway + Supabase to AWS. Infrastructure live via AWS CDK (4 stacks). Expansion workloads planned for the next 3 months.

---

## 1. Project breakdown

Poysis is a multi-tenant AI knowledge platform. It ingests a customer's scattered documents (Google Drive, Notion, Slack, GitHub, YouTube), chunks and embeds them into a vector store, clusters them into a topic hierarchy, and exposes the resulting knowledge base three ways: a streaming RAG chat API, a set of domain-agnostic retrieval/classification blocks, and a hosted MCP (Model Context Protocol) server that Claude.ai and ChatGPT connect to directly.

**Completed in the last 2 months — full migration onto AWS:**

| Workload | Implementation |
|---|---|
| Application runtime | Containerised FastAPI worker on **ECS Fargate**, fronted by an **ALB**, images in **ECR**, deployed by **GitHub Actions** via IAM OIDC (no static keys) |
| Data layer | **RDS PostgreSQL 16** with `pgvector` + HNSW index, in isolated subnets. Replaced Supabase entirely. Includes a full data migration of the vectors table |
| AI inference | **Amazon Bedrock** — Claude Haiku for topic categorisation/clustering, and an OpenAI-compatible Bedrock chat model for RAG synthesis. Task role scoped to `bedrock:InvokeModel`. Document embeddings still run on an external provider; the move to Titan Embeddings V2 is the next step (see planned work below) |
| Networking | Purpose-built **VPC**, 2 AZs, public + private-isolated subnet tiers, NAT Gateway deliberately eliminated for cost, security groups restricting ALB→ECS→RDS |
| Secrets | **AWS Secrets Manager** — all ~25 credentials injected into the task definition at container start; nothing in plaintext |
| Scheduled work | **EventBridge** rule every 6h → **Lambda** → incremental source-sync endpoint |
| Observability | **CloudWatch Logs** + Container Insights, ALB and container health checks, ECS deployment circuit breaker with auto-rollback |
| IaC | 100% **AWS CDK** (Python): `PoysisNetwork`, `PoysisData`, `PoysisSecrets`, `PoysisApp` |

**Architecture:**

```
Internet → ALB (public) → ECS Fargate (1 vCPU / 2 GB, autoscale 1→3 on 70% CPU)
                              ├─→ RDS PostgreSQL 16 + pgvector (isolated subnets)
                              ├─→ Amazon Bedrock (categorisation, chat synthesis)
                              └─→ CloudWatch Logs / Container Insights
EventBridge (6h) → Lambda → /consolidation/sync
GitHub Actions (OIDC) → ECR → ECS force-new-deployment
```

**Planned in the next 2–3 months (the workloads WAFR funding would support):**

1. **Production hardening from the WAFR itself** — Multi-AZ RDS, HTTPS via ACM + Route 53, AWS WAF on the ALB, VPC endpoints, GuardDuty/Security Hub, and reinstating a NAT Gateway for private-subnet tasks. These are the reliability/security pillar remediations we expect the review to surface.
2. **Move document embeddings to Bedrock Titan** — the chat and categorisation paths already run on Bedrock. The embedding path still calls an external provider. Moving it to Titan Embeddings V2 puts the highest-volume AI workload on AWS, because embedding volume scales with document count rather than question count.
3. **Hosted MCP server at scale** — the per-workspace MCP endpoint becomes a first-class product surface for Claude/ChatGPT clients, driving a step change in inference and request volume.
4. **Query routing layer** — an inference-time classifier that routes each request between cheap retrieval and expensive synthesis paths. Additional Bedrock model usage.
5. **Semantic topic analysis** — LLM-driven analysis during the clustering pass to characterise topic content. Batch Bedrock workload, run per snapshot.
6. **Metering & billing pipeline** — per-workspace token/usage accounting for monetisation, backed by CloudWatch metrics and RDS.
7. **Scale-out of the ingestion pipeline** — ECS task size increase (2 vCPU / 4 GB) and higher max task count as corpus sizes grow, plus S3 for raw document staging.

---

## 2. AWS services

**In production today:**
Amazon ECS (Fargate) · Amazon ECR · Elastic Load Balancing (ALB) · Amazon RDS for PostgreSQL (pgvector) · Amazon Bedrock · AWS Lambda · Amazon EventBridge · AWS Secrets Manager · Amazon VPC · Amazon CloudWatch (Logs, Metrics, Container Insights) · AWS IAM (incl. OIDC federation) · AWS CloudFormation / AWS CDK

**Planned within 3 months:**
Amazon Route 53 · AWS Certificate Manager · AWS WAF · Amazon S3 · Amazon CloudFront · Amazon GuardDuty · AWS Security Hub · VPC Endpoints (PrivateLink) · Amazon Bedrock Knowledge Bases · AWS Application Auto Scaling · Amazon SQS (ingestion queueing)

---

## 3. Cost

### Current baseline (post-migration, infrastructure only)

| Service | Configuration | Est. monthly |
|---|---|---|
| ECS Fargate | 1 task × 1 vCPU / 2 GB | ~$30 |
| RDS PostgreSQL | db.t4g.small, 100 GB gp3, single-AZ | ~$25 |
| Application Load Balancer | hourly + LCU | ~$20 |
| ECR | image storage | <$5 |
| Secrets Manager | 2 secrets | <$2 |
| CloudWatch Logs | 1-month retention | ~$5 |
| Lambda + EventBridge | 4 invocations/day | <$1 |
| NAT Gateway | eliminated by design | $0 |
| **Infrastructure subtotal** | | **~$87/mo** |
| Amazon Bedrock | usage-based; $0 to date | $0 so far |
| **Current total** | | **~$95/mo measured** |

Measured from Cost Explorer: the infrastructure went live on 9 August 2026. Full days show $3.06–$3.26 each. This gives a run-rate of approximately **$95/month**. We hold $25,000 in AWS credits, so the current run-rate is fully covered. The credits give long runway at today's spend, but the planned workloads in section 1 raise the monthly cost substantially.

### Projected 12-month cost with planned workloads

| Phase | Scope | Est. monthly |
|---|---|---|
| Months 1–3 | Current baseline + WAFR remediations (Multi-AZ RDS, WAF, ACM/Route 53, VPC endpoints, GuardDuty) | ~$350–450 |
| Months 4–6 | MCP server scale-out, query routing, semantic analysis; ECS to 2 vCPU / 4 GB and 2–3 tasks; Bedrock volume up ~4× | ~$900–1,300 |
| Months 7–12 | Multi-tenant growth: larger RDS instance + storage, S3 + CloudFront, SQS ingestion, sustained Bedrock inference | ~$2,000–3,000 |

**Estimated 12-month AWS spend: ~$15,000–20,000**, with roughly 55–65% of that being Amazon Bedrock inference (embeddings, categorisation, and RAG synthesis) as usage scales.

### Current monthly spend

Approximately **$95/month**, measured from Cost Explorer. The infrastructure started on 9 August 2026, so total spend to date is $8.46. The largest items are RDS, ECS Fargate, and the load balancer. Bedrock inference is $0 to date, because customer traffic has not yet started. Bedrock becomes the largest cost item as the workloads in section 1 start.

---

## 4. Why WAFR is a good fit

The workload was rebuilt on AWS-native primitives in the last two months and the stack is deployed and stable, but it was deliberately cost-optimised over resilience for launch: single-AZ RDS, 1-day backup retention, no NAT Gateway, HTTP-only ALB, and no WAF. Those are exactly the Reliability and Security pillar gaps a Well-Architected Review would identify, and the remediations are already scoped in our roadmap above. The credits would fund closing them ahead of the growth in Bedrock-backed workloads planned for the next quarter.
