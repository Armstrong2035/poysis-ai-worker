#!/usr/bin/env python3
"""Run Supabase migration to add semantic columns."""
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_PRODUCT_URL")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("[ERROR] Supabase credentials not set")
    exit(1)

client = create_client(url, key)

# Read migration SQL
with open("migrations_add_semantic_columns.sql", "r") as f:
    sql = f.read()

print("[MIGRATION] Running SQL migration...")
print(f"[MIGRATION] URL: {url}")

try:
    # Execute migration - Supabase doesn't have a direct RPC for arbitrary SQL,
    # so we use the postgrest client to execute via a stored procedure or we handle it via the API
    # For now, let's just confirm the schema and print what needs to happen

    # Attempt to fetch a topic to see current schema
    result = client.table("consolidation_topics").select("*").limit(1).execute()

    if result.data:
        topic = result.data[0]
        has_semantic = 'semantic_summary' in topic
        has_themes = 'key_themes' in topic
        has_usecases = 'suggested_use_cases' in topic

        print("\n[SCHEMA CHECK]")
        print(f"  semantic_summary: {'OK' if has_semantic else 'MISSING'}")
        print(f"  key_themes: {'OK' if has_themes else 'MISSING'}")
        print(f"  suggested_use_cases: {'OK' if has_usecases else 'MISSING'}")

        if not (has_semantic and has_themes and has_usecases):
            print("\n[INFO] Columns are missing. You need to run this migration in Supabase SQL Editor:")
            print("\n" + "="*60)
            print(sql)
            print("="*60)
            print("\nSteps:")
            print("1. Go to Supabase Dashboard")
            print("2. Navigate to SQL Editor")
            print("3. Create new query and paste the SQL above")
            print("4. Click 'Run'")
        else:
            print("\n[SUCCESS] All semantic columns already exist!")

except Exception as e:
    print(f"[ERROR] {e}")
    exit(1)
