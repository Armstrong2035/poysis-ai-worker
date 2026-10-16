"""Postgres persistence for the tenant (organization) layer.

A tenant groups workspaces under a reseller/org. Users join a tenant through
`tenant_members` with a role (owner / admin / member). Self-serve sign-ups
provision a personal tenant via `ensure_personal_tenant`.
"""

import json
import secrets
import uuid

from psycopg2.extras import RealDictCursor

from app.primitives.database import _conn, _run

TENANT_COLS = "id, slug, name, branding, plan, status, created_by, created_at, updated_at"


class TenantStore:
    async def get_tenant(self, tenant_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {TENANT_COLS} FROM tenants WHERE id=%s",
                        (str(tenant_id),),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def get_tenant_by_slug(self, slug):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {TENANT_COLS} FROM tenants WHERE slug=%s",
                        (slug,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def get_tenant_for_user(self, user_id):
        """The tenant this user owns (first owner membership), or None."""
        cols = ", ".join(f"t.{c}" for c in TENANT_COLS.split(", "))
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {cols} FROM tenants t "
                        "JOIN tenant_members tm ON tm.tenant_id = t.id "
                        "WHERE tm.user_id=%s AND tm.role='owner' "
                        "ORDER BY t.created_at LIMIT 1",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def is_member(self, tenant_id, user_id):
        def read():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT role FROM tenant_members WHERE tenant_id=%s AND user_id=%s",
                        (str(tenant_id), user_id),
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        return await _run(read)

    async def ensure_personal_tenant(self, user_id, name="My Organization"):
        """Provision (or return) the user's personal tenant. Idempotent."""
        existing = await self.get_tenant_for_user(user_id)
        if existing:
            return existing

        tenant_id = str(uuid.uuid4())
        for _ in range(5):
            slug = "org-" + secrets.token_hex(6)
            created = await self._create_tenant(tenant_id, slug, name, user_id)
            if created is None:
                # Slug collision, or a concurrent caller already provisioned.
                existing = await self.get_tenant_for_user(user_id)
                if existing:
                    return existing
                continue
            await self.add_member(tenant_id, user_id, "owner")
            return created
        raise RuntimeError("Could not provision a tenant")

    async def _create_tenant(self, tenant_id, slug, name, created_by):
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "INSERT INTO tenants (id, slug, name, created_by) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (slug) DO NOTHING "
                        "RETURNING " + TENANT_COLS,
                        (tenant_id, slug, name, created_by),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)

    async def add_member(self, tenant_id, user_id, role):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO tenant_members (tenant_id, user_id, role) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (tenant_id, user_id) DO NOTHING",
                        (str(tenant_id), user_id, role),
                    )
                conn.commit()
        await _run(write)

    async def attach_workspaces(self, user_id, tenant_id):
        """Link any of this user's workspaces that lack a tenant to the tenant."""
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE workspaces SET tenant_id=%s "
                        "WHERE user_id=%s AND tenant_id IS NULL",
                        (str(tenant_id), user_id),
                    )
                conn.commit()
        await _run(write)

    async def list_workspaces(self, tenant_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT workspace_id, user_id, name, created_at "
                        "FROM workspaces WHERE tenant_id=%s ORDER BY created_at",
                        (str(tenant_id),),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def list_members(self, tenant_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT user_id, role, created_at FROM tenant_members "
                        "WHERE tenant_id=%s ORDER BY created_at",
                        (str(tenant_id),),
                    )
                    return [dict(r) for r in cur.fetchall()]
        return await _run(read)

    async def update_branding(self, tenant_id, branding):
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "UPDATE tenants SET branding=%s::jsonb, updated_at=NOW() "
                        "WHERE id=%s RETURNING " + TENANT_COLS,
                        (json.dumps(branding), str(tenant_id)),
                    )
                    row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None
        return await _run(write)
