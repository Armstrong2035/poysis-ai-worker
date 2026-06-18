# Poysis — Product Requirements & Onboarding Document

**Audience:** Victor (incoming fullstack developer), evaluating the codebase before committing.
**Author's note:** This document was written by reading the codebase directly. Where the
running code disagrees with older docs/comments, the code wins and the discrepancy is
called out explicitly. Treat every "⚠️" as something to verify against the source yourself
on day one — that's the point.

Repo: `poysis-ai-worker` (the FastAPI backend / "AI Worker"). The Next.js frontend and
Supabase project live elsewhere and are referenced where relevant.

---

## 1. Product Overview

**What Poysis is.** Poysis is a knowledge-operations platform. It takes the documents a
person or team has scattered across cloud tools (today: Google Drive), consolidates them
into a single semantic index, organizes that index into a navigable hierarchy of topics and
narrative "stories," and then exposes the whole thing back to the user *through the AI
clients they already use* — Claude, ChatGPT, and (soon) Slack — via the Model Context
Protocol (MCP). The backend in this repo is the "heavy lifting" layer: ingestion,
embedding, clustering, retrieval, and the MCP server.

**The problem it solves.** Knowledge fragmentation. A user's institutional memory is spread
across Drive, Notion, Gmail, and Slack; none of those tools can answer a question *across*
all of them, and none of them can be reasoned over by an AI assistant. Poysis makes the
entire corpus queryable in one place and, critically, makes it queryable *by an LLM in real
time* — so "What did we decide about the Q3 budget?" gets answered from the actual source
documents with citations, instead of from someone's memory or a manual search.

**The three-verb loop (Consolidate → Query → Build).** *Consolidate* is the ingestion
pipeline: pull documents from a source, chunk them, embed them, store the vectors, and
cluster them into topics/stories. *Query* is retrieval: semantic search over the consolidated
vectors, surfaced both as REST endpoints and as MCP tools that Claude/ChatGPT call directly.
*Build* is the forward-looking layer (the "Ouroboros" engine): Poysis analyzes the user's
consolidated knowledge and proactively suggests AI agents/bots they could stand up from it
(onboarding bot, FAQ bot, decision tracker, etc.), pre-wired to the relevant topic clusters.
Consolidate populates the brain, Query reads from it, Build turns it into applications.

---

## 2. Current Stack

| Layer | Technology | Role | Why / Notes |
|---|---|---|---|
| Web framework | **FastAPI** + **Uvicorn** (Procfile/Docker), **Gunicorn** available | All HTTP endpoints, async background jobs, SSE streaming | Async-first; pairs well with the streaming ingestion pipeline. App assembled in [main.py](main.py). |
| Language | **Python 3.11** (Docker base `python:3.11-slim`); README claims 3.10+ | — | Dockerfile pins 3.11. |
| Vector store | **Supabase Postgres + `pgvector`** | Stores embeddings + metadata in a single `vectors` table, queried by cosine distance (`<=>`) | ⚠️ **Not Pinecone.** A `PINE_CONE_API_KEY` exists in `.env` and "Pinecone" appears in stale comments, but no Pinecone code exists. Storage and search are 100% pgvector via raw `psycopg2`. See [vector_store.py](app/primitives/knowledge/vector_store.py). |
| DB access | **`supabase` Python client** (service-role key) for tables; **`psycopg2` + `ThreadedConnectionPool`** for the hot `vectors` path | Two parallel DB access styles | Supabase client used in [database.py](app/primitives/database.py) for app tables. Raw psycopg2 used for vector upsert/query because it needs `::vector` casts and connection pooling against the Supavisor pooler (`:6543`). |
| Embeddings (ingest + query) | **OpenAI `text-embedding-3-small`** via `llama-index-embeddings-openai` | Converts chunks and queries to vectors | ⚠️ **Not Gemini.** See [engine.py](app/primitives/knowledge/engine.py#L24-L28). The query path uses the *same* model ([retrieval router](app/blocks/retrieval/router.py)), which is essential — embedder mismatch silently returns garbage. |
| Embeddings (unused) | **Gemini `gemini-embedding-001`** via `google-generativeai` | — | The [Embedder class](app/primitives/knowledge/embedder.py) exists but is **not** wired into the consolidation/retrieval path. ⚠️ Verify before relying on it. |
| LLM reasoning | **Google Gemini** via `google-generativeai` and `llama-index-llms-google-genai` | Topic categorization, sub-clustering, story detection, semantic summaries, Ouroboros build suggestions, and the legacy `/ask` synthesis | ⚠️ Model IDs are inconsistent across files: categorizer uses `gemini-3.1-flash-lite-preview` ([categorizer.py:10](app/primitives/consolidation/categorizer.py#L10)), Ouroboros uses `gemini-3.5-flash` ([promise_detector.py:176](app/ouroboros/promise_detector.py#L176)), KnowledgeEngine `/ask` uses `gemini-2.0-flash` ([engine.py:174](app/primitives/knowledge/engine.py#L174)). |
| Chunking / ingestion | **LlamaIndex** (`llama-index`, `SentenceSplitter`, `SimpleDirectoryReader`) + **LlamaParse** (`llama-parse`) for high-fidelity PDF | Document → nodes → embeddings | `SentenceSplitter(chunk_size=512, chunk_overlap=50)` is the canonical chunker in the consolidation path. The consolidation processors ([processors/](app/primitives/consolidation/processors/)) do their own paragraph/row chunking before this. |
| Document parsing | **PyMuPDF** (PDF), **pandas** + **openpyxl** (spreadsheets) | Format-specific text extraction | See the [processors](app/primitives/consolidation/processors/). |
| Clustering libs | **BERTopic**, **umap-learn**, **hdbscan**, **scikit-learn**, **numpy** | Present in requirements; a `bertopic_handler` exists | ⚠️ The **live** clustering path is **LLM categorization (Gemini)**, not BERTopic — [ClusteringEngine](app/primitives/consolidation/clustering.py) delegates to [CategorizerEngine](app/primitives/consolidation/categorizer.py). BERTopic appears to be legacy/experimental. |
| Source connector | **Google Drive REST API v3** via `httpx` | Lists + downloads/exports Drive files | [google_drive.py](app/primitives/consolidation/connectors/google_drive.py). OAuth in [google_auth.py](app/primitives/consolidation/google_auth.py). |
| MCP | **`mcp`** package (stdio server) **and** a hand-rolled **JSON-RPC-2.0-over-HTTP** server | Exposes knowledge as Claude/ChatGPT tools | Two implementations — see §3. The HTTP one ([mcp_http.py](app/api/mcp_http.py)) is the production path. |
| Auth (Google) | **Google OAuth 2.0** | Drive access + user email | Tokens stored in `drive_connections` and `consolidation_workspaces`. |
| Email | **Resend** (`RESEND_API_KEY` present, `httpx`) | Planned completion emails | ⚠️ Not yet wired — see [POST_LAUNCH_PUNCHLIST.md](POST_LAUNCH_PUNCHLIST.md) item 1. |
| Deployment | **Railway** (Procfile + `railway.toml`, healthcheck `/ping`); **Docker**/`docker-compose` for local | Production hosting | Prod base URL: `https://poysis-ai-worker-production.up.railway.app`. ⚠️ `docker-compose.yml` is **stale** — it references Qdrant/`fastembed`, neither of which is used anymore. |
| Reranking (unused) | **`cohere`** | — | In requirements; no active use found. |

**Configuration decisions worth knowing:**
- The backend connects to Postgres through **Supavisor transaction-mode pooler** (`:6543`),
  which recycles backends aggressively. The code uses a small `ThreadedConnectionPool` and
  discards broken connections rather than reconnecting per batch — this was a real bug fix
  ([vector_store.py:28-44](app/primitives/knowledge/vector_store.py#L28-L44)).
- The backend uses the **Supabase service-role key**, which **bypasses RLS**. Isolation is
  enforced in application code, not the database (see §3).
- `.env` is **gitignored and not tracked** (verified). Real secrets currently live in the
  local `.env`; there is no committed `.env.example` (a gap — see §8).

---

## 3. Current Architecture

### 3.1 The Consolidation Pipeline (Drive → chunking → embedding → pgvector → clustering)

Entry point: `POST /consolidation/snapshot` ([consolidation.py:154](app/api/consolidation.py#L154)).
It validates workspace ownership, refreshes the Google token, loads the set of
already-indexed files (for dedup — §3.5), creates a job row, and kicks off
`_run_snapshot_job` as a FastAPI background task. Flow:

```
POST /consolidation/snapshot
  └─ _run_snapshot_job  (background, app/api/consolidation.py)
       └─ ConsolidationEngine.run_snapshot         (engine.py)
            └─ SnapshotRunner.stream()             (snapshot.py)
                 ├─ GoogleDriveConnector.list_items   → lists Drive files (paged, mime-filtered)
                 ├─ dedup check (etag vs indexed_files)
                 ├─ fetch + parse  (≤5 docs concurrent via semaphore)
                 │     ├─ document     → DocumentProcessor   (paragraph chunks)
                 │     ├─ spreadsheet  → SpreadsheetProcessor (pandas/openpyxl, row-wise)
                 │     ├─ pdf          → PDFProcessor         (PyMuPDF)
                 │     └─ office_doc   → SimpleDirectoryReader (⚠️ pptx fails here, §5e)
                 │  yields ProcessedChunk objects
            └─ batches of 200 chunks → KnowledgeEngine._run_ingestion_pipeline
                 ├─ SentenceSplitter (512/50)
                 ├─ OpenAI text-embedding-3-small  (sub-batches of 200, 429-retry w/ backoff)
                 └─ VectorService.upsert_vectors → INSERT … ON CONFLICT into `vectors`
       └─ (loop while runner.has_more — pagination past doc_limit)
       └─ ClusteringEngine.run_clustering           (clustering.py)
            └─ CategorizerEngine.run_categorization (categorizer.py, Gemini)
                 ├─ top-level categories (6–8)
                 ├─ sub-clusters for any category > 25 docs
                 ├─ semantic summaries per topic   (parallel Gemini)
                 ├─ story/narrative detection       (parallel Gemini)
                 ├─ save consolidation_topics / consolidation_stories
                 └─ write category_id/label back onto vector metadata
```

Key design properties:
- **Streaming, one-doc-in-memory-at-a-time.** `SnapshotRunner.stream()` is an async
  generator; `ConsolidationEngine` consumes it in batches of `BATCH_SIZE = 200` and
  discards each batch after embedding. Pipeline overlap is free: while batch N embeds,
  workers fetch N+1.
- **Per-batch progress flush.** After each batch, completed `{source_id, etag}` records are
  written to `consolidation_indexed_files` ([engine.py:108](app/primitives/consolidation/engine.py#L108)).
  A crash at 80% keeps the 80% — this is the recovery story (§3.5).
- **Orphaning, not failing.** Files > 5 MB, or that error during parse, are recorded with an
  `ORPHANED:<etag>` marker so they're skipped on re-run and don't count as "indexed"
  ([snapshot.py:114-123](app/primitives/consolidation/snapshot.py#L114-L123)).
- **Clustering needs ≥10 docs** (`MIN_DOCS_TO_CLUSTER`) or it's skipped.
- **Namespace convention:** every vector for a workspace is stored under
  `namespace = f"consolidation_{workspace_id}"`. This string is the join key between
  consolidation, retrieval, and MCP. Memorize it.

### 3.2 The MCP Server(s) and the two tools

⚠️ **There are two MCP implementations. Know which is which.**

**(A) Production: HTTP JSON-RPC server — [app/api/mcp_http.py](app/api/mcp_http.py).**
This is what Claude.ai Remote Connectors / Claude Desktop actually talk to. It speaks
JSON-RPC 2.0 over HTTP (MCP Streamable HTTP transport, `protocolVersion 2025-03-26`) and is
**scoped by URL path**: `POST /mcp/{workspace_id}`. Implements `initialize`, `tools/list`,
`tools/call`, and `notifications/*`. Two tools:

- **`retrieve_from_knowledge_base`** — semantic search. Takes `query`, optional `top_k`
  (default 5), `min_score` (default 0.5). Over-fetches `top_k*2`, filters by score, formats
  results with title/score/source/URL/snippet. Calls `KnowledgeEngine.fetch_raw` against the
  workspace namespace.
- **`list_documents`** — metadata browse. Optional `search` (title substring) and
  `source_type` filters. Returns one row per document with title/URL/snippet (capped at 50).

The MCP URL is generated by `_generate_mcp_url` ([consolidation.py:410](app/api/consolidation.py#L410))
from `MCP_SERVER_URL`, e.g. `https://poysis-ai-worker-production.up.railway.app/mcp/{workspace_id}`,
and is returned by `GET /consolidation/mcp_url/{workspace_id}` and in the SSE "complete" event.

**(B) Legacy: stdio server — [mcp_server.py](mcp_server.py).** Uses the `mcp` package over
stdio for Claude Code/Desktop local config. ⚠️ It is **out of sync**: its tools are named
`query_knowledge_base` / `list_documents` (note the different first name), it expects
`workspace_id` *as a tool argument*, and it calls the older REST endpoints
(`retrieval/query_knowledge_base`, `retrieval/list_documents`) with `POYSIS_API_KEY` as the
`X-User-ID`. Treat this as a reference/legacy artifact, not the production path.

⚠️ **Tool-name drift to be aware of:** README_CONSOLIDATION mentions
`retrieve_from_knowledge_base`; the stdio server uses `query_knowledge_base`; the REST
endpoint is `/retrieval/query_knowledge_base`. The *production HTTP MCP* exposes
`retrieve_from_knowledge_base`. The two live MCP connectors registered in this environment
expose `retrieve_from_knowledge_base` and `list_documents`.

### 3.3 Authentication & Data Isolation Model

There are **two different auth surfaces**, and this is the most important thing to get right:

**REST API (everything except MCP):** caller must send an **`X-User-ID` header**
([security.py:10](app/api/security.py#L10)). `get_user_id` is a FastAPI dependency that 401s
if it's missing. `verify_workspace_ownership` (alias of `verify_workspace_access`) then
checks the `workspace_members` table for that user, falling back to the legacy
`workspaces.user_id` owner column and auto-adding the owner to `workspace_members`. This is
the isolation boundary for `/consolidation/*`, `/retrieval/query_knowledge_base`,
`/sources/*`, `/ouroboros/*`, etc.

**MCP HTTP endpoint:** ⚠️ **No `X-User-ID`, no per-user auth.** `POST /mcp/{workspace_id}`
is gated only by `_validate_workspace`, which checks that the workspace *exists and has
topics*. **Anyone who has the URL can query that workspace's knowledge.** Security today is
"the URL is an unguessable-ish capability token." This is exactly the gap that Priority (a)
— the MCP scoping layer — exists to close.

**RLS:** ⚠️ The README markets "Supabase RLS," but the backend authenticates with the
**service-role key** ([database.py:13](app/primitives/database.py#L13)), which **bypasses
RLS entirely**. Whatever RLS policies exist in Supabase protect *direct client access from
the frontend*, not backend access. **Isolation in this service is application-enforced**
(X-User-ID + `workspace_members`), full stop. Don't assume the database is a backstop.

**Google tokens:** stored per `(user_id, workspace_id, google_account_email)` in
`drive_connections`, and mirrored into `consolidation_workspaces` so the pipeline can read
them. `get_valid_token` refreshes expired access tokens via the refresh token.

### 3.4 Frontend ↔ Backend Connection

The frontend is a separate Next.js app (`CLIENT_URL` defaults to
`http://localhost:3000/workspace`). It talks to this backend over plain HTTP/JSON with the
`X-User-ID` header. Notable surfaces:
- **OAuth redirect dance:** frontend sends the user to `GET /auth/google`, Google calls back
  to `GET /auth/google/callback`, the backend persists tokens and 302-redirects back to
  `CLIENT_URL` with a `drive=connected|error` query param ([auth.py](app/api/auth.py)).
- **Live progress via SSE:** `GET /consolidation/snapshot/stream/{workspace_id}` returns a
  `text/event-stream` that polls the in-memory job state every 500 ms and emits `progress`
  events, then a final `complete` event containing the `mcp_url`
  ([consolidation.py:260](app/api/consolidation.py#L260)).
- **CORS is wide open** (`allow_origins=["*"]`) in [main.py](main.py#L44) — fine for beta,
  flag for production hardening.
- Frontend integration contracts are documented in `MCP_INTEGRATION_FRONTEND.md`,
  `KNOWLEDGE_HIERARCHY_CLIENT.md`, `FRONTEND_CHECKLIST.md`, `CONSOLIDATION_STREAMING.md`.

### 3.5 Job State Persistence & Deduplication

**Job state lives in two places:**
- **In-memory** `_jobs` / `_cluster_jobs` dicts ([consolidation.py:28](app/api/consolidation.py#L28))
  — fast, drives the SSE stream, **lost on redeploy**.
- **Persistent** `consolidation_jobs` table — survives restarts; written via
  `create_job` / `update_job` / `touch_job`.

A job heartbeats by bumping `updated_at` (`touch_job`) during indexing. A "running" job whose
`updated_at` is older than `JOB_STALE_AFTER_SECONDS = 300` is considered **orphaned**:
- On startup, `lifespan` reaps all stale "running" jobs → "failed" ([main.py:23-32](main.py#L23)).
- On a new snapshot request, a stale running row is reaped before the 409 guard, so a crash
  can't block re-triggering forever ([consolidation.py:168-184](app/api/consolidation.py#L168)).

⚠️ Known gap: clustering does **not** heartbeat, so a long clustering phase can look stale
(POST_LAUNCH_PUNCHLIST item 2).

**Deduplication** is **etag-based**, where the etag is the Drive file's `modifiedTime`:
- Before a snapshot, `get_indexed_files(workspace_id)` loads `{source_id: etag}` from
  `consolidation_indexed_files`.
- In `stream()`, a file is **skipped** if its stored etag equals the current `modifiedTime`;
  re-processed if the etag changed (file edited); skipped as **orphaned** if stored as
  `ORPHANED:<etag>` and unchanged ([snapshot.py:108-123](app/primitives/consolidation/snapshot.py#L108)).
- This makes re-running `/snapshot` an **idempotent resume**: indexed files are skipped,
  only new/changed files are processed, then clustering re-runs (idempotent via
  `UNIQUE(workspace_id, topic_id/story_id)` upserts).

---

## 4. What Is Already Built and Working

Based on the code as it stands (not the roadmap):

- ✅ **Google Drive consolidation end-to-end.** OAuth connect/disconnect, token refresh,
  paged file listing with mime filtering, streaming fetch+parse, batched OpenAI embedding,
  pgvector upsert.
- ✅ **Multi-format ingestion:** Google Docs, Google Slides (exported as text), Google
  Sheets (CSV export), `.csv`/`.xls`/`.xlsx` (row-wise), PDF (PyMuPDF, LlamaParse when
  `LLAMA_CLOUD_API_KEY` is set), `.docx` (SimpleDirectoryReader). ⚠️ `.pptx` is *attempted*
  but fails (§5e).
- ✅ **LLM-based hierarchical clustering** (2 levels): 6–8 top categories, sub-clusters past
  25 docs, with semantic summaries, key themes, suggested use cases per topic.
- ✅ **Narrative "stories"** detection across topics, with strength scoring.
- ✅ **Semantic retrieval** over a workspace namespace, with score filtering and score-gap
  detection. Exposed as REST (`/retrieval/query_knowledge_base`, `/search`) and the legacy
  streaming `/ask` (Gemini synthesis).
- ✅ **Production HTTP MCP server**, per-workspace URL, two working tools, validated against
  Claude connectors.
- ✅ **Per-workspace MCP URL generation** + a standalone `GET /consolidation/mcp_url`
  endpoint (so the client can show "Connect to Claude" any time).
- ✅ **Resumable, crash-safe jobs**: per-batch flush, etag dedup/resume, stale-job reaping,
  persistent job rows, 409 concurrency guard.
- ✅ **Real-time SSE progress stream** to the frontend.
- ✅ **Application-level multi-tenant isolation** via `X-User-ID` + `workspace_members`
  (with legacy owner fallback).
- ✅ **Ouroboros build-suggestion engine** (`/ouroboros/build-promises`): Gemini analyzes
  topics+stories and returns pre-configured bot blueprints with confidence scores.
- ✅ **Supporting surfaces:** analytics/tracking, waitlist capture (Resend key present),
  file-upload ingestion (`/ingest-file`), middleware stack (logging, rate limiting, input
  validation, error handling), test suite (`tests/`).

⚠️ **Built but not the live path / not wired:** Gemini `Embedder`, BERTopic handler,
completion emails, `cohere` reranking, `docker-compose` (stale Qdrant config).

---

## 5. Immediate Build Priorities

### (a) MCP Scoping Layer — *priority 1*
- **What:** A real per-notebook scoped MCP endpoint with **server-side permission filtering**.
  Today `POST /mcp/{workspace_id}` is protected only by URL obscurity and a "does this
  workspace have topics" check — no identity, no per-document/per-topic permissions. Build:
  (1) an unguessable scoped token per notebook (not just the raw `workspace_id`), and
  (2) server-side enforcement that a given token may only retrieve the subset of
  topics/documents it's authorized for.
- **Why it matters:** This is the **enterprise team-access model** (a locked decision, §6) —
  teams share knowledge by handing out scoped MCP URLs, not via a separate dashboard. Without
  permission filtering, "share with the team" = "share everything with anyone who gets the
  link." It's also the current biggest security gap (§3.3).
- **Where:** [app/api/mcp_http.py](app/api/mcp_http.py) (`_validate_workspace`, `_dispatch`,
  the route signature), a new token/scope table + lookups in
  [database.py](app/primitives/database.py), and the URL generator
  `_generate_mcp_url` in [consolidation.py](app/api/consolidation.py#L410). Retrieval
  filtering hooks into `KnowledgeEngine.fetch_raw`'s `metadata_filter` (topic_id already
  supported in [vector_store.py:96](app/primitives/knowledge/vector_store.py#L96)).
- **Complexity:** **Large.** Touches the security model, schema, MCP transport, and retrieval
  filtering; needs careful testing of isolation.

### (b) ChatGPT MCP Connector — *priority 2*
- **What:** Verify, harden, and document the existing HTTP MCP server against **ChatGPT's MCP
  connector interface** (ChatGPT now supports remote MCP). Confirm the handshake, tool
  schemas, and response shapes that ChatGPT expects; document the exact setup steps.
- **Why it matters:** Doubles the reach of the core "Query" verb with little new code — same
  server, second large AI client. `mcp_production.md` still says "ChatGPT doesn't natively
  support MCP," which is now outdated and must be corrected.
- **Where:** [app/api/mcp_http.py](app/api/mcp_http.py) for any protocol/shape tweaks; new
  user-facing docs (replace the stale section in [mcp_production.md](mcp_production.md)).
  Likely no schema/DB changes.
- **Complexity:** **Small–Medium** (mostly verification + docs; medium if ChatGPT needs
  protocol-version or tool-shape adjustments).

### (c) Slack Bot — *priority 3*
- **What:** A Slack app/bot that lets users query Poysis knowledge from inside Slack
  (slash command or @-mention → `retrieve_from_knowledge_base` → formatted reply with
  citations).
- **Why it matters:** Meets teams where they already work; strong fit for the "onboarding
  bot / FAQ bot" use cases Ouroboros already suggests.
- **Where:** A **new module** (e.g. `app/api/slack.py` + a router in [main.py](main.py)).
  It would call the same retrieval primitives the MCP server uses
  ([KnowledgeEngine.fetch_raw](app/primitives/knowledge/engine.py#L117)). ⚠️ The prompt
  references "same pattern as the Telegram bot" — **there is no Telegram bot in this repo**
  (verified). Slack is the first chat integration, so there's no internal pattern to copy;
  design the bot↔workspace mapping and auth from scratch (which Slack workspace maps to which
  Poysis workspace; how a Slack user authorizes).
- **Complexity:** **Medium** (Slack OAuth/event handling, signature verification, and the
  workspace-mapping/auth design are the real work; the query call itself is trivial).

### (d) Nango Self-Hosted Evaluation — *priority 4*
- **What:** Assess migrating the Drive connector (and future connectors) to **self-hosted
  Nango**, given 12+ source connectors on the roadmap. Today there's one bespoke connector
  ([google_drive.py](app/primitives/consolidation/connectors/google_drive.py)) behind a clean
  `BaseConnector` interface ([connectors/base.py](app/primitives/consolidation/connectors/base.py)),
  plus hand-rolled OAuth/token-refresh.
- **Why it matters:** Writing and maintaining OAuth + sync for a dozen sources by hand is the
  expensive path. Nango centralizes auth/token-refresh/sync. The decision to go **self-hosted
  (not Nango Cloud)** is already locked (§6) — this task is the *build-vs-adopt* depth check,
  not a vendor bake-off.
- **Where:** Deliverable is a written evaluation (effort to wrap Nango behind the existing
  `BaseConnector`, what `ScopeConfig`/token storage would change, ops cost of self-hosting).
  No production code unless the eval greenlights a spike.
- **Complexity:** **Medium** (investigation + a thin proof-of-concept connector).

### (e) Fix `.pptx` Indexing — *priority 5*
- **What:** Handle `.pptx` gracefully. Drive `.pptx` files are mapped to `content_type =
  "office_doc"` and routed through `SimpleDirectoryReader`
  ([snapshot.py:173-181](app/primitives/consolidation/snapshot.py#L173)), which needs
  `python-pptx` (**not in [requirements.txt](requirements.txt)**) to parse them. They
  currently throw, get caught, and are silently **orphaned** — the user just sees a doc
  "missing" with no explanation. Either add real `.pptx` support (add `python-pptx`) **or**
  detect `.pptx` early and surface a clear "PowerPoint isn't supported yet" message to the
  user instead of a silent orphan.
- **Why it matters:** Silent data loss erodes trust ("I uploaded my deck and it's gone").
  Aligns with Rule 12 / fail-loud.
- **Where:** [snapshot.py](app/primitives/consolidation/snapshot.py) (`_process_item`,
  orphaning path), [google_drive.py](app/primitives/consolidation/connectors/google_drive.py)
  (`OFFICE_DOCS` mapping), `requirements.txt` if adding `python-pptx`, and the error surfacing
  back through `runner.errors` → job result.
- **Complexity:** **Small.**

---

## 6. Architecture Decisions Already Made (Locked — do not re-litigate)

1. **`/ask` namespace resolution = Option B.** The client passes `workspace_id` in the request
   body; the server derives the namespace as `consolidation_{workspace_id}`. No server-side
   "current workspace" inference. (Consistent with how `/retrieval/query_knowledge_base` and
   the MCP tools already resolve namespaces.)
2. **Scoped MCP access IS the enterprise team-access model.** Teams get knowledge by being
   handed scoped MCP connector URLs — **not** a separate web dashboard. This is *why*
   Priority (a) is priority 1.
3. **Nango = self-hosted, not Nango Cloud,** for source connectors. The only open question is
   whether to adopt it at all (Priority d), not which Nango.
4. **Notebook chat runtime = Open WebUI or similar, deferred to Month 4–6.** Don't build a
   bespoke chat UI now.
5. **Session memory is standard; cross-session memory is a paid add-on.** Build session memory
   as the default; cross-session persistence is a monetizable upgrade.

---

## 7. The Broader Roadmap Context

Where the immediate work sits in the larger vision:

- **Three expansion axes.** Growth happens along three independent axes:
  1. **Sources** — more connectors beyond Drive (Notion, Gmail, Slack, …, 12+). This is what
     makes Priority (d)/Nango strategically important. `ScopeConfig` already anticipates
     `gmail` and `recordings` sources.
  2. **File types** — more formats per source (Priority (e)/pptx is the first crack here;
     audio/video transcription, images, etc. follow).
  3. **New ways to use the data** — more *surfaces* on top of the same index: MCP for
     Claude/ChatGPT (Priorities a/b), Slack (c), notebook chat (Month 4–6), the creator
     platform, the Discovery Engine.

- **Google Workspace ecosystem ownership.** Near-term strategy is to go deep on the Google
  Workspace surface (Drive today; Gmail, Docs, Sheets, Slides, Calendar plausibly next) and
  *own* that ecosystem as the beachhead before sprawling across every SaaS tool. The current
  single, well-built Drive connector reflects this depth-first posture.

- **Creator platform (Month 4–6).** Let users package their consolidated knowledge + Ouroboros
  blueprints into shareable/sellable AI apps (this is where the notebook chat runtime and
  payments roadmap converge). Deferred.

- **Hermeneutical engine (Month 3–4) — the technical moat.** Move beyond "retrieve matching
  chunks" toward genuine *interpretation*: understanding meaning, intent, and the relationships
  between documents. The current **stories/narrative detection** and **per-topic semantic
  summaries** ([categorizer.py](app/primitives/consolidation/categorizer.py)) are the seeds of
  this. This is positioned as the hard-to-copy differentiator.

- **Discovery Engine (long-term).** The endgame: surfacing knowledge the user didn't know to
  ask for — gaps, connections, and insights across the corpus — turning Poysis from a
  question-answering tool into a proactive thinking partner. Ouroboros (proactive build
  suggestions) is the first primitive pointing in this direction.

---

## 8. Development Environment Setup

⚠️ **There is no committed `.env.example` and the `docker-compose.yml` is stale (Qdrant,
not used).** The instructions below are reconstructed from the code, `README_CONSOLIDATION.md`,
the Dockerfile, and the live `.env` keys. First onboarding task should arguably be to add a
proper `.env.example` and fix/remove `docker-compose.yml`.

### Prerequisites
- Python **3.11** (matches the Docker image).
- A Supabase project with `pgvector` enabled and the app tables created (see below).
- API keys: OpenAI (embeddings), Gemini (reasoning), Google OAuth client, and optionally
  LlamaCloud (better PDF) and Resend (future emails).

### Steps
```bash
# 1. Clone + enter
cd poysis-ai-worker

# 2. Create a virtualenv (recommended)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install deps
pip install -r requirements.txt

# 4. Create .env in the repo root (see template below)

# 5. Run
python main.py                      # serves on http://localhost:8000
# or, matching production:
uvicorn main:app --host 0.0.0.0 --port 8000

# 6. Smoke test
curl http://localhost:8000/ping     # {"status":"ok"}
curl http://localhost:8000/docs     # FastAPI interactive docs
```

### `.env` template (recreate locally — values are secrets, never commit)
```bash
# Supabase
SUPABASE_PRODUCT_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>     # backend uses this (bypasses RLS)
SUPABASE_PUBLISHABLE_KEY=<anon/publishable-key>
SUPABASE_DIRECT_CONNECTION_STRING=postgresql://postgres.<ref>:<pwd>@<host>.pooler.supabase.com:6543/postgres

# Embeddings + reasoning
OPENAI_API_KEY=sk-...                # REQUIRED — consolidation + retrieval embeddings
GEMINI_API_KEY=AIza...               # REQUIRED — clustering, stories, Ouroboros, /ask
LLAMA_CLOUD_API_KEY=llx-...          # optional — high-fidelity PDF parsing

# Google OAuth (Drive)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback

# App URLs
CLIENT_URL=http://localhost:3000/workspace
MCP_SERVER_URL=http://localhost:8000/mcp          # prod: https://poysis-ai-worker-production.up.railway.app/mcp
WORKER_URL=http://localhost:8000

# Optional / future
RESEND_API_KEY=re_...               # completion emails (not yet wired)
```
⚠️ `PINE_CONE_API_KEY` and the Vertex/GCP keys appear in the current `.env` but are **not used
by the running consolidation/retrieval path** — don't be misled by their presence.

### Database setup
The app tables (`workspaces`, `workspace_members`, `consolidation_workspaces`,
`drive_connections`, `consolidation_indexed_files`, `consolidation_jobs`, `consolidation_topics`,
`consolidation_stories`, `waitlist`, `search_logs`, `attribution_events`) are created via the
SQL files in the repo:
- [migrations_create_consolidation_tables.sql](migrations_create_consolidation_tables.sql)
- [migrations_add_semantic_columns.sql](migrations_add_semantic_columns.sql)
- [analytics_rpc.sql](analytics_rpc.sql)

⚠️ **The `vectors` table itself is not defined in any committed migration** — it was created
directly in Supabase (pgvector). Day-one question: get the exact `vectors` DDL (columns:
`id`, `namespace`, `embedding vector(N)`, `metadata jsonb`; with `UNIQUE(id, namespace)` and a
pgvector index) and its embedding **dimension** (must match `text-embedding-3-small` =
**1536** dims; ⚠️ `README_CONSOLIDATION.md` claims 768, which corresponds to the *unused*
Gemini embedder — verify the live column dimension).

⚠️ `supabase_schema.sql` is **legacy Shopify** (`merchants` table) and is not the current
schema — ignore it.

### Running the full pipeline locally
See `README_CONSOLIDATION.md` for the full curl sequence: `POST /auth/google` → `POST
/consolidation/snapshot` → `GET /consolidation/snapshot/status/{ws}` → (clustering runs
automatically at the end of snapshot, or `POST /consolidation/cluster/{ws}`) → `GET
/consolidation/topics/{ws}` → `POST /retrieval/query_knowledge_base`.

### Tests
```bash
pytest tests/ -v
# Key files: test_end_to_end.py, test_mcp_tools.py, test_job_persistence.py,
#            test_error_handling.py, test_auth_isolation.py
```

---

## 9. Open Questions for the Onboarding Call (for Armstrong)

1. **MCP scoping token design.** For Priority (a), what's the intended permission granularity —
   per-notebook, per-topic, or per-document? And should a scoped MCP URL carry an opaque token
   (revocable, stored in a new table) instead of the raw `workspace_id` in the path? This
   determines the schema and the whole §3.3 security redesign.

2. **The `vectors` table contract.** It's not in any migration. What's the exact DDL and the
   embedding **dimension** (1536 for the live OpenAI model vs. the README's 768)? And is there
   a pgvector index (IVFFlat/HNSW) on it, or are we doing exact scans? This affects retrieval
   latency at scale.

3. **Embedder strategy — OpenAI vs. Gemini.** The live path embeds with OpenAI
   `text-embedding-3-small`, but there's a parallel unused Gemini `Embedder` and the product is
   described as "Gemini embeddings." Is the OpenAI choice deliberate and permanent, or
   mid-migration? (Switching embedders means re-indexing every workspace.)

4. **Slack ↔ workspace mapping & auth.** With no existing Telegram/Slack pattern, how should a
   Slack workspace map to a Poysis workspace, and how does an individual Slack user prove they
   may query it? (Shared team token vs. per-user OAuth.) This is the core design decision for
   Priority (c).

5. **Gemini model pinning.** Three different Gemini model IDs are hardcoded across the codebase
   (`gemini-3.1-flash-lite-preview`, `gemini-3.5-flash`, `gemini-2.0-flash`). Is this
   intentional (cost/quality per task), and should model IDs move to config/env rather than
   being scattered in source? (Touches Rule 5 / parameterization on the roadmap.)
```
