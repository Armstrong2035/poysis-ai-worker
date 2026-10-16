"""Postgres persistence for the frontend-owned tables (formerly Supabase/RLS).

The Next.js apps (studio, developer, marketplace) previously read and wrote
`notebooks`, `cluster_ceilings`, `profiles`, and `workspaces` directly through
Supabase. After the Cognito migration they reach this store through the worker
API instead; the worker verifies the caller's token and scopes every query to
`user_id`, so there is no RLS and no anon path into private rows.
"""

import json
import uuid

from psycopg2.extras import RealDictCursor

from app.primitives.database import _conn, _run


class ClientDataStore:
    # ── Profiles ────────────────────────────────────────────────────────────

    async def get_profile(self, user_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT user_id, is_admin, ui_mode, enterprise, created_at "
                        "FROM profiles WHERE user_id=%s",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def ensure_profile(self, user_id):
        """Create a default profile row if missing; returns the current profile."""
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "INSERT INTO profiles (user_id, is_admin, enterprise) "
                        "VALUES (%s, FALSE, FALSE) ON CONFLICT (user_id) DO NOTHING",
                        (user_id,),
                    )
                    cur.execute(
                        "SELECT user_id, is_admin, ui_mode, enterprise, created_at "
                        "FROM profiles WHERE user_id=%s",
                        (user_id,),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)

    async def set_ui_mode(self, user_id, mode):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE profiles SET ui_mode=%s WHERE user_id=%s",
                        (mode, user_id),
                    )
                conn.commit()
        await _run(write)

    async def set_enterprise(self, user_id, enabled):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE profiles SET enterprise=%s WHERE user_id=%s",
                        (enabled, user_id),
                    )
                conn.commit()
        await _run(write)

    # ── Workspaces ──────────────────────────────────────────────────────────

    async def get_workspace_id(self, user_id):
        def read():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT workspace_id FROM workspaces WHERE user_id=%s "
                        "ORDER BY workspace_id LIMIT 1",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        return await _run(read)

    async def ensure_workspace(self, user_id):
        """Return an existing workspace id, or create a default one.

        Self-serve tenancy: provision the caller's personal tenant and attach
        any of their workspaces that still lack one, so every workspace belongs
        to a tenant from the moment the account first touches the app.
        """
        from app.primitives.tenant_store import TenantStore

        tenant = await TenantStore().ensure_personal_tenant(user_id)

        existing = await self.get_workspace_id(user_id)
        if existing:
            await TenantStore().attach_workspaces(user_id, tenant["id"])
            return existing

        workspace_id = str(uuid.uuid4())
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO workspaces (workspace_id, user_id, name, tenant_id) "
                        "VALUES (%s, %s, 'My Workspace', %s) ON CONFLICT (workspace_id) DO NOTHING",
                        (workspace_id, user_id, tenant["id"]),
                    )
                conn.commit()
        await _run(write)
        # A concurrent create may have won; re-resolve.
        return await self.get_workspace_id(user_id) or workspace_id

    # ── Notebooks ───────────────────────────────────────────────────────────

    _NOTEBOOK_COLS = "id, user_id, workspace_id, name, slug, config, verified, created_at, updated_at"

    async def list_notebooks(self, user_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {self._NOTEBOOK_COLS} FROM notebooks WHERE user_id=%s "
                        "ORDER BY created_at ASC",
                        (user_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def get_notebook(self, user_id, notebook_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {self._NOTEBOOK_COLS} FROM notebooks "
                        "WHERE id=%s AND user_id=%s",
                        (str(notebook_id), user_id),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def create_notebook(self, user_id, workspace_id, name, config):
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "INSERT INTO notebooks (user_id, workspace_id, name, config) "
                        "VALUES (%s, %s, %s, %s::jsonb) RETURNING " + self._NOTEBOOK_COLS,
                        (user_id, workspace_id, name, json.dumps(config)),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)

    async def save_notebook(self, user_id, notebook_id, name, config):
        """Full-config upsert scoped to the owner."""
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "INSERT INTO notebooks (id, user_id, name, config, updated_at) "
                        "VALUES (%s, %s, %s, %s::jsonb, NOW()) "
                        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, "
                        "config=EXCLUDED.config, updated_at=NOW() "
                        "WHERE notebooks.user_id=EXCLUDED.user_id "
                        "RETURNING " + self._NOTEBOOK_COLS,
                        (str(notebook_id), user_id, name, json.dumps(config)),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)

    async def rename_notebook(self, user_id, notebook_id, name):
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "UPDATE notebooks SET name=%s, updated_at=NOW() "
                        "WHERE id=%s AND user_id=%s RETURNING " + self._NOTEBOOK_COLS,
                        (name, str(notebook_id), user_id),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)

    async def set_slug(self, user_id, notebook_id, slug):
        """Returns True on success, or 'taken' if the slug is already used."""
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notebooks SET slug=%s, updated_at=NOW() "
                        "WHERE id=%s AND user_id=%s",
                        (slug, str(notebook_id), user_id),
                    )
                    changed = cur.rowcount == 1
                conn.commit()
                return changed
        try:
            return await _run(write)
        except Exception as e:
            if "23505" in str(e):
                return "taken"
            raise

    async def clear_slug(self, user_id, notebook_id):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notebooks SET slug=NULL, updated_at=NOW() "
                        "WHERE id=%s AND user_id=%s",
                        (str(notebook_id), user_id),
                    )
                conn.commit()
        await _run(write)

    async def delete_notebook(self, user_id, notebook_id):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM notebooks WHERE id=%s AND user_id=%s",
                        (str(notebook_id), user_id),
                    )
                    count = cur.rowcount
                conn.commit()
                return count
        return await _run(write)

    # ── Public notebook reads (no auth) ─────────────────────────────────────

    async def get_public_notebook(self, notebook_id):
        """Public read. user_id is the owner (needed by the marketplace chat
        proxy to scope ceilings); no other identity column is exposed."""
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, name, slug, config, user_id, workspace_id, verified "
                        "FROM notebooks WHERE id=%s",
                        (str(notebook_id),),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def get_public_notebook_by_slug(self, slug):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, name, slug, config, user_id, workspace_id, verified "
                        "FROM notebooks WHERE slug=%s",
                        (slug,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def list_public_directory(self):
        """Marketplace directory: verified notebooks that are public."""
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, name, slug, config, user_id, created_at "
                        "FROM notebooks WHERE verified=TRUE AND slug IS NOT NULL "
                        "ORDER BY created_at DESC",
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    # ── Cluster ceilings ────────────────────────────────────────────────────

    async def list_ceilings(self, owner_user_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT topic_id, owner_user_id, ceiling, updated_at "
                        "FROM cluster_ceilings WHERE owner_user_id=%s",
                        (owner_user_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def list_public_ceilings(self, owner_user_id, topic_ids=None):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    sql = ("SELECT topic_id FROM cluster_ceilings "
                           "WHERE owner_user_id=%s AND ceiling='public'")
                    params = [owner_user_id]
                    if topic_ids:
                        sql += " AND topic_id = ANY(%s)"
                        params.append(list(topic_ids))
                    cur.execute(sql, params)
                    return [r["topic_id"] for r in cur.fetchall()]
        return await _run(read)

    async def upsert_ceiling(self, topic_id, owner_user_id, ceiling):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO cluster_ceilings (topic_id, owner_user_id, ceiling, updated_at) "
                        "VALUES (%s, %s, %s, NOW()) "
                        "ON CONFLICT (topic_id, owner_user_id) DO UPDATE "
                        "SET ceiling=EXCLUDED.ceiling, updated_at=NOW()",
                        (topic_id, owner_user_id, ceiling),
                    )
                conn.commit()
        await _run(write)

    # ── Consolidation jobs (live status bars) ───────────────────────────────

    async def list_jobs(self, workspace_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM consolidation_jobs WHERE workspace_id=%s "
                        "ORDER BY created_at DESC LIMIT 20",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def list_drive_connections(self, user_id, workspace_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    if workspace_id:
                        cur.execute(
                            "SELECT id, workspace_id, google_account_email, doc_count, "
                            "last_synced_at, created_at FROM drive_connections "
                            "WHERE user_id=%s AND workspace_id=%s ORDER BY created_at DESC",
                            (user_id, workspace_id),
                        )
                    else:
                        cur.execute(
                            "SELECT id, workspace_id, google_account_email, doc_count, "
                            "last_synced_at, created_at FROM drive_connections "
                            "WHERE user_id=%s ORDER BY created_at DESC",
                            (user_id,),
                        )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    # ── Topic overrides ─────────────────────────────────────────────────────

    async def list_topic_overrides(self, workspace_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT topic_id, user_id, locked, custom_label, updated_at "
                        "FROM topic_overrides WHERE workspace_id=%s",
                        (workspace_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def upsert_topic_override(self, workspace_id, topic_id, user_id, locked):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO topic_overrides (workspace_id, topic_id, user_id, locked, updated_at) "
                        "VALUES (%s, %s, %s, %s, NOW()) "
                        "ON CONFLICT (workspace_id, topic_id) DO UPDATE "
                        "SET locked=EXCLUDED.locked, user_id=EXCLUDED.user_id, updated_at=NOW()",
                        (workspace_id, topic_id, user_id, locked),
                    )
                conn.commit()
        await _run(write)
