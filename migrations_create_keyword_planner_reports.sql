-- Keyword Planner estimates are immutable research snapshots, not daily facts.
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
