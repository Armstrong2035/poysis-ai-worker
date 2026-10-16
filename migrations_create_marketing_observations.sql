-- Apply to the backend Postgres database before using PostgresMarketingStore.
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
-- Backend-only table. Do not grant direct client access without tenant policies.
ALTER TABLE marketing_observations ENABLE ROW LEVEL SECURITY;
