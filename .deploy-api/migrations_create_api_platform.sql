CREATE TABLE IF NOT EXISTS api_clients (
    client_id UUID PRIMARY KEY,
    workspace_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id UUID PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES api_clients(client_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE CHECK (char_length(key_hash) = 64),
    scopes TEXT[] NOT NULL,
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_keys_workspace ON api_keys(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS api_usage_events (
    event_id BIGSERIAL PRIMARY KEY,
    key_id UUID NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES api_clients(client_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_usage_workspace_time ON api_usage_events(workspace_id, occurred_at DESC);

ALTER TABLE api_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_usage_events ENABLE ROW LEVEL SECURITY;
