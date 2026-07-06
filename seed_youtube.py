"""
Local YouTube transcript seeder.

Run this from the project root when Railway's IP is blocked by YouTube:
    python seed_youtube.py

Uses your local IP (not blocked) to fetch transcripts, then embeds and stores
them directly into production Supabase using the same pipeline as the cloud.

Requires: .env file with YOUTUBE_API_KEY, OPENAI_API_KEY,
          SUPABASE_PRODUCT_URL, SUPABASE_SERVICE_ROLE_KEY,
          SUPABASE_DIRECT_CONNECTION_STRING
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

WORKSPACE_ID = "98b8d281-72c9-44d8-9407-68af84411733"


async def main():
    from app.primitives.database import DatabaseService
    from app.primitives.consolidation.scope import ScopeConfig
    from app.primitives.consolidation.engine import ConsolidationEngine

    db = DatabaseService()

    yt_channels = await db.get_youtube_channels(WORKSPACE_ID)
    if not yt_channels:
        print("No YouTube channels connected to this workspace. Exiting.")
        sys.exit(1)

    channel_ids = [c["channel_id"] for c in yt_channels]
    print(f"Channels: {channel_ids}")

    indexed_files = await db.get_indexed_files(WORKSPACE_ID)
    print(f"Already indexed: {len(indexed_files)} videos (will be skipped)")

    scope = ScopeConfig(
        workspace_id=WORKSPACE_ID,
        sources=[],
        youtube_channel_ids=channel_ids,
        indexed_files=indexed_files,
        doc_limit=10000,
        time_window_days=0,
    )

    engine = ConsolidationEngine(db=db)

    def on_progress(state: dict):
        print(
            f"  docs={state['docs_processed']} "
            f"skipped={state['docs_skipped']} "
            f"orphaned={state['docs_orphaned']} "
            f"vectors={state['vectors_indexed']}"
        )

    print("\nStarting local snapshot...\n")
    result = await engine.run_snapshot(scope, progress_callback=on_progress)

    print("\n--- Done ---")
    print(f"Docs processed : {result['docs_processed']}")
    print(f"Docs skipped   : {result['docs_skipped']}")
    print(f"Docs orphaned  : {result['docs_orphaned']}")
    print(f"Vectors indexed: {result['vectors_indexed']}")
    if result["errors"]:
        print(f"\nErrors ({len(result['errors'])}):")
        for e in result["errors"][:20]:
            print(f"  {e}")
        if len(result["errors"]) > 20:
            print(f"  ... and {len(result['errors']) - 20} more")


if __name__ == "__main__":
    asyncio.run(main())
