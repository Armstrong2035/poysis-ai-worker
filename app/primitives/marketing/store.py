"""Postgres persistence using the backend's existing connection pool."""

import json

from app.primitives.database import _conn, _run
from psycopg2.extras import RealDictCursor, execute_values


class PostgresMarketingStore:
    async def save_planner_report(self, workspace_id, request, content, fingerprint, user_id):
        def write():
            with _conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO marketing_keyword_planner_reports
                            (workspace_id, snapshot_id, customer_id, dataset, captured_at,
                             content, fingerprint, imported_by)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (workspace_id, snapshot_id) DO UPDATE
                            SET fingerprint = EXCLUDED.fingerprint
                            WHERE marketing_keyword_planner_reports.fingerprint = EXCLUDED.fingerprint
                        RETURNING snapshot_id
                    """, (workspace_id, str(request.snapshot_id), request.context.customer_id,
                          request.context.dataset, request.captured_at, json.dumps(content), fingerprint, user_id))
                    saved = cursor.fetchone() is not None
                conn.commit()
                return saved
        return await _run(write)

    async def list_planner_reports(self, workspace_id, customer_id, dataset, limit, offset):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT snapshot_id, customer_id, dataset, captured_at, imported_at,
                               content->'context' AS context,
                               content->>'report_kind' AS report_kind,
                               jsonb_array_length(content->'keywords') AS keyword_count
                        FROM marketing_keyword_planner_reports
                        WHERE workspace_id=%s AND customer_id=%s AND dataset=%s
                        ORDER BY captured_at DESC, snapshot_id LIMIT %s OFFSET %s
                    """, (workspace_id, customer_id, dataset, limit + 1, offset))
                    return [dict(row) for row in cursor.fetchall()]
        return await _run(read)

    async def get_planner_report(self, workspace_id, snapshot_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT content FROM marketing_keyword_planner_reports
                        WHERE workspace_id=%s AND snapshot_id=%s
                    """, (workspace_id, str(snapshot_id)))
                    row = cursor.fetchone()
                    return row["content"] if row else None
        return await _run(read)

    async def upsert(self, rows):
        if not rows:
            return

        def write():
            with _conn() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, """
                        INSERT INTO marketing_observations
                          (workspace_id, source, property_id, dataset, day,
                           dimension_key, dimensions, metrics)
                        VALUES %s
                        ON CONFLICT (workspace_id, source, property_id, dataset, day, dimension_key)
                        DO UPDATE SET dimensions = EXCLUDED.dimensions,
                                      metrics = EXCLUDED.metrics, ingested_at = now()
                    """, rows)
                conn.commit()
        await _run(write)

    async def fetch(self, workspace_id, source, property_id, dataset, start, end):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT day, dimensions, metrics FROM marketing_observations
                        WHERE workspace_id = %s AND source = %s AND property_id = %s
                          AND dataset = %s AND day BETWEEN %s AND %s
                        ORDER BY day, dimension_key
                        LIMIT 100001
                    """, (workspace_id, source, property_id, dataset, start, end))
                    return [dict(row) for row in cursor.fetchall()]
        return await _run(read)

    async def data(self, workspace_id, source, property_id, dataset, start, end,
                   dimensions, limit, offset):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT day, dimensions, metrics, ingested_at
                        FROM marketing_observations
                        WHERE workspace_id=%s AND source=%s AND property_id=%s
                          AND dataset=%s AND day BETWEEN %s AND %s
                          AND dimensions @> %s::jsonb
                        ORDER BY day, dimension_key LIMIT %s OFFSET %s
                    """, (workspace_id, source, property_id, dataset, start, end,
                          json.dumps(dimensions), limit + 1, offset))
                    return [dict(row) for row in cursor.fetchall()]
        return await _run(read)

    async def list_rules(self, workspace_id, limit, offset):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT rule_id, config, revision, updated_at, updated_by
                        FROM marketing_rules WHERE workspace_id=%s
                        ORDER BY rule_id LIMIT %s OFFSET %s
                    """, (workspace_id, limit + 1, offset))
                    return [dict(row) for row in cursor.fetchall()]
        return await _run(read)

    async def get_rule(self, workspace_id, rule_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT rule_id, config, revision, updated_at, updated_by
                        FROM marketing_rules WHERE workspace_id=%s AND rule_id=%s
                    """, (workspace_id, str(rule_id)))
                    row = cursor.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def save_rule(self, workspace_id, rule_id, rule, user_id, expected_revision):
        """Revision 0 creates; updates require the current revision to avoid lost edits."""
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    if expected_revision == 0:
                        cursor.execute("""
                            INSERT INTO marketing_rules (workspace_id, rule_id, config, updated_by)
                            VALUES (%s, %s, %s::jsonb, %s)
                            ON CONFLICT (workspace_id, rule_id) DO NOTHING
                            RETURNING rule_id, config, revision, updated_at, updated_by
                        """, (workspace_id, str(rule_id), rule.model_dump_json(), user_id))
                    else:
                        cursor.execute("""
                            UPDATE marketing_rules
                            SET config=%s::jsonb, revision=revision+1, updated_by=%s, updated_at=now()
                            WHERE workspace_id=%s AND rule_id=%s AND revision=%s
                            RETURNING rule_id, config, revision, updated_at, updated_by
                        """, (rule.model_dump_json(), user_id, workspace_id, str(rule_id), expected_revision))
                    row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)
