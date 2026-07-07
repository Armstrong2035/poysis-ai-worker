# Poysis AI Worker

A Python/FastAPI backend that consolidates a user's scattered documents (Google Drive, Notion, YouTube, ...) into a searchable, topic-clustered knowledge base, and exposes that knowledge base to Claude/ChatGPT via MCP (Model Context Protocol). It's the backend for a Next.js frontend (separate repo) and is deployed as a standalone microservice.

The codebase is mid-migration from an earlier single-tenant Shopify app (`product-scout`) into a generalized, multi-tenant "AI worker" — see `ROADMAP.md` and `poysis_abstraction_analysis.md` for that history. Expect some inconsistency and dead code from that transition (called out below).

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server (reads .env via python-dotenv)
python main.py                    # http://localhost:8000, auto-reload off
uvicorn main:app --reload         # dev with auto-reload

# Interactive API docs (FastAPI auto-generated)
# http://localhost:8000/docs

# Run tests — these are INTEGRATION tests that hit a live server, not unit tests.
# Start `python main.py` in one terminal first.
pytest tests/ -v
pytest tests/test_auth_isolation.py -v
pytest tests/test_auth_isolation.py::test_missing_user_id_header -v

# Point tests at a non-default backend (e.g. staging/Railway)
WORKER_URL=https://your-worker.railway.app pytest tests/

# Docker
docker build -t poysis-worker .
docker compose up            # NOTE: docker-compose.yml also starts a qdrant
                              # container — that's leftover from the Shopify
                              # predecessor and unused by current code.
```

There is no configured linter/formatter or CI workflow in this repo (`.github/workflows/` is empty).

## Architecture

### Layering

`main.py` wires together three layers of routers, in increasing order of business-policy:

- **`app/primitives/`** — raw capabilities with no product opinions: `database.py` (Supabase client + all table access, one `DatabaseService` class), `knowledge/` (embedding + pgvector storage + RAG), `consolidation/` (source discovery, parsing, clustering), `nango/` (OAuth-as-a-service client).
- **`app/blocks/`** — the "Golden Quad": four standalone, domain-agnostic AI building blocks (`retrieval`, `classifier`, `clustering`, `recommendation`), each a thin policy layer (score thresholds, result shaping) over the `KnowledgeEngine` primitive. Mounted at `/retrieval`, `/classify`, `/cluster`, `/recommend`.
- **`app/api/`** + **`app/ouroboros/`** — product-facing endpoints: `auth` (Google OAuth + Nango OAuth), `consolidation` (the main pipeline — snapshot/cluster jobs, SSE progress), `chat` (workspace-scoped streaming RAG chat), `mcp_http` (the live MCP server Claude.ai connects to), `sources`, `security` (auth dependencies), `analytics`/`tracking`, `waitlist`, `ouroboros/promise_detector` (suggests bots to build from the knowledge base).

**Known stale code:** `app/blocks/classifier`, `clustering`, and `recommendation` routers call `engine.fetch_raw(workspace_id=..., ...)` and `engine.upsert_documents(...)`, but `KnowledgeEngine`'s actual methods (`app/primitives/knowledge/engine.py`) take `notebook_id`, not `workspace_id`, and don't accept arbitrary kwargs — these three blocks predate a rename and will raise `TypeError` if called. Only `app/blocks/retrieval/router.py` is confirmed wired correctly against the current `KnowledgeEngine` API.

### The consolidation pipeline (the core product flow)

```
Sources (Google Drive / Nango providers / YouTube)
  → Connectors (BaseConnector: list_items / fetch_text / fetch_file → RawSourceItem)
  → Processors (BaseProcessor: process → ProcessedChunk[])
  → SnapshotRunner.stream()   — streams chunks doc-by-doc, never buffers a whole corpus
  → ConsolidationEngine.run_snapshot() — batches chunks (BATCH_SIZE=200), embeds, upserts to pgvector
  → ClusteringEngine → CategorizerEngine — Gemini-driven topic hierarchy + narrative "stories"
  → consolidation_topics / consolidation_stories tables
  → MCP server (app/api/mcp_http.py) — Claude queries this over JSON-RPC
```

Key points a new engineer needs to know before touching this:

- **Connectors** (`app/primitives/consolidation/connectors/`): Google Drive is first-class (`google_drive.py`, uses Google OAuth tokens). Everything else (Notion, Slack, GitHub, ...) goes through `NangoConnector` (`nango_base.py`) — Nango handles OAuth so new sources don't need custom auth code. YouTube (`youtube.py`) needs no OAuth at all — it's scraped by public channel ID.
- **Processors** (`processors/`): `DocumentProcessor`, `SpreadsheetProcessor`, `PDFProcessor` chunk with LlamaIndex's `SentenceSplitter`. `TranscriptProcessor` is different — it pre-chunks by ~60s of transcript, then `KnowledgeEngine.embed_and_store` does a second pass that merges adjacent pre-chunks into topic-coherent segments using cosine-similarity valleys (`_find_topic_groups`), preserving timestamp boundaries for deep-linking into the video.
- **Streaming/concurrency**: `SnapshotRunner.stream()` fetches up to `FETCH_CONCURRENCY=5` documents in parallel (YouTube is capped at 1 — it rate-limits scraping, with a 3s sleep between fetches). `ConsolidationEngine` overlaps embedding of one batch with fetching of the next.
- **Incremental sync**: every indexed file's `etag` (Drive's `modifiedTime`) is stored in `consolidation_indexed_files`; re-running a snapshot skips unchanged files. Oversized files (`SIZE_WARNING_BYTES = 5MB`) and failed fetches are marked `ORPHANED:<etag>` rather than retried forever.
- **Namespacing in the `vectors` table**: `consolidation_{workspace_id}` for regular documents, `youtube_{workspace_id}` for transcripts (kept separate because they use different chunking). `KnowledgeEngine.fetch_raw` / `VectorService.query_vectors` take this namespace directly.
- **Job tracking**: consolidation/clustering jobs live in two places — an in-memory `_jobs`/`_cluster_jobs` dict in `app/api/consolidation.py` (fast status reads, but resets on redeploy) and the `consolidation_jobs` table (source of truth across restarts). A `touch_job` heartbeat during long-running snapshots lets stale/orphaned "running" jobs (crashed worker) be detected and reaped — both at startup (`main.py` `lifespan`) and lazily whenever a new snapshot/cluster job is requested (`JOB_STALE_AFTER_SECONDS = 300`).

### Knowledge Engine / embeddings — two different models are in play

`KnowledgeEngine` (`app/primitives/knowledge/engine.py`) is the actual embedding + retrieval + RAG-synthesis engine used by consolidation, chat, and retrieval. It embeds with **`OpenAIEmbedding` (`text-embedding-3-small`)**, not the `Embedder` class. It answers/streams with **Gemini 2.0 Flash** (`llm.astream_complete`).

The separate `Embedder` class (`app/primitives/knowledge/embedder.py`) wraps **Gemini's `embed_content`** and is only used by the `classify` block for label/text similarity — its vectors are not comparable to (and not stored alongside) the OpenAI vectors `KnowledgeEngine` produces. Don't assume "embedding" means the same model everywhere in this codebase.

Topic clustering/categorization (`app/primitives/consolidation/categorizer.py`) uses a third model, `gemini-3.1-flash-lite-preview`, to name and hierarchy-ize clusters and to detect cross-topic narrative "stories".

`BEDROCK_MIGRATION.md` documents a **planned but not-yet-executed** migration to AWS Bedrock (Titan embeddings + Claude Haiku) and references a Pinecone index (`poysis-gemini`) — that doesn't match current reality: storage today is Supabase pgvector accessed directly via `psycopg2` (`VectorService`), not Pinecone. Treat that doc as a future plan, not the current architecture.

### Storage

Supabase (Postgres + `pgvector`) is the only datastore. `VectorService` (`app/primitives/knowledge/vector_store.py`) talks to it directly via `psycopg2` against `SUPABASE_DIRECT_CONNECTION_STRING` (the Supavisor transaction-mode pooler), not through the `supabase` client — vector similarity search (`<=>` operator) needs raw SQL. `DatabaseService` (`app/primitives/database.py`) uses the `supabase` client for everything else (workspaces, jobs, connections, topics, stories, analytics).

Tables referenced in code (schema itself is managed in Supabase directly — `supabase_schema.sql` in the repo root is legacy from the Shopify app and out of date): `workspaces`, `workspace_members`, `consolidation_workspaces` (Google token storage), `consolidation_jobs`, `consolidation_indexed_files`, `consolidation_topics`, `consolidation_stories`, `drive_connections`, `nango_connections`, `youtube_channels`, `search_logs`, `attribution_events`, `waitlist`, and the `vectors` table (pgvector, keyed by `namespace`).

### Multi-tenancy & auth

`workspace_id` is the tenant boundary. Every workspace-scoped endpoint depends on `get_user_id` (reads the `X-User-ID` header — there's no real session/JWT auth) and `verify_workspace_ownership` (`app/api/security.py`), which checks the `workspace_members` table and falls back to `workspaces.user_id` for legacy single-owner rows (auto-migrating them into `workspace_members` on first check).

### MCP — two separate implementations, don't confuse them

- **`app/api/mcp_http.py`** — mounted in `main.py` at `/mcp/{workspace_id}`. This is the live, production Streamable-HTTP JSON-RPC server that Claude.ai's Cloud Connectors / Claude Desktop / ChatGPT connect to directly over HTTP. Tools: `retrieve_from_knowledge_base`, `list_documents`, `list_topics`.
- **`mcp_server.py`** (repo root) — a separate, standalone **stdio** MCP server (see `MCP_SETUP.md`) meant to run locally as a subprocess under Claude Code/Desktop's MCP config. It doesn't talk to the DB directly — it's an HTTP client that calls back into this same worker's REST endpoints (`retrieval/query_knowledge_base`, `retrieval/list_documents`) using a `POYSIS_API_KEY` as a stand-in user ID. Different tool names (`query_knowledge_base` vs `retrieve_from_knowledge_base`) than the HTTP MCP server above.

### Middleware order

`main.py` registers middleware bottom-up (inner middleware runs first): `ErrorHandlingMiddleware` → `LoggingMiddleware` → `RateLimitMiddleware` (100 req/min per `X-User-ID`, in-memory) → `InputValidationMiddleware` (10MB body cap, basic `workspace_id` sanity check) → CORS (`app/middleware.py`).

### Deployment

Railway is the primary target (`railway.toml` health-checks `/ping`, `Procfile` runs `uvicorn main:app`). `Dockerfile` is also maintained. `docker-compose.yml` additionally spins up a `qdrant` container — that's a leftover from the pre-migration Shopify app and is not used by any current code path (all vector storage is Supabase pgvector).

## Environment Variables

Grouped by subsystem (see `app/primitives/database.py`, `knowledge/`, `consolidation/` for exact usage):

- **Supabase**: `SUPABASE_PRODUCT_URL`, `SUPABASE_SERVICE_ROLE_KEY` (client access, RLS-bypassing service role), `SUPABASE_DIRECT_CONNECTION_STRING` (raw psycopg2 pool for vector queries)
- **LLM / embeddings**: `OPENAI_API_KEY` (embeddings used by `KnowledgeEngine`), `GEMINI_API_KEY` (RAG synthesis, classify-block embeddings, categorization, ouroboros), `LLAMA_CLOUD_API_KEY` (optional, high-fidelity PDF parsing via LlamaParse)
- **Google OAuth (Drive)**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
- **Nango (OAuth-as-a-service for non-Google sources)**: `NANGO_BASE_URL`, `NANGO_SECRET_KEY`, `WORKER_BASE_URL`
- **MCP / sync**: `MCP_SERVER_URL` (public base URL used to build per-workspace connector links), `CONSOLIDATION_SYNC_KEY` (bearer secret guarding the `/consolidation/sync` cron endpoint)
- **Client redirect**: `CLIENT_URL` (where OAuth callbacks redirect back to after success/failure)
- **Testing**: `WORKER_URL` (base URL the integration test suite targets, default `http://localhost:8000`)
- **Standalone MCP server** (`mcp_server.py` only): `POYSIS_API_KEY`, `WORKER_URL`

No `.env.example` exists in the repo — cross-reference this list and `app/primitives/consolidation/google_auth.py` / `app/primitives/nango/client.py` when setting up a local `.env`.

## Further Reading

- `README_CONSOLIDATION.md` — end-to-end walkthrough of the consolidation pipeline with example `curl` requests
- `MCP_SETUP.md` — configuring the standalone stdio MCP server in Claude Code/Desktop
- `ROADMAP.md` / `poysis_abstraction_analysis.md` — history of the migration from the Shopify predecessor
- `BEDROCK_MIGRATION.md` — planned (not yet executed) migration off Gemini/OpenAI to AWS Bedrock
