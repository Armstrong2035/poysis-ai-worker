#!/usr/bin/env python3
"""Check what tables exist in Supabase."""
import asyncio
from app.primitives.database import DatabaseService

async def main():
    db = DatabaseService()

    if not db.client:
        print("[ERROR] Supabase client not initialized")
        return

    try:
        # Query information_schema to list all tables
        result = db.client.rpc(
            'get_tables',
            {}
        ).execute()
        print("[INFO] Available tables (via RPC):")
        print(result.data)
    except Exception as e:
        print(f"[RPC failed] {e}")
        print("\n[Trying direct query instead]...")

    # Try to list some known tables
    tables_to_check = [
        "consolidation_topics",
        "consolidation_workspaces",
        "workspaces",
        "search_logs",
        "consolidation_jobs"
    ]

    print("\n[Checking specific tables]:")
    for table in tables_to_check:
        try:
            result = db.client.table(table).select("COUNT(*)", count="exact").execute()
            count = result.count
            print(f"  {table}: EXISTS ({count} rows)")
        except Exception as e:
            print(f"  {table}: NOT FOUND - {str(e)[:80]}")

asyncio.run(main())
