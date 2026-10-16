-- Frontend-owned tables, moved from the deleted Supabase project into RDS.
-- Apply idempotently: every statement is safe to re-run.
--
-- The frontend previously read/wrote these directly through Supabase (RLS).
-- After this migration the Next.js apps reach them through the worker API
-- (app/api/client.py), which verifies the Cognito access token and scopes
-- every query to the caller's own user_id. There is no RLS; the worker is the
-- only writer and it does its own authorization.

-- ── notebooks ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notebooks (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      TEXT NOT NULL,
    workspace_id TEXT,
    name         TEXT NOT NULL DEFAULT 'Untitled Notebook',
    slug         TEXT,
    config       JSONB NOT NULL DEFAULT '{}',
    verified     BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS notebooks_slug_unique
    ON notebooks (slug) WHERE slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS notebooks_user_idx ON notebooks (user_id);
CREATE INDEX IF NOT EXISTS notebooks_verified_idx
    ON notebooks (verified) WHERE verified = true;

-- ── cluster_ceilings ─────────────────────────────────────────────────────────
-- How far a topic's contents may reach: private / team / public. A missing row
-- means "private" (the safe default), not an error.
CREATE TABLE IF NOT EXISTS cluster_ceilings (
    topic_id      TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    ceiling       TEXT NOT NULL DEFAULT 'private'
                  CHECK (ceiling IN ('private', 'team', 'public')),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (topic_id, owner_user_id)
);

CREATE INDEX IF NOT EXISTS cluster_ceilings_owner_ceiling_idx
    ON cluster_ceilings (owner_user_id, ceiling);

-- ── profiles: add UI mode + enterprise entitlement ───────────────────────────
-- These previously lived in Supabase auth user_metadata / app_metadata. Cognito
-- has no equivalent that a user cannot self-set, so they live in RDS where only
-- the worker writes them.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS ui_mode TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS enterprise BOOLEAN NOT NULL DEFAULT false;
