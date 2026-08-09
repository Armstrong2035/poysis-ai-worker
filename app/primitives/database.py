"""
DatabaseService — raw psycopg2 replacement for the Supabase SDK client.

All methods preserve the exact same signatures and return types as the
original supabase-py implementation so callers need zero changes.

Connection string is read from DB_CONNECTION_STRING (RDS on AWS) with
fallback to SUPABASE_DIRECT_CONNECTION_STRING for local dev / transition.

Connection pooling: ThreadedConnectionPool (min=1, max=10) shared across
the process lifetime. All async methods run blocking psycopg2 calls via
asyncio.to_thread so the event loop is never blocked.

NOTE: get_active_drive_workspaces previously called Supabase Auth's admin
API (list_users). That API has no equivalent in raw Postgres — the auth
schema is Supabase-specific. The method now falls back to returning all
workspaces that have a Google token (i.e. no last-login filter). This is
conservative: the cron will sync slightly more workspaces than before, but
will never miss an active one.
"""

import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extensions import make_dsn
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Connection pool (process-wide singleton)
# ---------------------------------------------------------------------------

_pool: Optional[ThreadedConnectionPool] = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.getenv("DB_CONNECTION_STRING") or os.getenv("SUPABASE_DIRECT_CONNECTION_STRING")
        if not dsn:
            required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
            if all(os.getenv(name) for name in required):
                dsn = make_dsn(
                    host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
                    dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                    password=os.environ["DB_PASSWORD"], sslmode=os.getenv("DB_SSLMODE", "require"),
                )
        if not dsn:
            raise RuntimeError(
                "No database connection string found. "
                "Set DB_CONNECTION_STRING, SUPABASE_DIRECT_CONNECTION_STRING, or the DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD fields."
            )
        _pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
    return _pool


@contextmanager
def _conn():
    """Borrow a connection from the pool, return it when done."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn, close=conn.closed)


def _run(fn):
    """Run a blocking psycopg2 function in a thread so callers can await it."""
    return asyncio.to_thread(fn)



# ---------------------------------------------------------------------------
# DatabaseService
# ---------------------------------------------------------------------------

class DatabaseService:
    """Drop-in replacement for the Supabase SDK DatabaseService."""

    # kept for compatibility — some callers check `if not self.client`
    @property
    def client(self):
        return True  # non-falsy; pool is always ready if env var is set

    def refresh_client(self) -> None:
        """No-op — psycopg2 pool needs no refresh."""
        pass

    # ── Workspaces ───────────────────────────────────────────────────────────

    async def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM workspaces WHERE workspace_id = %s LIMIT 1",
                        (workspace_id,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch workspace: {e}")
            return None

    async def save_workspace(self, workspace_data: Dict[str, Any]) -> bool:
        cols = list(workspace_data.keys())
        vals = [workspace_data[c] for c in cols]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "workspace_id")
        sql = (
            f"INSERT INTO workspaces ({', '.join(cols)}) VALUES %s "
            f"ON CONFLICT (workspace_id) DO UPDATE SET {updates}"
        )
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, [vals])
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save workspace: {e}")
            return False

    async def create_workspace(self, workspace_id: str, user_id: str, name: str = "My Workspace") -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO workspaces (workspace_id, user_id, name) VALUES (%s, %s, %s) "
                        "ON CONFLICT (workspace_id) DO NOTHING",
                        (workspace_id, user_id, name),
                    )
                conn.commit()
        try:
            await _run(_q)
            await self.add_workspace_member(workspace_id, user_id, role="owner")
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to create workspace: {e}")
            return False

    async def has_workspace_access(self, workspace_id: str, user_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM workspace_members WHERE workspace_id = %s AND user_id = %s LIMIT 1",
                        (workspace_id, user_id),
                    )
                    return cur.fetchone() is not None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to check workspace access: {e}")
            return False

    async def add_workspace_member(self, workspace_id: str, user_id: str, role: str = "member") -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, %s) "
                        "ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role",
                        (workspace_id, user_id, role),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to add workspace member: {e}")
            return False

    async def remove_workspace_member(self, workspace_id: str, user_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                        (workspace_id, user_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to remove workspace member: {e}")
            return False

    async def get_workspace_members(self, workspace_id: str) -> List[Dict[str, Any]]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM workspace_members WHERE workspace_id = %s",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get workspace members: {e}")
            return []


    async def update_credits(self, workspace_id: str, amount: int) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE workspaces SET credits_balance = COALESCE(credits_balance,0) + %s "
                        "WHERE workspace_id = %s",
                        (amount, workspace_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update credits: {e}")
            return False

    async def increment_query_count(self, workspace_id: str):
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE workspaces SET total_queries = COALESCE(total_queries,0) + 1 "
                        "WHERE workspace_id = %s",
                        (workspace_id,),
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to increment query count: {e}")

    # ── Analytics / Logging ──────────────────────────────────────────────────

    async def log_search(self, analytics_data: Dict[str, Any]):
        cols = list(analytics_data.keys())
        vals = [analytics_data[c] for c in cols]
        sql = f"INSERT INTO search_logs ({', '.join(cols)}) VALUES %s"
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, [vals])
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to log search: {e}")

    async def log_attribution_event(self, event_data: Dict[str, Any]):
        cols = list(event_data.keys())
        vals = [event_data[c] for c in cols]
        sql = f"INSERT INTO attribution_events ({', '.join(cols)}) VALUES %s"
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, [vals])
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to log attribution event: {e}")

    async def log_topic_event(self, event_data: Dict[str, Any]):
        cols = list(event_data.keys())
        vals = [
            json.dumps(v) if isinstance(v, (list, dict)) else v
            for v in (event_data[c] for c in cols)
        ]
        sql = f"INSERT INTO topic_query_events ({', '.join(cols)}) VALUES %s"
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, [vals])
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to log topic event: {e}")

    async def get_dashboard_analytics(self, workspace_id: str, days: int = 30) -> Optional[Dict[str, Any]]:
        """Calls the get_dashboard_analytics Postgres function (same as the old Supabase RPC)."""
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM get_dashboard_analytics(%s, %s)",
                        (workspace_id, days),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch analytics: {e}")
            return None

    async def get_recent_searches(self, workspace_id: str, limit: int = 50) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, query, result_count, created_at, latency_ms "
                        "FROM search_logs WHERE workspace_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (workspace_id, limit),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch recent searches: {e}")
            return []

    async def get_raw_logs(self, workspace_id: str, start_date=None, end_date=None,
                           limit: int = 100, offset: int = 0) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    conditions = ["sl.workspace_id = %s"]
                    params: list = [workspace_id]
                    if start_date:
                        conditions.append("sl.created_at >= %s")
                        params.append(start_date)
                    if end_date:
                        conditions.append("sl.created_at <= %s")
                        params.append(end_date)
                    where = " AND ".join(conditions)
                    cur.execute(
                        f"SELECT sl.*, json_agg(ae.*) FILTER (WHERE ae.id IS NOT NULL) as attribution_events "
                        f"FROM search_logs sl "
                        f"LEFT JOIN attribution_events ae ON ae.search_log_id = sl.id "
                        f"WHERE {where} "
                        f"GROUP BY sl.id "
                        f"ORDER BY sl.created_at DESC "
                        f"LIMIT %s OFFSET %s",
                        params + [limit, offset],
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch raw logs: {e}")
            return []


    # ── Google OAuth tokens ──────────────────────────────────────────────────

    async def save_google_tokens(self, workspace_id: str, access_token: str,
                                  refresh_token: str, expiry: str, user_id: str = None) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO consolidation_workspaces "
                        "(workspace_id, user_id, google_access_token, google_refresh_token, google_token_expiry) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (workspace_id, user_id) DO UPDATE SET "
                        "google_access_token = EXCLUDED.google_access_token, "
                        "google_refresh_token = EXCLUDED.google_refresh_token, "
                        "google_token_expiry = EXCLUDED.google_token_expiry",
                        (workspace_id, user_id, access_token, refresh_token, expiry),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save Google tokens: {e}")
            return False

    async def get_google_tokens(self, workspace_id: str, user_id: str = None) -> Optional[dict]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if user_id:
                        cur.execute(
                            "SELECT google_access_token, google_refresh_token, google_token_expiry "
                            "FROM consolidation_workspaces WHERE workspace_id = %s AND user_id = %s LIMIT 1",
                            (workspace_id, user_id),
                        )
                    else:
                        cur.execute(
                            "SELECT google_access_token, google_refresh_token, google_token_expiry "
                            "FROM consolidation_workspaces WHERE workspace_id = %s LIMIT 1",
                            (workspace_id,),
                        )
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get Google tokens: {e}")
            return None

    # ── Indexed files ────────────────────────────────────────────────────────

    async def get_indexed_files(self, workspace_id: str) -> dict:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT source_id, etag FROM consolidation_indexed_files WHERE workspace_id = %s",
                        (workspace_id,),
                    )
                    return {r["source_id"]: r["etag"] for r in cur.fetchall()}
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch indexed files: {e}")
            return {}

    async def mark_files_indexed(self, workspace_id: str, files: list) -> None:
        if not files:
            return
        def _q():
            seen: dict = {}
            for f in files:
                seen[f["source_id"]] = f
            rows = [
                (workspace_id, f["source_id"], f["etag"], f.get("source_type", "google_drive"))
                for f in seen.values()
            ]
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO consolidation_indexed_files (workspace_id, source_id, etag, source_type) "
                        "VALUES %s ON CONFLICT (workspace_id, source_id) DO UPDATE SET "
                        "etag = EXCLUDED.etag, source_type = EXCLUDED.source_type",
                        rows,
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to mark files indexed: {e}")

    # ── Topics ───────────────────────────────────────────────────────────────

    async def clear_topics(self, workspace_id: str) -> None:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM consolidation_topics WHERE workspace_id = %s", (workspace_id,))
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to clear topics: {e}")

    async def save_topics(self, workspace_id: str, topics: list) -> None:
        if not topics:
            return
        def _q():
            rows = [
                (
                    workspace_id,
                    t["topic_id"],
                    t.get("label"),
                    json.dumps(t.get("keywords", [])),
                    t.get("doc_count", 0),
                    t.get("parent_topic_id"),
                    t.get("semantic_summary"),
                    json.dumps(t.get("key_themes", [])),
                    json.dumps(t.get("suggested_use_cases", [])),
                )
                for t in topics
            ]
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO consolidation_topics "
                        "(workspace_id, topic_id, label, keywords, doc_count, parent_topic_id, "
                        "semantic_summary, key_themes, suggested_use_cases, updated_at) "
                        "VALUES %s "
                        "ON CONFLICT (workspace_id, topic_id) DO UPDATE SET "
                        "label = EXCLUDED.label, keywords = EXCLUDED.keywords, "
                        "doc_count = EXCLUDED.doc_count, parent_topic_id = EXCLUDED.parent_topic_id, "
                        "semantic_summary = EXCLUDED.semantic_summary, key_themes = EXCLUDED.key_themes, "
                        "suggested_use_cases = EXCLUDED.suggested_use_cases, updated_at = NOW()",
                        rows,
                        template="(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,NOW())",
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save topics: {e}")

    async def get_topics(self, workspace_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT topic_id, label, keywords, doc_count, parent_topic_id, "
                        "semantic_summary, key_themes, suggested_use_cases, updated_at "
                        "FROM consolidation_topics WHERE workspace_id = %s ORDER BY doc_count DESC",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch topics: {e}")
            return []

    async def get_locked_topic_ids(self, workspace_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT topic_id FROM topic_overrides WHERE workspace_id = %s AND locked = TRUE",
                        (workspace_id,),
                    )
                    ids = []
                    for row in cur.fetchall():
                        try:
                            ids.append(int(row[0]))
                        except (TypeError, ValueError):
                            pass
                    return ids
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch locked topic ids: {e}")
            return []

    async def lock_topics(self, workspace_id: str, user_id: str, entries: list) -> None:
        if not entries:
            return
        def _q():
            rows = [
                (workspace_id, str(e["topic_id"]), user_id, True, e.get("label"))
                for e in entries
            ]
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO topic_overrides (workspace_id, topic_id, user_id, locked, custom_label, updated_at) "
                        "VALUES %s "
                        "ON CONFLICT (workspace_id, topic_id) DO UPDATE SET "
                        "locked = EXCLUDED.locked, custom_label = EXCLUDED.custom_label, updated_at = NOW()",
                        rows,
                        template="(%s,%s,%s,%s,%s,NOW())",
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to lock topics: {e}")


    # ── Stories ──────────────────────────────────────────────────────────────

    async def save_stories(self, workspace_id: str, stories: list) -> None:
        if not stories:
            return
        def _q():
            rows = [
                (
                    workspace_id,
                    s["story_id"],
                    s.get("title"),
                    s.get("description"),
                    json.dumps(s.get("topic_sequence", [])),
                    s.get("reasoning"),
                    float(s.get("strength", 0.5)),
                    s.get("doc_count", 0),
                )
                for s in stories
            ]
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO consolidation_stories "
                        "(workspace_id, story_id, title, description, topic_sequence, "
                        "reasoning, strength, doc_count, updated_at) "
                        "VALUES %s "
                        "ON CONFLICT (workspace_id, story_id) DO UPDATE SET "
                        "title=EXCLUDED.title, description=EXCLUDED.description, "
                        "topic_sequence=EXCLUDED.topic_sequence, reasoning=EXCLUDED.reasoning, "
                        "strength=EXCLUDED.strength, doc_count=EXCLUDED.doc_count, updated_at=NOW()",
                        rows,
                        template="(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,NOW())",
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save stories: {e}")

    async def get_stories(self, workspace_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT story_id, title, description, topic_sequence, reasoning, strength, doc_count "
                        "FROM consolidation_stories WHERE workspace_id = %s ORDER BY strength DESC",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch stories: {e}")
            return []

    async def clear_stories(self, workspace_id: str) -> None:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM consolidation_stories WHERE workspace_id = %s", (workspace_id,))
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to clear stories: {e}")

    # ── Topic documents ──────────────────────────────────────────────────────

    async def save_topic_documents(self, workspace_id: str, topic_id: int, videos: list) -> None:
        if not videos:
            return
        def _q():
            rows = [
                (workspace_id, topic_id, v["video_id"], json.dumps({
                    "title": v.get("title"), "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                    "thumbnail": v.get("thumbnail"), "published_at": v.get("published_at"),
                    "position": v.get("position"), "playlist_id": v.get("playlist_id"),
                    "playlist_title": v.get("playlist_title"),
                }))
                for v in videos
            ]
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO topic_documents (workspace_id, topic_id, source_id, metadata) VALUES %s "
                        "ON CONFLICT (workspace_id, topic_id, source_id) DO UPDATE SET metadata=EXCLUDED.metadata",
                        rows,
                        template="(%s,%s,%s,%s::jsonb)",
                    )
                conn.commit()
        try:
            await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save topic documents: {e}")

    async def get_topic_documents(self, workspace_id: str, topic_id: Optional[int] = None) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if topic_id is not None:
                        cur.execute(
                            "SELECT topic_id, source_id, metadata FROM topic_documents "
                            "WHERE workspace_id = %s AND topic_id = %s",
                            (workspace_id, topic_id),
                        )
                    else:
                        cur.execute(
                            "SELECT topic_id, source_id, metadata FROM topic_documents WHERE workspace_id = %s",
                            (workspace_id,),
                        )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch topic documents: {e}")
            return []

    # ── Accounts ─────────────────────────────────────────────────────────────

    async def is_admin_account(self, user_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT is_admin FROM profiles WHERE user_id = %s LIMIT 1",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    return bool(row and row[0])
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to read profile for {user_id}: {e}")
            return False

    async def add_waitlist_email(self, email: str, source: Optional[str] = None) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO waitlist (email, source) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING",
                        (email, source),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to add waitlist email: {e}")
            return False


    # ── Job tracking ─────────────────────────────────────────────────────────

    async def create_job(self, workspace_id: str, user_id: str, job_type: str) -> Optional[str]:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO consolidation_jobs (workspace_id, user_id, job_type, status) "
                        "VALUES (%s, %s, %s, 'running') RETURNING id",
                        (workspace_id, user_id, job_type),
                    )
                    row = cur.fetchone()
                conn.commit()
                return str(row[0]) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to create job: {e}")
            return None

    async def update_job(self, job_id: str, status: str, result: dict = None, error: str = None) -> bool:
        def _q():
            sets = ["status = %s", "updated_at = NOW()"]
            params: list = [status]
            if result:
                sets.append("result = %s::jsonb")
                params.append(json.dumps(result))
            if status == "done":
                sets.append("completed_at = NOW()")
            if error:
                sets.append("error = %s")
                params.append(error)
                if status != "running":
                    sets.append("completed_at = NOW()")
            params.append(job_id)
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"UPDATE consolidation_jobs SET {', '.join(sets)} WHERE id = %s", params)
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update job: {e}")
            return False

    async def get_job(self, job_id: str) -> Optional[dict]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM consolidation_jobs WHERE id = %s LIMIT 1", (job_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch job: {e}")
            return None

    async def record_job_progress(self, job_id: str, progress: dict) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE consolidation_jobs SET result = %s::jsonb, updated_at = NOW() WHERE id = %s",
                        (json.dumps(progress), job_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to record job progress: {e}")
            return False

    async def reap_stale_jobs(self, stale_after_seconds: int = 300) -> int:
        def _q():
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE consolidation_jobs SET status='failed', error='orphaned (no heartbeat)', "
                        "updated_at=NOW(), completed_at=NOW() "
                        "WHERE status='running' AND updated_at < %s",
                        (cutoff,),
                    )
                    count = cur.rowcount
                conn.commit()
                return count
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to reap stale jobs: {e}")
            return 0

    async def get_latest_job(self, workspace_id: str, job_type: str = None) -> Optional[dict]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if job_type:
                        cur.execute(
                            "SELECT * FROM consolidation_jobs WHERE workspace_id = %s AND job_type = %s "
                            "ORDER BY created_at DESC LIMIT 1",
                            (workspace_id, job_type),
                        )
                    else:
                        cur.execute(
                            "SELECT * FROM consolidation_jobs WHERE workspace_id = %s "
                            "ORDER BY created_at DESC LIMIT 1",
                            (workspace_id,),
                        )
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch latest job: {e}")
            return None


    # ── Google Drive connections ──────────────────────────────────────────────

    async def get_drive_connection(self, user_id: str, workspace_id: str, google_account_email: str) -> Optional[Dict[str, Any]]:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM drive_connections WHERE user_id=%s AND workspace_id=%s AND google_account_email=%s LIMIT 1",
                        (user_id, workspace_id, google_account_email),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch drive connection: {e}")
            return None

    async def list_drive_connections(self, user_id: str, workspace_id: Optional[str] = None) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if workspace_id:
                        cur.execute(
                            "SELECT id, workspace_id, google_account_email, doc_count, last_synced_at, created_at "
                            "FROM drive_connections WHERE user_id=%s AND workspace_id=%s ORDER BY created_at DESC",
                            (user_id, workspace_id),
                        )
                    else:
                        cur.execute(
                            "SELECT id, workspace_id, google_account_email, doc_count, last_synced_at, created_at "
                            "FROM drive_connections WHERE user_id=%s ORDER BY created_at DESC",
                            (user_id,),
                        )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to list drive connections: {e}")
            return []

    async def save_drive_connection(self, user_id: str, workspace_id: str, google_account_email: str,
                                     access_token: str, refresh_token: Optional[str] = None,
                                     token_expiry: Optional[str] = None) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO drive_connections "
                        "(user_id, workspace_id, google_account_email, access_token, refresh_token, token_expiry) "
                        "VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (user_id, workspace_id, google_account_email) DO UPDATE SET "
                        "access_token=EXCLUDED.access_token, refresh_token=EXCLUDED.refresh_token, "
                        "token_expiry=EXCLUDED.token_expiry",
                        (user_id, workspace_id, google_account_email, access_token, refresh_token, token_expiry),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save drive connection: {e}")
            return False

    async def update_drive_connection_doc_count(self, connection_id: str, doc_count: int) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE drive_connections SET doc_count=%s, last_synced_at=NOW() WHERE id=%s",
                        (doc_count, connection_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update drive connection doc_count: {e}")
            return False

    async def delete_drive_connection(self, user_id: str, workspace_id: str, google_account_email: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM drive_connections WHERE user_id=%s AND workspace_id=%s AND google_account_email=%s",
                        (user_id, workspace_id, google_account_email),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to delete drive connection: {e}")
            return False

    async def get_active_drive_workspaces(self, active_within_hours: int = 48) -> list:
        """Return workspaces that have a Google token stored.

        The original Supabase version filtered by last login via Auth Admin API,
        which has no equivalent in raw Postgres. This version returns all workspaces
        with a Google token — slightly broader but never misses an active workspace.
        """
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT workspace_id, user_id FROM consolidation_workspaces "
                        "WHERE google_access_token IS NOT NULL"
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get active drive workspaces: {e}")
            return []

    async def mark_drive_connection_synced(self, workspace_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE drive_connections SET last_synced_at=NOW() "
                        "WHERE id = (SELECT id FROM drive_connections WHERE workspace_id=%s "
                        "ORDER BY created_at DESC LIMIT 1)",
                        (workspace_id,),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to mark drive connection synced: {e}")
            return False


    # ── Nango connections ────────────────────────────────────────────────────

    async def save_nango_connection(self, workspace_id: str, user_id: str,
                                     provider: str, connection_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO nango_connections (workspace_id, user_id, provider, connection_id, enabled) "
                        "VALUES (%s,%s,%s,%s,TRUE) "
                        "ON CONFLICT (workspace_id, provider) DO UPDATE SET "
                        "connection_id=EXCLUDED.connection_id, enabled=TRUE",
                        (workspace_id, user_id, provider, connection_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save nango connection: {e}")
            return False

    async def get_nango_connections(self, workspace_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, workspace_id, provider, connection_id, enabled, created_at "
                        "FROM nango_connections WHERE workspace_id=%s AND enabled=TRUE",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get nango connections: {e}")
            return []

    async def delete_nango_connection(self, workspace_id: str, provider: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM nango_connections WHERE workspace_id=%s AND provider=%s",
                        (workspace_id, provider),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to delete nango connection: {e}")
            return False

    # ── YouTube channels ─────────────────────────────────────────────────────

    async def save_youtube_channel(self, workspace_id: str, user_id: str, channel_id: str,
                                    channel_name: str = "", min_duration_seconds: Optional[int] = None) -> bool:
        def _q():
            cols = ["workspace_id", "user_id", "channel_id", "channel_name", "enabled"]
            vals = [workspace_id, user_id, channel_id, channel_name, True]
            if min_duration_seconds is not None:
                cols.append("min_duration_seconds")
                vals.append(min_duration_seconds)
            update_cols = [c for c in cols if c not in ("workspace_id", "channel_id")]
            updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO youtube_channels ({', '.join(cols)}) VALUES %s "
                f"ON CONFLICT (workspace_id, channel_id) DO UPDATE SET {updates}"
            )
            with _conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, [vals])
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save YouTube channel: {e}")
            return False

    async def get_youtube_channels(self, workspace_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, workspace_id, channel_id, channel_name, enabled, created_at, min_duration_seconds "
                        "FROM youtube_channels WHERE workspace_id=%s AND enabled=TRUE",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get YouTube channels: {e}")
            return []

    async def find_youtube_channels_for_user(self, user_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT workspace_id, channel_id, channel_name, min_duration_seconds, created_at "
                        "FROM youtube_channels WHERE user_id=%s AND enabled=TRUE",
                        (user_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to list channels for user: {e}")
            return []

    async def find_workspaces_for_youtube_channel(self, channel_id: str) -> list:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT workspace_id FROM youtube_channels WHERE channel_id=%s AND enabled=TRUE",
                        (channel_id,),
                    )
                    return [r[0] for r in cur.fetchall()]
        try:
            return await _run(_q)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to look up channel workspaces: {e}")
            return []

    async def delete_youtube_channel(self, workspace_id: str, channel_id: str) -> bool:
        def _q():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM youtube_channels WHERE workspace_id=%s AND channel_id=%s",
                        (workspace_id, channel_id),
                    )
                conn.commit()
        try:
            await _run(_q)
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to delete YouTube channel: {e}")
            return False
