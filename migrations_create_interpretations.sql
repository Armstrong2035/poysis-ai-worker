CREATE TABLE IF NOT EXISTS interpretation_jobs (
    id text PRIMARY KEY,
    client_id text NOT NULL,
    workspace_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    request_json text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    lease_owner text,
    lease_until double precision,
    attempts integer NOT NULL DEFAULT 0,
    result_json text,
    error_code text,
    UNIQUE(client_id, workspace_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS interpretation_evidence (
    interpretation_id text NOT NULL REFERENCES interpretation_jobs(id),
    object_id text NOT NULL,
    snapshot_json text NOT NULL,
    PRIMARY KEY(interpretation_id, object_id)
);
CREATE TABLE IF NOT EXISTS interpretation_evidence_links (
    interpretation_id text NOT NULL,
    claim_path text NOT NULL,
    object_id text NOT NULL,
    relationship text NOT NULL,
    weight double precision NOT NULL,
    rationale text NOT NULL,
    FOREIGN KEY(interpretation_id, object_id) REFERENCES interpretation_evidence(interpretation_id, object_id)
);
CREATE INDEX IF NOT EXISTS interpretation_queue ON interpretation_jobs(status, lease_until, created_at);
CREATE INDEX IF NOT EXISTS interpretation_workspace ON interpretation_jobs(client_id, workspace_id, updated_at DESC);
-- Backend-only access. Existing trusted pool role must own tables or bypass RLS.
ALTER TABLE interpretation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE interpretation_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE interpretation_evidence_links ENABLE ROW LEVEL SECURITY;
