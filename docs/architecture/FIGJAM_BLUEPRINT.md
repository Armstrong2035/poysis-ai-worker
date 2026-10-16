# FigJam blueprint: Poysis backend

Use this as the drawing guide for [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md). Snapshot: working-tree code and infrastructure configuration on 14 September 2026. Frontend hosting is out of scope.

Tabular copies: [62 component cards](FIGJAM_COMPONENTS.csv) and [21 overview connections](FIGJAM_CONNECTIONS.csv). These are ordinary CSV files for copying or use with an import tool of your choice, not a native FigJam board. Detailed sub-flow arrows are specified in the frame instructions below.

## Board conventions

Create separate frames rather than one giant diagram. Keep the overview simple, then expand the same components in detail frames. Reuse the IDs below so a component has the same identity across frames.

| Visual | Meaning |
|---|---|
| Blue rectangle | Application module inside the FastAPI deployment. |
| Purple rectangle | Managed infrastructure or external service. |
| Cylinder | Persistent storage. |
| Gray rectangle | External caller or operator. |
| Amber rectangle / dashed border | Partial, local, optional, or unverified integration. |
| Solid arrow | Implemented runtime request, response, or data transfer. |
| Dashed arrow | Partial wiring, optional path, or undeployed host integration. Label which one. |
| Dotted arrow | Deployment/configuration relationship; not an API request. |
| Red sticky note | Concrete architecture finding; reference its ID in FINDINGS.md. |

Put an evidence badge on each box: CODE, CONFIG, LOCAL, PARTIAL, UNKNOWN, or PROPOSED. An infrastructure declaration is CONFIG even if it describes a service intended for production. Do not label it live-verified.

Use this box template:

```text
[ID] Component name
Responsibility
Input → output
State / process / deployment boundary
Important limits
Evidence status
```

## Frame layout

| Frame | Title | Layout | Detail reference |
|---|---|---|---|
| 01 | Backend overview | Callers left; API center; stores below; providers right | Architecture sections 1–4 |
| 02 | AWS runtime | VPC outer box; public and isolated subnet groups | Section 2 |
| 03 | Request and access boundary | Middleware chain, then identity/membership/scope decisions | Sections 3–5 |
| 04 | API capability catalog | Group route cards under their router modules | Section 4 + API_INVENTORY.md |
| 05 | Source acquisition | Drive, YouTube, partial Notion lanes into common chunk contract | Section 6 |
| 06 | Document indexing | Parse → split → embed → upsert → checkpoint | Section 7 |
| 07 | Transcript indexing | Timed windows → embed → merge → embed → upsert | Section 7 |
| 08 | Search and chat | Query preparation → search → selection → mode branch | Sections 8–9 |
| 09 | Topic organization | Centroids → UMAP/HDBSCAN → model labels → persistence | Section 10 |
| 10 | Data model | Table groups with enforced versus logical relationships | Section 12 + DATA_SCHEMA.md |
| 11 | Schedule and job lifecycle | EventBridge → Lambda → API → in-process jobs → DB progress | Section 15 |
| 12 | MCP | External assistant → hosted tools → evidence; optional stdio wrapper | Section 11 |
| 13 | Marketing | Observations, rules, and Keyword Planner in distinct lanes | Section 13 |
| 14 | Interpretation engine | Separate optional host, lease store, evidence, two model passes | Section 14 |
| 15 | Deployment and operations | GitHub → ECR → ECS; monitoring and failure boundaries | Section 16 |
| 16 | Cost and capacity | One sticky per cost driver; quantities before prices | Section 17 |
| 17 | API-platform decisions | Proposed contracts and findings, separate from current system | Section 18 + FINDINGS.md |

## Component cards

These IDs describe logical components. Only the explicitly identified managed services and external hosts get separate deployment boxes.

| ID | Box label | Detail to write inside | Status |
|---|---|---|---|
| X01 | API consumer | Sends JSON/form requests; receives JSON or custom streams | External actor |
| X02 | External assistant | Uses MCP tools; generates its own answer outside this backend | External actor |
| X03 | Operator / GitHub | Changes code, triggers deployments, inspects jobs | External actor |
| I01 | Public ALB | Routes to healthy API tasks; health check `/ping`; port 80 or conditional TLS 443 | CONFIG |
| I02 | ECS Fargate service | Desired 1, min 1/max 3 tasks; each 1 vCPU / 2048 MiB | CONFIG |
| I03 | API container | Python 3.11; Gunicorn; one Uvicorn worker; port 8000 | CONFIG |
| I04 | RDS PostgreSQL | `db.t4g.small`, PostgreSQL 16.4 configured, 100–500 GiB gp3, single-AZ | CONFIG |
| I05 | ECR repository | Existing `poysis-worker` repository; SHA and latest image tags | CONFIG |
| I06 | CloudWatch | App/access logs, container telemetry, rejected VPC flows | CONFIG |
| I07 | Secrets Manager | Runtime configuration references; values not part of this board | CONFIG |
| I08 | EventBridge schedule | Fires every six hours; does not process documents | CONFIG |
| I09 | Sync-trigger Lambda | Calls POST `/consolidation/sync`; 60s function timeout, 30s HTTP timeout | CONFIG |
| A01 | Middleware | CORS → input check → rate limit → logging → error wrapper | CODE |
| A02 | Identity dependency | Configurable bearer verification; legacy header fallback remains | CODE / runtime UNKNOWN |
| A03 | Workspace access | Membership check; owner fallback; route-specific role checks | CODE |
| A04 | Retrieval scope | Namespace plus optional connection/category selections | CODE |
| A05 | Source API | Connect/list/disconnect; channel seeding; playlist import | CODE |
| A06 | Consolidation API | Discover, start snapshot, status, progress stream, sync | CODE |
| A07 | Snapshot background job | In-process orchestration; persisted status is not durable execution | CODE |
| A08 | SnapshotRunner | Lists items, skips ETags, fetches and parses sources | CODE |
| A09 | Processors | Text, spreadsheet, PDF, Office, timed transcript normalization | CODE |
| A10 | ConsolidationEngine | Batches 200 chunks; coordinates embedding/checkpoints | CODE |
| A11 | KnowledgeEngine | Text splitting, document/query embeddings, transcript two-pass flow | CODE |
| A12 | VectorService | pgvector reads/writes, counts, source grouping, metadata updates | CODE |
| A13 | DatabaseService | Application tables; shared psycopg2 pool | CODE |
| A14 | Chat orchestrator | History, rewrite, retrieval, mode, answer/quote, source/meta stream | CODE |
| A15 | ClusteringEngine | At least 10 docs; delegates categorization | CODE |
| A16 | UMAP/HDBSCAN | Existing doc centroids → temporary 5D coordinates → candidate groups | CODE |
| A17 | Categorizer | Model chooses/names groups; summaries/stories; topic persistence | CODE |
| A18 | Playlist overlay | Human categories, locks, video membership, vector stamping | CODE |
| A19 | Hosted MCP | initialize/tools/list/tools/call; three evidence tools | CODE |
| A20 | Local stdio MCP wrapper | Optional desktop-side HTTP client to REST knowledge endpoints | Optional CODE |
| A21 | Label classifier | Two embedding calls per label; default threshold .65 | CODE |
| A22 | Legacy blocks | `/cluster/cluster`, `/recommend/recommend`; incompatible engine calls | PARTIAL |
| A23 | Tracking / analytics | Predecessor attribution contracts and dashboard/log reads | CODE / compatibility findings |
| A24 | Build suggestions | Topics/stories → direct Gemini → bot template suggestions | CODE |
| A25 | Admin / waitlist | Admin diagnostics; separate public signup handler | CODE |
| P01 | Google Drive | Metadata listing, text export, temporary binary download | External service |
| P02 | YouTube Data API | Channel/upload metadata, durations, playlists | External service |
| P03 | YouTube captions | Library default; optional yt-dlp path and proxy | External dependency |
| P04 | Nango + Notion | Connector exists; not exposed through snapshot request wiring | PARTIAL |
| P05 | OpenAI embeddings | `text-embedding-3-small`, document/query vectors = 1536D | CODE selection |
| P06 | Chat provider | Configured OpenAI-compatible endpoint; model literal requires live verification | CODE / CONFIG |
| P07 | Categorization provider | Bedrock Haiku branch; direct Gemini fallback by configuration | CODE selection |
| P08 | Classification provider | Titan V2 1024D on AWS; separate local Gemini fallback | CODE selection |
| P09 | Optional LlamaParse | Direct PDF-upload parsing branch only when configured | Optional CODE |
| D01 | Vectors + chunk text | `vectors`, shared namespace, embedding + JSONB provenance/text | CODE / declared schema |
| D02 | Workspace / connection records | Workspaces, members, profiles, Drive/Nango/YouTube records | CODE / declared schema |
| D03 | Jobs + file checkpoints | `consolidation_jobs`, `consolidation_indexed_files` | CODE / declared schema |
| D04 | Topics + stories | Hierarchy, narratives, overrides, playlist document membership | CODE / declared schema |
| D05 | Product events | Search, attribution, topic-query events, waitlist records | CODE / declared schema |
| M01 | Marketing parsers | Host supplies GA4/Search Console reports → daily observations | LOCAL |
| M02 | Marketing service/store | Scoped SQL persistence and deterministic opportunity evaluation | LOCAL |
| M03 | Rule preview | Utility model → validated preview; cannot save/execute | LOCAL |
| M04 | Keyword Planner import | Normalize + fingerprint → research snapshot | LOCAL |
| M05 | Marketing tables | Observations, revisioned rules, research reports in main PostgreSQL | LOCAL schema |
| H01 | Trusted interpretation host | Must supply identity, grants, model, repository; not configured here | Required host / UNKNOWN |
| H02 | HermeneuticsEngine | Submit/get/evidence/run_next; plans and validates two-pass interpretation | LOCAL / unmounted |
| H03 | Evidence adapter | Scoped metrics, derived signals, document retrieval, prior hypotheses | LOCAL |
| H04 | Context packet | Versioned evidence/provenance; 60,000-character limit | LOCAL |
| H05 | Strong model adapter | Host-supplied model; initial interpretation then challenge/synthesis | LOCAL / injected |
| H06 | SQLite repository | Persistent host file; idempotency, leased jobs, evidence/link tables | LOCAL / host not configured |

## Overview arrows

| From | To | Arrow label | Style |
|---|---|---|---|
| X01 | I01 | HTTP API requests | Solid |
| X02 | I01 | Hosted MCP JSON-RPC | Solid |
| I01 | I02 | Route to healthy task targets on port 8000 | Solid |
| I02 | I03 | Runs one app worker per task | Containment |
| I03 | I04 | SQL, TCP 5432 | Solid |
| I03 | P01 | Source metadata/content requests | Solid |
| I03 | P02 | Channel/playlist metadata requests | Solid |
| I03 | P03 | Caption requests | Solid |
| I03 | P04 | Internal connector path; public snapshot wiring incomplete | Dashed |
| I03 | P05 | Document/query embedding calls | Solid |
| I03 | P06 | Chat utility and generation calls | Solid |
| I03 | P07 | Category/summary/story generation calls | Solid |
| I03 | P08 | Temporary label-comparison embeddings | Solid |
| I03 | P09 | Optional direct-upload PDF parsing | Dashed |
| I05 | I02 | Container image deployment | Dotted |
| I07 | I03 | Referenced configuration at task startup | Dotted |
| I03 | I06 | Logs and operational telemetry | Solid |
| I08 | I09 | Scheduled invocation every six hours | Solid |
| I09 | I01 | POST /consolidation/sync | Solid |
| H01 | H02 | Future host integration required | Dashed |
| H02 | H06 | Durable local jobs/results/evidence | Solid within optional package frame |

## Frame 02: ALB and networking close-up

Draw AWS region → VPC → two subnet groups:

```text
Public subnet group, across up to two AZs
  Public ALB
    → target group: /ping health check every 30s
    → healthy task IP:8000
  Fargate task 1: API container
  Fargate task 2: optional scale-out
  Fargate task 3: optional scale-out
  Tasks have public IPs for outbound provider access

Isolated subnet group
  Single-AZ RDS PostgreSQL instance
    ← TCP 5432 allowed from VPC CIDR

NAT gateways: zero
```

ALB listeners: HTTP 80 when no certificate context; HTTPS 443 when certificate context is supplied; HTTP redirect controlled separately. The ALB forwards to healthy containers and handles ingress/rollout routing. ECS scaling decides task count. Do not draw the ALB as a job scheduler or model router.

Place a finding beside scale-out: J01/J02, because current snapshot job ownership is not safe merely because the HTTP service can run three replicas.

## Frame 06: document indexing arrows

`A06 authorize + create job → A07 schedule background function → A08 discover/filter/fetch → A09 parse → A10 buffer → A11 SentenceSplitter 512/50 → P05 embed sub-batches of 200 → A12 upsert batches of 100 → D01`.

Side arrows:

- `D03 ETags → A08 skip unchanged/orphaned versions`.
- `A10 completed sources → D03 checkpoint after writes`.
- `A07 counters/state → D03 job progress`.
- `A06 stream → D03 poll every second → X01 framed SSE`.
- `A07 changed corpus → A15 clustering`, unless explicitly skipped.
- `A09 binary files → temporary disk → cleanup`; temporary disk is not a persistent document archive.

Annotate 5 MiB source-size rejection and five fetches at a time. Add J04 beside the unbounded result queue and J09 beside version replacement.

## Frame 07: transcript indexing arrows

`P02 list videos/durations → P03 captions → A09 timestamped ~60s windows → P05 first embeddings → A11 adjacent similarity + smoothing → A11 merge at .75 threshold / target 6000 tokens → P05 second embeddings → A12 → D01`.

Direct supplied-transcript API joins at the timed-segment processor. Label the first embeddings temporary and the second embeddings persisted. Label stored dimensions 1536 throughout. Put J11 at the semantic merger's source boundary. Caption acquisition has one concurrent video per runner plus three-second delay; optional proxy is a provider-side access path, not a vector store.

## Frame 08: search/chat arrows

`X01 → A14 access check → optional history rewrite → P05 query embedding → A12 cosine search → score floor .4 → largest-gap trim → source round-robin → up to 8 passages`.

Intent classification runs concurrently where possible. Then split:

- Retrieval: `passages → source cards + D04 themes → custom output stream`.
- Synthesis: `passages + history + system instructions → P06 answer stream`; concurrently `passages → P06 key quote`; merge with `D04 themes` into trailing metadata.

Label the default candidate request 48; the SQL minimum pool is 50. Put a sticky showing one query embedding + three generation calls for an automatic first-turn synthesis; a follow-up adds one rewrite call. Label 800 tokens as requested output budget, not context budget.

Draw caller history as request data, not as a server conversation database. Put C05 beside the output: raw marker stream differs from snapshot SSE framing.

## Frame 09: organization arrows

`D01 per-source centroid averages → A16 normalization → UMAP 5D → HDBSCAN candidate sweep → P07 candidate selection → P07 labels/parent grouping → A17 preserve locked topics → P07 semantic summaries + stories → D04 → A12 category metadata stamping → D01`.

Overlay: `P02 playlists → A18 → D04 locks + membership`, then `A18 → D01 category stamping`.

Mark temporary 5D projection separately from stored 1536D vectors. Draw no graph database; stories reference topic IDs inside PostgreSQL. Put J13 at the clear/rebuild/publish boundary.

## Frame 10: data relationships

Draw I04 as one outer database boundary with D01–D05 and M05 inside. Use [DATA_SCHEMA.md](DATA_SCHEMA.md) to expand each table down to columns and indexes.

- Workspace → members: declared foreign key.
- Workspace → source connections/jobs/checkpoints/topics/stories/events: generally logical `workspace_id` relationships.
- Workspace → namespace → vectors: derived string convention.
- Source → vector metadata source ID: logical relation.
- Topic → parent topic: logical relation within workspace.
- Topic → vector `category_id`: logical relation.
- Story → ordered topic IDs: JSON relationship.
- Topic ↔ video/source: `topic_documents` membership; can exist without vectors.
- Search log → attribution event: declared FK, but current tracking write contract needs correction.
- Workspace → marketing datasets/rules/research snapshots: scoped SQL data.

Draw the optional H06 SQLite database outside RDS. Its tables are `interpretation_jobs`, `interpretation_evidence`, and `interpretation_evidence_links`.

## Frame 11: EventBridge, sync trigger, and jobs

```text
EventBridge: the clock
  every 6 hours
    ↓
Sync-trigger Lambda: the HTTP caller
  authenticated POST /consolidation/sync
    ↓
ALB: request routing
    ↓
FastAPI sync route: select Google-connected workspaces
  check existing job / source access / checkpoints
  create job and schedule in-process work
    ↓
Background snapshot: actual ingestion and optional categorization
    ↓
PostgreSQL job status and indexed content
```

The timer does not fetch files; Lambda does not perform embeddings; the ALB does not choose workspaces. The API chooses workspaces and its background function does the processing.

Show the acknowledgement return arrow from the sync route to Lambda before job completion. Label it **scheduled, not completed**. Show a separate consumer progress-read arrow to job rows.

Job state labels: DB `running → done / failed`; phase `clustering` is also carried inside progress/result data. Execution ownership is process-local. Attach J01/J02/J03 for startup cleanup, races, and heartbeat gaps. Scheduled sync currently selects Drive, not YouTube/Notion or marketing imports.

Add a red **J17** sticky to the workspace-selection box: **Intended: users active within 48 hours. Current: all workspace/user records with a stored Google connection; activity filter ignored after migration.** The backend uses each selected pair's connection. Keep the existing-job and connection checks as separate decision diamonds; unchanged-file skipping belongs inside ingestion. Restoring activity-based eligibility is a follow-up, not implemented by this diagram.

## Frame 12: MCP

`X02 → I01 → A19 → access check → tool dispatch → A11/A12/D04 → evidence text → X02 external answer generation`.

Tool cards: retrieve knowledge, list documents, list topics. No edge from hosted MCP to A14 chat. Optional desktop lane: `X02 → A20 stdio wrapper → REST retrieval routes → same knowledge index`.

Put C06 beside tool validation: topic presence is required even if vectors exist.

## Frame 13: marketing

Lane 1: `host-supplied reports → M01 → M02 → M05 observations → scoped reads / deterministic opportunities`.

Lane 2: `target text → M03 → P06 utility model → schema validation → preview response`; separate save arrow `authorized owner → revision check → M05 rules`.

Lane 3: `supplied Keyword Planner report → M04 → M05 research snapshots → filter/list keywords`.

No direct Google Analytics polling or auto-action arrow is wired here. Label business dimensions and embedding dimensions distinctly. Mark all these components LOCAL.

## Frame 14: interpretation engine

`H01 authorized scope + frame → H02 submit → H06 queued job → worker claim → H03 scoped facts/documents/prior interpretations → H04 context → H05 initial explanation/rivals → H03 rival evidence retrieval → H04 expanded context → H05 challenge/synthesis → H02 validation → H06 atomic result/evidence persistence`.

Side arrows: H03 → M02 structured facts; H03 → A11/A12 document evidence; H03 → H06 prior interpretations. These are adapters requiring host configuration, not live API routes.

Put lease details on H06: 300s lease, 20s heartbeat, up to three crash-reclaim attempts, client/workspace idempotency key. Model calls: two, each with 90s engine timeout. Context: 60,000 characters. Host must supply persistent disk or a replacement shared repository; this is not deployed by the existing Fargate stack.

## Frame 15–17: deployment, costs, and decisions

Deployment arrows: `GitHub main/manual trigger → OIDC role → Docker build → ECR SHA image → task-definition revision → ECS rollout → ALB health routing`. Add a separate CDK lane for infrastructure changes and separate SQL lane for migrations; neither runs in the current application workflow.

Cost frame: copy the driver/measurement table from architecture section 17. Leave dollar amounts blank until actual bills and credits are checked. Add a sample calculation: 18,000 vectors × 1536 × 4 bytes ≈ 105.47 MiB of numeric values, excluding text/indexes/DB overhead.

Decision frame: use the questions in FINDINGS.md. Keep PROPOSED platform contracts visually apart from CODE and CONFIG. Logical boundaries can remain within one deployment; the board should not imply a service split has already been chosen.
