# Declared database schema

Extracted from repository migration declarations on 2026-09-14. This is not the live database schema. Repeated or overlapping table definitions show migration history, not multiple databases. The legacy `supabase_schema.sql` is deliberately excluded from the current baseline. CREATE/ALTER/INDEX declarations are listed without running any SQL. Procedural functions and data backfills require their source files.

## scripts/migrate_db.py

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS workspaces (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  TEXT UNIQUE NOT NULL,
    user_id       TEXT NOT NULL,
    name          TEXT DEFAULT 'My Workspace',
    credits_balance INTEGER DEFAULT 0,
    total_queries   INTEGER DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspace_members (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    role         TEXT DEFAULT 'member',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS profiles (
    id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id  TEXT UNIQUE NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS consolidation_workspaces (
    id                   BIGSERIAL PRIMARY KEY,
    workspace_id         TEXT NOT NULL,
    user_id              TEXT,
    google_access_token  TEXT,
    google_refresh_token TEXT,
    google_token_expiry  TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS consolidation_jobs (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL,
    user_id      TEXT,
    job_type     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    result       JSONB,
    error        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON consolidation_jobs(workspace_id);

CREATE INDEX IF NOT EXISTS idx_jobs_status    ON consolidation_jobs(status);

CREATE TABLE IF NOT EXISTS consolidation_indexed_files (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    etag         TEXT,
    source_type  TEXT DEFAULT 'google_drive',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_indexed_workspace ON consolidation_indexed_files(workspace_id);

CREATE TABLE IF NOT EXISTS consolidation_topics (
    id                 BIGSERIAL PRIMARY KEY,
    workspace_id       TEXT NOT NULL,
    topic_id           INTEGER NOT NULL,
    label              TEXT,
    keywords           JSONB DEFAULT '[]',
    doc_count          INTEGER DEFAULT 0,
    parent_topic_id    INTEGER,
    semantic_summary   TEXT,
    key_themes         JSONB DEFAULT '[]',
    suggested_use_cases JSONB DEFAULT '[]',
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_topics_workspace ON consolidation_topics(workspace_id);

CREATE TABLE IF NOT EXISTS consolidation_stories (
    id             BIGSERIAL PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    story_id       INTEGER NOT NULL,
    title          TEXT,
    description    TEXT,
    topic_sequence JSONB DEFAULT '[]',
    reasoning      TEXT,
    strength       FLOAT DEFAULT 0.5,
    doc_count      INTEGER DEFAULT 0,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, story_id)
);

CREATE TABLE IF NOT EXISTS drive_connections (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id              TEXT NOT NULL,
    workspace_id         TEXT NOT NULL,
    google_account_email TEXT NOT NULL,
    access_token         TEXT,
    refresh_token        TEXT,
    token_expiry         TEXT,
    doc_count            INTEGER DEFAULT 0,
    last_synced_at       TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, workspace_id, google_account_email)
);

CREATE TABLE IF NOT EXISTS nango_connections (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  TEXT NOT NULL,
    user_id       TEXT,
    provider      TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    enabled       BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, provider)
);

CREATE TABLE IF NOT EXISTS youtube_channels (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id         TEXT NOT NULL,
    user_id              TEXT,
    channel_id           TEXT NOT NULL,
    channel_name         TEXT DEFAULT '',
    enabled              BOOLEAN DEFAULT TRUE,
    min_duration_seconds INTEGER,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, channel_id)
);

CREATE TABLE IF NOT EXISTS topic_overrides (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL,
    topic_id     TEXT NOT NULL,
    user_id      TEXT,
    locked       BOOLEAN DEFAULT FALSE,
    custom_label TEXT,
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, topic_id)
);

CREATE TABLE IF NOT EXISTS topic_documents (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL,
    topic_id     INTEGER NOT NULL,
    source_id    TEXT NOT NULL,
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, topic_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_topdocs_workspace ON topic_documents(workspace_id);

CREATE TABLE IF NOT EXISTS search_logs (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL,
    query        TEXT,
    result_count INTEGER,
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_logs_workspace ON search_logs(workspace_id);

CREATE INDEX IF NOT EXISTS idx_search_logs_created   ON search_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS attribution_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  TEXT,
    search_log_id UUID REFERENCES search_logs(id) ON DELETE SET NULL,
    event_type    TEXT,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS topic_query_events (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL,
    user_id      TEXT,
    query        TEXT,
    topic_ids    JSONB DEFAULT '[]',
    themes       JSONB DEFAULT '[]',
    source_ids   JSONB DEFAULT '[]',
    top_score    FLOAT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tqe_workspace ON topic_query_events(workspace_id);

CREATE TABLE IF NOT EXISTS waitlist (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email      TEXT UNIQUE NOT NULL,
    source     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vectors (
    id        TEXT NOT NULL,
    namespace TEXT NOT NULL,
    embedding vector(1536),
    metadata  JSONB DEFAULT '{}',
    PRIMARY KEY (id, namespace)
);

CREATE TABLE IF NOT EXISTS mcp_tokens (
    workspace_id TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
```

## migrations_add_semantic_columns.sql

```sql
ALTER TABLE public.consolidation_topics
ADD COLUMN IF NOT EXISTS semantic_summary TEXT,
ADD COLUMN IF NOT EXISTS key_themes TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN IF NOT EXISTS suggested_use_cases TEXT[] DEFAULT ARRAY[]::TEXT[];

CREATE INDEX IF NOT EXISTS idx_consolidation_topics_semantic_summary
ON public.consolidation_topics (workspace_id, semantic_summary);
```

## migrations_add_topic_documents_metadata.sql

```sql
ALTER TABLE public.topic_documents
ADD COLUMN IF NOT EXISTS metadata jsonb;
```

## migrations_create_consolidation_tables.sql

```sql
CREATE TABLE IF NOT EXISTS public.consolidation_topics (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  topic_id INT NOT NULL,
  label TEXT NOT NULL,
  keywords TEXT[] DEFAULT ARRAY[]::TEXT[],
  doc_count INT DEFAULT 0,
  parent_topic_id INT,
  semantic_summary TEXT,
  key_themes TEXT[] DEFAULT ARRAY[]::TEXT[],
  suggested_use_cases TEXT[] DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_consolidation_topics_workspace_id
ON public.consolidation_topics(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_topics_workspace_topic
ON public.consolidation_topics(workspace_id, topic_id);

CREATE TABLE IF NOT EXISTS public.consolidation_stories (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  story_id INT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  topic_sequence INT[] NOT NULL,
  reasoning TEXT,
  strength NUMERIC(3,2) DEFAULT 0.5,
  doc_count INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, story_id)
);

CREATE INDEX IF NOT EXISTS idx_consolidation_stories_workspace_id
ON public.consolidation_stories(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_stories_strength
ON public.consolidation_stories(workspace_id, strength DESC);

CREATE TABLE IF NOT EXISTS public.consolidation_indexed_files (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  etag TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, source_id)
);

CREATE TABLE IF NOT EXISTS public.consolidation_workspaces (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  user_id TEXT,
  google_access_token TEXT,
  google_refresh_token TEXT,
  google_token_expiry TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.consolidation_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  user_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT DEFAULT 'running',
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_consolidation_jobs_workspace_id
ON public.consolidation_jobs(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_jobs_status
ON public.consolidation_jobs(workspace_id, status);
```

## migrations_create_keyword_planner_reports.sql

```sql
CREATE TABLE IF NOT EXISTS marketing_keyword_planner_reports (
    workspace_id text NOT NULL,
    snapshot_id uuid NOT NULL,
    customer_id text NOT NULL,
    dataset text NOT NULL,
    captured_at timestamptz NOT NULL,
    content jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    fingerprint text NOT NULL,
    imported_by text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, snapshot_id)
);

CREATE INDEX IF NOT EXISTS marketing_keyword_planner_reports_scope
    ON marketing_keyword_planner_reports (workspace_id, customer_id, dataset, captured_at DESC, snapshot_id);

ALTER TABLE marketing_keyword_planner_reports ENABLE ROW LEVEL SECURITY;
```

## migrations_create_marketing_observations.sql

```sql
CREATE TABLE IF NOT EXISTS marketing_observations (
    workspace_id text NOT NULL,
    source text NOT NULL,
    property_id text NOT NULL,
    dataset text NOT NULL,
    day date NOT NULL,
    dimension_key text NOT NULL,
    dimensions jsonb NOT NULL CHECK (jsonb_typeof(dimensions) = 'object'),
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, source, property_id, dataset, day, dimension_key)
);

ALTER TABLE marketing_observations ENABLE ROW LEVEL SECURITY;
```

## migrations_create_marketing_rules.sql

```sql
CREATE TABLE IF NOT EXISTS marketing_rules (
    workspace_id text NOT NULL,
    rule_id uuid NOT NULL,
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, rule_id)
);

ALTER TABLE marketing_rules ENABLE ROW LEVEL SECURITY;
```


## hermeneutics/repository.py (separate, host-owned SQLite database)

Not an RDS migration or a deployed database. The optional service initializes these tables in a persistent local file supplied by its host.

```sql
CREATE TABLE IF NOT EXISTS interpretation_jobs (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    lease_owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT, error_code TEXT,
                    UNIQUE(client_id, workspace_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS interpretation_evidence (
                    interpretation_id TEXT NOT NULL REFERENCES interpretation_jobs(id),
                    object_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(interpretation_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS interpretation_evidence_links (
                    interpretation_id TEXT NOT NULL, claim_path TEXT NOT NULL,
                    object_id TEXT NOT NULL, relationship TEXT NOT NULL,
                    weight REAL NOT NULL, rationale TEXT NOT NULL,
                    FOREIGN KEY(interpretation_id, object_id)
                        REFERENCES interpretation_evidence(interpretation_id, object_id)
                );
                CREATE INDEX IF NOT EXISTS interpretation_queue
                    ON interpretation_jobs(status, lease_until, created_at);
```
