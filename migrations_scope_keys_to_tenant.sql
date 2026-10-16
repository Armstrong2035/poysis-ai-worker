-- Scope API keys, usage, and MCP connectors to the tenant layer.
--
-- api_clients was keyed on workspace_id (NOT NULL UNIQUE). It becomes
-- tenant-scoped: a client (the "application" that holds keys) belongs to a
-- tenant. workspace_id becomes nullable so legacy rows still read, and new
-- clients are tenant-scoped.
--
-- api_keys, api_usage_events, and mcp_tokens keep their workspace_id but gain a
-- tenant_id for attribution and rollup. A key still targets one workspace; the
-- tenant sees all of its workspaces' keys and usage.
--
-- Idempotent: every statement is safe to re-run.

ALTER TABLE api_clients
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE api_clients ALTER COLUMN workspace_id DROP NOT NULL;

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE api_usage_events
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE mcp_tokens
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS api_clients_tenant_unique
    ON api_clients (tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS api_keys_tenant_idx ON api_keys (tenant_id);
CREATE INDEX IF NOT EXISTS api_usage_tenant_time_idx
    ON api_usage_events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS mcp_tokens_tenant_idx ON mcp_tokens (tenant_id);

-- Backfill tenant_id from the owning workspace's tenant.
UPDATE api_clients c SET tenant_id = w.tenant_id
    FROM workspaces w WHERE w.workspace_id = c.workspace_id AND c.tenant_id IS NULL;
UPDATE api_keys k SET tenant_id = w.tenant_id
    FROM workspaces w WHERE w.workspace_id = k.workspace_id AND k.tenant_id IS NULL;
UPDATE api_usage_events u SET tenant_id = w.tenant_id
    FROM workspaces w WHERE w.workspace_id = u.workspace_id AND u.tenant_id IS NULL;
UPDATE mcp_tokens m SET tenant_id = w.tenant_id
    FROM workspaces w WHERE w.workspace_id = m.workspace_id AND m.tenant_id IS NULL;
