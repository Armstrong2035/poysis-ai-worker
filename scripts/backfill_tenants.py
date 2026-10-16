#!/usr/bin/env python3
"""
Backfill: provision a personal tenant for every existing user and attach their
workspaces to it.

Safe to re-run: ensure_personal_tenant is idempotent, and attach_workspaces
only touches workspaces that still have tenant_id IS NULL.

Usage:
    DB_CONNECTION_STRING=postgresql://... python scripts/backfill_tenants.py
"""

import asyncio
import os

from app.primitives.database import _conn, _run
from app.primitives.tenant_store import TenantStore


async def main():
    def get_users():
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT user_id FROM workspaces WHERE user_id IS NOT NULL"
                )
                return [r[0] for r in cur.fetchall()]

    users = await _run(get_users)
    print(f"Backfilling tenants for {len(users)} user(s)")

    store = TenantStore()
    ok = 0
    for user_id in users:
        try:
            tenant = await store.ensure_personal_tenant(user_id)
            await store.attach_workspaces(user_id, tenant["id"])
            ok += 1
        except Exception as e:
            print(f"  [{user_id}] ERROR: {e}")

    print(f"Done: {ok}/{len(users)} user(s) provisioned")


if __name__ == "__main__":
    asyncio.run(main())
