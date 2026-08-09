#!/usr/bin/env python3
"""
Database migration: Supabase → RDS PostgreSQL

This script:
  1. Creates the schema on RDS (pgvector extension + all tables)
  2. Exports data from Supabase via psycopg2
  3. Imports it into RDS

Usage:
    # Set env vars first:
    export SUPABASE_DIRECT_CONNECTION_STRING="postgresql://..."  # source (Supabase)
    export RDS_CONNECTION_STRING="postgresql://..."               # target (RDS)

    python scripts/migrate_db.py --step schema   # create schema only
    python scripts/migrate_db.py --step data     # migrate data only
    python scripts/migrate_db.py --step all      # schema + data (default)
    python scripts/migrate_db.py --table vectors # migrate one table only

IMPORTANT: Run schema step first, then data step.
The vectors table can be very large — migrate it last and consider running
it off-hours. Progress is printed every 10,000 rows.
"""

import argparse
import os
import sys
import time
import psycopg2
import psycopg2.extras

# ── Table migration order (respects FK constraints) ──────────────────────────
# Migrate parent tables before child tables.
TABLES_IN_ORDER = [
    "workspaces",
    "workspace_members",
    "profiles",
    "consolidation_workspaces",
    "consolidation_jobs",
    "consolidation_indexed_files",
    "consolidation_topics",
    "consolidation_stories",
    "drive_connections",
    "nango_connections",
    "youtube_channels",
    "topic_overrides",
    "topic_documents",
    "search_logs",
    "attribution_events",
    "topic_query_events",
    "waitlist",
    "vectors",  # largest — migrate last
]

ARRAY_TO_JSONB_COLUMNS = {
    "consolidation_topics": {"keywords", "key_themes", "suggested_use_cases"},
    "consolidation_stories": {"topic_sequence"},
}


# ── Schema DDL ────────────────────────────────────────────────────────────────
# This recreates the tables that were previously managed by Supabase.
# Adjust column types if your Supabase schema differs.
SCHEMA_SQL = """
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- workspaces
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

-- workspace_members
CREATE TABLE IF NOT EXISTS workspace_members (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    role         TEXT DEFAULT 'member',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);

-- profiles (admin flags etc.)
CREATE TABLE IF NOT EXISTS profiles (
    id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id  TEXT UNIQUE NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- consolidation_workspaces (Google OAuth tokens)
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

-- consolidation_jobs
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

-- consolidation_indexed_files
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

-- consolidation_topics
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

-- consolidation_stories
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

-- drive_connections
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

-- nango_connections
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

-- youtube_channels
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

-- topic_overrides
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

-- topic_documents
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

-- search_logs
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

-- attribution_events
CREATE TABLE IF NOT EXISTS attribution_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  TEXT,
    search_log_id UUID REFERENCES search_logs(id) ON DELETE SET NULL,
    event_type    TEXT,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- topic_query_events
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

-- waitlist
CREATE TABLE IF NOT EXISTS waitlist (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email      TEXT UNIQUE NOT NULL,
    source     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- vectors (pgvector) — the main embedding store
-- Dimension 1536 matches OpenAI text-embedding-3-small.
-- If you migrate embeddings to Titan V2 (1024-dim), change the dimension here
-- and rebuild the index after re-indexing all documents.
CREATE TABLE IF NOT EXISTS vectors (
    id        TEXT NOT NULL,
    namespace TEXT NOT NULL,
    embedding vector(1536),
    metadata  JSONB DEFAULT '{}',
    PRIMARY KEY (id, namespace)
);

-- HNSW index for fast ANN search — build AFTER data is loaded for speed.
-- Run manually after migration: CREATE INDEX ...
-- DO NOT create during migration; it's much faster to build once on the full dataset.
-- Command to run after migration:
--   CREATE INDEX vectors_embedding_hnsw ON vectors
--   USING hnsw (embedding vector_cosine_ops)
--   WITH (m = 16, ef_construction = 64);
"""


def get_conn(dsn: str):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def run_schema(rds_dsn: str):
    print("Creating schema on RDS...")
    conn = get_conn(rds_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        print("Schema created successfully.")
    finally:
        conn.close()


def migrate_table(src_dsn: str, dst_dsn: str, table: str, batch_size: int = 1000):
    if table not in TABLES_IN_ORDER:
        raise ValueError(f"Unsupported table {table!r}; choose one of: {', '.join(TABLES_IN_ORDER)}")
    print(f"\n[{table}] Starting migration...")
    src = get_conn(src_dsn)
    dst = get_conn(dst_dsn)
    total = 0
    t0 = time.time()

    try:
        # Get column names from source
        with src.cursor() as cur:
            cur.execute(f"SELECT * FROM {table} LIMIT 0")
            cols = [desc[0] for desc in cur.description]

        with dst.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (table,),
            )
            dst_cols = {row[0] for row in cur.fetchall()}
        missing_columns = set(cols) - dst_cols
        if missing_columns:
            raise RuntimeError(
                f"Destination table {table} is missing source columns: "
                f"{', '.join(sorted(missing_columns))}"
            )

        col_list = ", ".join(cols)
        # Stream from source in batches using server-side cursor
        with src.cursor(name=f"migrate_{table}", cursor_factory=psycopg2.extras.DictCursor) as src_cur:
            src_cur.itersize = batch_size
            src_cur.execute(f"SELECT {col_list} FROM {table}")

            batch = []
            for row in src_cur:
                values = list(row)
                for index, column in enumerate(cols):
                    if column in ARRAY_TO_JSONB_COLUMNS.get(table, set()) and values[index] is not None:
                        values[index] = json.dumps(values[index])
                batch.append(values)
                if len(batch) >= batch_size:
                    psycopg2.extras.execute_values(
                        dst.cursor(),
                        f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT DO NOTHING",
                        batch,
                    )
                    dst.commit()
                    total += len(batch)
                    batch = []
                    if total % 10000 == 0:
                        print(f"  [{table}] {total:,} rows... ({time.time()-t0:.0f}s)")

            # Flush remaining
            if batch:
                psycopg2.extras.execute_values(
                    dst.cursor(),
                    f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT DO NOTHING",
                    batch,
                )
                dst.commit()
                total += len(batch)

        with src.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            source_count = cur.fetchone()[0]
        with dst.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            destination_count = cur.fetchone()[0]
        if destination_count != source_count:
            raise RuntimeError(
                f"Row-count mismatch after {table}: source={source_count}, destination={destination_count}"
            )

        elapsed = time.time() - t0
        print(f"  [{table}] Done — {total:,} rows in {elapsed:.1f}s")

    except Exception as e:
        dst.rollback()
        print(f"  [{table}] ERROR: {e}")
        raise
    finally:
        src.close()
        dst.close()

    return total


def main():
    parser = argparse.ArgumentParser(description="Migrate Supabase → RDS")
    parser.add_argument("--step", choices=["schema", "data", "all"], default="all")
    parser.add_argument("--table", help="Migrate a single table only")
    args = parser.parse_args()

    src_dsn = os.getenv("SUPABASE_DIRECT_CONNECTION_STRING")
    dst_dsn = os.getenv("RDS_CONNECTION_STRING")

    if not src_dsn:
        print("ERROR: SUPABASE_DIRECT_CONNECTION_STRING not set")
        sys.exit(1)
    if not dst_dsn:
        print("ERROR: RDS_CONNECTION_STRING not set")
        sys.exit(1)

    if args.step in ("schema", "all"):
        run_schema(dst_dsn)

    if args.step in ("data", "all"):
        tables = [args.table] if args.table else TABLES_IN_ORDER
        grand_total = 0
        t0 = time.time()
        for table in tables:
            try:
                grand_total += migrate_table(src_dsn, dst_dsn, table)
            except Exception as e:
                print(f"\nMigration failed on table '{table}': {e}")
                print("Fix the error and re-run with --table to resume from this table.")
                sys.exit(1)

        print(f"\nMigration complete — {grand_total:,} total rows in {time.time()-t0:.1f}s")
        print("\nNext step: build the HNSW index on the vectors table:")
        print("  psql $RDS_CONNECTION_STRING -c \"CREATE INDEX vectors_embedding_hnsw ON vectors")
        print("    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);\"")


if __name__ == "__main__":
    main()
