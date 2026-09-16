CREATE TABLE IF NOT EXISTS marketing_rules (
    workspace_id text NOT NULL,
    rule_id uuid NOT NULL,
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, rule_id)
);
-- Backend access only; authenticated API enforces membership and owner edits.
ALTER TABLE marketing_rules ENABLE ROW LEVEL SECURITY;
