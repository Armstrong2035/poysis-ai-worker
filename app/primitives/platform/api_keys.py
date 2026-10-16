"""Hashed API keys and tenant-scoped external client access.

A client (the "application" that holds keys) belongs to a tenant. A key is
issued by a tenant and targets one workspace inside it. Usage is attributed to
both, so a tenant can roll up usage across all of its workspaces.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.primitives.database import _conn, _run

ALLOWED_SCOPES = frozenset({
    "marketing:read",
    "interpretations:read",
    "interpretations:write",
    "tenant:member:write",
})


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def issue_key():
    key_id = uuid4()
    prefix = secrets.token_hex(5)
    raw = f"poysis_live_{prefix}.{secrets.token_urlsafe(32)}"
    return key_id, prefix, raw, hash_key(raw)


class APIKeyStore:
    async def ensure_client(self, tenant_id: str, name: str, owner_id: str):
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""INSERT INTO api_clients (client_id, tenant_id, name, created_by)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (tenant_id) WHERE tenant_id IS NOT NULL
                        DO UPDATE SET name=EXCLUDED.name
                        RETURNING client_id, tenant_id, workspace_id, name, status, created_at""",
                        (str(uuid4()), str(tenant_id), name, owner_id))
                    row = dict(cur.fetchone())
                conn.commit()
                return row
        return await _run(write)

    async def get_client(self, tenant_id: str):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT client_id, tenant_id, workspace_id, name, status, created_at "
                                "FROM api_clients WHERE tenant_id=%s", (str(tenant_id),))
                    row = cur.fetchone()
                    return dict(row) if row else None
        return await _run(read)

    async def create_key(self, tenant_id, client_id, name, scopes, expires_at, created_by, workspace_id):
        key_id, prefix, raw, digest = issue_key()
        def write():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""INSERT INTO api_keys
                        (key_id, client_id, tenant_id, workspace_id, name, key_prefix, key_hash, scopes, expires_at, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        RETURNING key_id, tenant_id, workspace_id, name, key_prefix, scopes, expires_at, created_at""",
                        (str(key_id), str(client_id), str(tenant_id), workspace_id, name,
                         prefix, digest, list(scopes), expires_at, created_by))
                    row = dict(cur.fetchone())
                conn.commit()
                return row
        row = await _run(write)
        return {**row, "api_key": raw}

    async def authenticate(self, raw_key: str, required_scope: str):
        if not raw_key.startswith("poysis_live_") or len(raw_key) > 200:
            return None
        digest = hash_key(raw_key)
        now = datetime.now(timezone.utc)
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""SELECT k.key_id, k.client_id, k.tenant_id, k.workspace_id, k.name, k.key_prefix,
                               k.scopes, c.name AS client_name
                        FROM api_keys k JOIN api_clients c ON c.client_id=k.client_id
                        WHERE k.key_hash=%s AND k.revoked_at IS NULL
                          AND (k.expires_at IS NULL OR k.expires_at>%s)
                          AND c.status='active'""", (digest, now))
                    row = cur.fetchone()
                    if not row or required_scope not in row["scopes"]:
                        return None
                    cur.execute("UPDATE api_keys SET last_used_at=%s WHERE key_id=%s", (now, row["key_id"]))
                    cur.execute("""INSERT INTO api_usage_events
                        (key_id, client_id, tenant_id, workspace_id, scope, occurred_at)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (row["key_id"], row["client_id"], row["tenant_id"], row["workspace_id"], required_scope, now))
                    result = dict(row)
                conn.commit()
                return result
        return await _run(read)

    async def list_keys(self, tenant_id):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""SELECT key_id, workspace_id, name, key_prefix, scopes, expires_at,
                               last_used_at, revoked_at, created_at
                        FROM api_keys WHERE tenant_id=%s ORDER BY created_at DESC""", (str(tenant_id),))
                    return [dict(row) for row in cur.fetchall()]
        return await _run(read)

    async def revoke_key(self, tenant_id, key_id):
        def write():
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE api_keys SET revoked_at=now() WHERE tenant_id=%s AND key_id=%s AND revoked_at IS NULL",
                                (str(tenant_id), str(key_id)))
                    changed = cur.rowcount == 1
                conn.commit()
                return changed
        return await _run(write)

    async def usage(self, tenant_id, days):
        def read():
            with _conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""SELECT date_trunc('day', occurred_at) AS day, scope, count(*) AS requests
                        FROM api_usage_events WHERE tenant_id=%s
                          AND occurred_at >= now()-(%s * interval '1 day')
                        GROUP BY day, scope ORDER BY day DESC, scope""", (str(tenant_id), days))
                    return [dict(row) for row in cur.fetchall()]
        return await _run(read)
