# Architecture findings and unresolved questions

Static review of the current working tree, 14 September 2026. These findings explain how to draw the existing system and what needs a decision before it becomes a public API platform. They are not results of a penetration test, load test, production database inspection, or live deployment check. No implementation changes were made for this review.

## Corrections to historical descriptions

| Topic | What the current source shows |
|---|---|
| Deployment | CDK defines ECS Fargate, ALB, and RDS. Railway assets remain, but do not prove current Railway usage. |
| Datastore | Current application/vector persistence uses psycopg2 against PostgreSQL. Supabase connection-string fallback remains. |
| Namespace | Both standard documents and YouTube transcripts use `consolidation_{workspace_id}`. The old separate-YouTube-namespace description is stale. |
| Embeddings | Main document/query vectors are 1,536-dimensional OpenAI embeddings. Titan's 1,024-dimensional classifier vectors are separate and temporary. |
| Clustering | UMAP + HDBSCAN over document centroids; not a BERTopic call chain. UMAP's five dimensions do not replace stored vectors. |
| Document memory | Fetch concurrency is bounded, but parsed-result queues and scheduled task lists are not. The old one-document-in-memory description is inaccurate. |
| Snapshot SSE | Reads job rows from PostgreSQL, rather than only an in-memory dictionary. Other status paths still use local dictionaries first. |
| RDS backups | Executable retention is one day; header text says seven. |
| CI/CD | A deploy workflow exists; the older statement that `.github/workflows` is empty is stale. |
| Classifier | The current classify route calls the separate Embedder correctly; the old README lumps it together with other incompatible blocks. |
| Optional modules | Marketing and hermeneutics exist locally; hermeneutics is not mounted. Their existence is not proof of production deployment. |

## Access and API boundary findings

| ID | Source evidence | Practical implication |
|---|---|---|
| A01 | [security.py](../../app/api/security.py): header fallback defaults on; JWT support is configurable. [app_stack.py](../../infra/poysis_infra/app_stack.py) does not declare `AUTH_*` settings. | Runtime identity mode needs verification. Do not draw JWT as a universally enforced ingress gate. |
| A02 | [consolidation.py](../../app/api/consolidation.py): topic/story/knowledge GET handlers lack identity dependencies; cluster-status catches and ignores access errors. | Different knowledge endpoints expose different boundaries. |
| A03 | [analytics.py](../../app/api/analytics.py), [tracking.py](../../app/api/tracking.py), [classifier](../../app/blocks/classifier/router.py), [legacy clustering](../../app/blocks/clustering/router.py), [recommendations](../../app/blocks/recommendation/router.py): no identity dependency on these routes. | A public API contract cannot assume all operations are workspace-authorized merely because they accept a workspace ID. |
| A04 | [promise_detector.py](../../app/ouroboros/promise_detector.py): an identity dependency exists but workspace verification failures are caught and ignored. | Identity and resource authorization are separate concerns here. |
| A05 | [chat.py](../../app/api/chat.py): requested connection/topic selections are used directly after workspace access validation. | Define whether callers have full workspace access or need server-resolved content grants for shared bots. |
| A06 | [middleware.py](../../app/middleware.py): rate buckets use the unverified header or `anonymous`, independently of bearer identity, and are process-local. | Rate limits are not reliable per-platform-client quotas. Requests with only bearer authentication share the anonymous bucket. |
| A07 | [retrieval router](../../app/blocks/retrieval/router.py): new raw namespace paths enforce the `consolidation_` convention. Older block paths do not use the same guard. | Namespace authorization is not centralized throughout the codebase. |
| A08 | [main.py](../../main.py), [API inventory](API_INVENTORY.md): no version prefix; mixed JSON/form/query contracts. | Capture the current wire contracts before designing a stable versioned API. |

## Execution and data lifecycle findings

| ID | Source evidence | Practical implication |
|---|---|---|
| J01 | [main.py](../../main.py): startup reaps all running jobs older than the current instant. | Starting another task can mark a live task's work failed. Autoscaling is configured beyond the current job ownership design. |
| J02 | [consolidation.py](../../app/api/consolidation.py): read/check/create job operations are separate, with local dictionaries. | No atomic distributed lock prevents simultaneous submissions for one workspace. |
| J03 | [engine.py](../../app/primitives/consolidation/engine.py): progress heartbeat depends on document activity; model calls and clustering have long phases. | A healthy slow run can look stale after 300 seconds. |
| J04 | [snapshot.py](../../app/primitives/consolidation/snapshot.py): unbounded `asyncio.Queue` and one task object per enumerated item. | Memory can grow with corpus size/backpressure despite a fetch semaphore. |
| J05 | [embedding_cluster.py](../../app/primitives/consolidation/embedding_cluster.py): synchronous UMAP/HDBSCAN sweep inside `pick_best_partition`; [YouTube connector](../../app/primitives/consolidation/connectors/youtube.py): library caption call is synchronous inside async code. | These phases can delay other requests and heartbeats in the shared application worker. |
| J06 | [seeding.py](../../app/primitives/consolidation/seeding.py), [sources.py](../../app/api/sources.py): admin seeding schedules a separate helper without creating the standard job row. | The comment promising ordinary snapshot-status visibility is not backed by that helper's implementation. |
| J07 | [Drive connector](../../app/primitives/consolidation/connectors/google_drive.py): limit applies to listed files before indexed-file skipping; partial-run loop starts listing again. | Corpus coverage beyond the first limit needs a real cursor/test. No durable pagination cursor is stored. |
| J08 | [snapshot.py](../../app/primitives/consolidation/snapshot.py): Drive branch returns from the whole stream if no tasks are created. | A combined Drive + YouTube request can skip the later YouTube branch when Drive has nothing to fetch. |
| J09 | [knowledge engine](../../app/primitives/knowledge/engine.py): document chunk IDs come from splitter nodes; transcript IDs use source ID + timestamp; writes upsert individual rows. | No complete source-version replacement/removal step is implemented. Changed content can leave old chunks behind. |
| J10 | [sources.py](../../app/api/sources.py): disconnect operations remove connection metadata, without a vector purge path. | Disconnection is not proof that indexed content was deleted. |
| J11 | [knowledge engine](../../app/primitives/knowledge/engine.py): transcript grouping merges adjacent chunks without an explicit source-ID break. | A batch containing two videos can merge across a source boundary if similarities permit it. |
| J12 | [consolidation engine](../../app/primitives/consolidation/engine.py): completed-file checkpoints are flushed after an individual buffer flush; document and transcript buffers coexist. | Mixed-source checkpoint correctness needs validation: a checkpoint should represent committed vectors for that source, not just processing completion. |
| J13 | [categorizer](../../app/primitives/consolidation/categorizer.py): topics/stories are cleared, model work runs, rows saved, then vector categories updated in separate operations. | Rebuilds expose an intermediate state and can fail after clearing old structure. |
| J14 | [categorizer](../../app/primitives/consolidation/categorizer.py): IDs are reused by current label and allocated from current maximum. | Label changes can change topic identity; there is no separate durable high-water mark guaranteeing historical IDs are never reused after deletion. |
| J15 | [snapshot.py](../../app/primitives/consolidation/snapshot.py): Drive/YouTube skip matching orphan markers; YouTube uses publication time as ETag. | Later availability of captions or recovery from a transient failure may not trigger automatic retry of that version. |
| J16 | [consolidation.py](../../app/api/consolidation.py), [categorizer](../../app/primitives/consolidation/categorizer.py): snapshot aggregates `leaf_topics`/`hierarchy_depth`, while categorizer returns different summary keys. | Some returned counters can be zero despite successful category creation. |
| J17 | [sync endpoint](../../app/api/consolidation.py) passes `active_within_hours=48`; [database selector](../../app/primitives/database.py) ignores it and selects every workspace/user record with a non-null stored Google access token. Its comment attributes the missing last-login filter to the Supabase-to-Postgres migration. | Intended policy: recently active users only. Current selection can include inactive users. Existing-job, connection, and unchanged-file checks still apply. Restore the filter after defining an authoritative activity timestamp; documentation only, not fixed. |

## Compatibility and wiring findings

| ID | Source evidence | Practical implication |
|---|---|---|
| C01 | [recommendation](../../app/blocks/recommendation/router.py), [legacy clustering](../../app/blocks/clustering/router.py) pass `workspace_id=` to `KnowledgeEngine.fetch_raw`, whose parameter is `notebook_id`. | These execution paths raise an unexpected-keyword error. Legacy clustering may already have ingested into a raw namespace before reaching the failing call. |
| C02 | [query_knowledge_base](../../app/blocks/retrieval/router.py) reads `metadata['_text']`; [fetch_raw](../../app/primitives/knowledge/engine.py) extracts that field to top-level `text` and removes it from metadata. | That REST response can have empty passage text even when search matches exist. The local MCP wrapper depends on this route. |
| C03 | [ingest_file](../../app/primitives/knowledge/engine.py) catches parsing/ingestion exceptions and returns zero. | Upload can report a completed response with zero vectors after internal failure. |
| C04 | [ScopeConfig](../../app/primitives/consolidation/scope.py) has `nango_sources`; [SnapshotRequest](../../app/api/consolidation.py) does not populate it. | Notion implementation is not an exposed snapshot capability yet. |
| C05 | [chat.py](../../app/api/chat.py) emits raw text/markers under event-stream content type; [snapshot stream](../../app/api/consolidation.py) emits framed SSE. | One client streaming parser does not automatically work for both. |
| C06 | [MCP](../../app/api/mcp_http.py) requires topics before tool calls. | Index-only or small workspaces skipped by clustering can have searchable vectors but fail MCP validation. |
| C07 | [app_stack.py](../../infra/poysis_infra/app_stack.py) and [chat.py](../../app/api/chat.py) contain different literal spellings of the default Bedrock chat model. | Record the exact configured string and verify actual endpoint/model behavior before labeling it production-supported. |
| C08 | [tracking.py](../../app/api/tracking.py) writes `search_id`, `product_id`, and event-specific top-level fields; [migration baseline](../../scripts/migrate_db.py) declares `search_log_id` and JSONB metadata. | Dynamic inserts against the baseline can fail; the DB method logs and swallows the error. |
| C09 | [VectorService](../../app/primitives/knowledge/vector_store.py) constructs a pool per instance; several routes instantiate it/KnowledgeEngine repeatedly. | The shared-engine change did not establish one universal vector pool. |
| C10 | [main.py](../../main.py) loads dotenv with override after router imports; some modules also read environment at import. | Configuration resolution can depend on import order. Deployment should be checked against actual start-time settings. |
| C11 | [consolidation `_to_documents`](../../app/primitives/consolidation/engine.py) copies common fields and selected page metadata. | Generic processor `extra_metadata`, including spreadsheet columns, is not universally preserved through document ingestion. |

## What is not established by this repository review

- Deployed commit and whether local marketing/interpretation modules are included.
- Live task count, task sizing, TLS listener/certificate/redirect, DNS resolution, or AWS deployment drift.
- Actual embedding dimension of existing rows, row counts, vector sizes, HNSW presence/settings, pgvector version, or schema migration history.
- Successful model calls for the configured identifiers and endpoint paths.
- Actual document/chunk distributions, token counts, parsing coverage, indexing completeness, or query quality.
- Current provider bills, credits, free-tier status, storage occupancy, or operational performance.
- Actual API-consumer authorization model, content grants, billing contracts, SLOs, or retention/deletion requirements.

## Questions for the API-platform design session

These are decisions to put on FigJam, not blockers to the current documentation.

| Decision | Why it changes the architecture |
|---|---|
| What is an API customer: developer, organization, workspace owner, or app? | Establishes a stable client/tenant boundary and usage accounting. |
| Which capabilities are public products versus internal helpers? | Determines which of the 60 routes need stable contracts. |
| What is the unit of knowledge: workspace, collection, connection, document version? | Controls scoping, re-indexing, deletion, permissions, and billing. |
| Must jobs survive deploys and scale across tasks? | Determines queue/repository/lease design and worker separation. |
| Are search and answer generation independently billable? | Determines retrieval/model API boundaries and per-operation metering. |
| What is a versioned taxonomy and when is it published? | Determines atomic category updates and stable topic identifiers. |
| Does the interpretation engine become a public endpoint? | Requires a trusted host boundary, shared persistence, worker lifecycle, and explicit evidence grants. |
| Which external data feeds are actually supported? | Distinguishes source connectors from report parsers and future placeholders. |
| What retention, deletion, and provenance guarantees are promised? | Requires lifecycle behavior beyond disconnecting sources and upserting chunks. |
