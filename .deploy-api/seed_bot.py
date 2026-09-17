"""Seed a directory bot from a single YouTube channel.

Thin CLI over app.primitives.consolidation.seeding — the same code path the
seed-mode branch of /sources/youtube/connect uses, so the two can't drift.

Usage:
    python seed_bot.py "https://youtube.com/@somechannel"
    python seed_bot.py "@somechannel" --name "Jane Doe" --min-duration 900
    python seed_bot.py "UCxxxxxxxxxxxxxxxxxxxxxx" --dry-run

The owning account comes from POYSIS_SEED_USER_ID (or --user-id). Poysis seeds and
owns these workspaces; ownership transfers to the expert when they claim the bot.
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

from app.primitives.consolidation import seeding
from app.primitives.database import DatabaseService


def _safe(s) -> str:
    """Windows consoles choke on non-ASCII channel titles."""
    return str(s).encode("ascii", "replace").decode("ascii")


def _parse_args():
    p = argparse.ArgumentParser(description="Seed a directory bot from a YouTube channel.")
    p.add_argument("channel", help="Channel URL, @handle, or raw UC... id")
    p.add_argument("--name", default="", help="Bot/workspace name (defaults to the channel title)")
    p.add_argument(
        "--min-duration", type=int, default=seeding.DEFAULT_SEED_MIN_DURATION, metavar="SECONDS",
        help=f"Minimum video length to ingest (default: {seeding.DEFAULT_SEED_MIN_DURATION}). "
             "The app-wide default is 2700 (45min), which suits long-form sermon channels but "
             "silently excludes everything on a channel of shorter talks. Shorts are <=60s. "
             "Persisted per channel, so later syncs reuse it.",
    )
    p.add_argument("--doc-limit", type=int, default=500, help="Max videos to ingest (-1 = unlimited)")
    p.add_argument("--user-id", default=os.getenv("POYSIS_SEED_USER_ID", ""),
                   help="Owning account (default: $POYSIS_SEED_USER_ID)")
    p.add_argument("--skip-clustering", action="store_true", help="Ingest only; don't build topics")
    p.add_argument("--dry-run", action="store_true",
                   help="Resolve the channel and report what would happen; write nothing")
    return p.parse_args()


async def main():
    args = _parse_args()
    if not args.user_id and not args.dry_run:
        sys.exit("No owning account. Set POYSIS_SEED_USER_ID or pass --user-id.")

    db = DatabaseService()

    # Resolve + duplicate check happen before any writes, so a refusal leaves nothing behind.
    try:
        channel_id, channel_title = await seeding.resolve_and_check(db, args.channel)
    except seeding.SeedError as e:
        sys.exit(_safe(e))

    name = args.name or channel_title
    print(f"Channel  : {_safe(channel_title)}  ({channel_id})")
    print(f"Bot name : {_safe(name)}")
    print(f"Min video: {args.min_duration}s ({args.min_duration / 60:.0f}min)")

    if args.dry_run:
        print("\n[dry-run] Would create a new workspace, attach this channel, and ingest.")
        return

    try:
        workspace_id = await seeding.create_bot_workspace(
            db, args.user_id, channel_id, name, min_duration_seconds=args.min_duration
        )
    except seeding.SeedError as e:
        sys.exit(_safe(e))

    print(f"\nWorkspace: {workspace_id}")
    print(f"Namespace: consolidation_{workspace_id}")

    def _on_progress(p):
        print(
            f"  ...{p['vectors_indexed']:>6} vectors | "
            f"{p['docs_processed']} done, {p['docs_skipped']} skipped, {p['docs_orphaned']} orphaned",
            end="\r", flush=True,
        )

    print("\nIngesting (sequential, ~5s per video — long channels take a while)...")
    totals = await seeding.ingest_and_cluster(
        db, workspace_id, channel_id, args.min_duration,
        doc_limit=args.doc_limit,
        skip_clustering=args.skip_clustering,
        progress_callback=_on_progress,
    )

    print(" " * 78, end="\r")
    print(f"Ingested : {totals['vectors_indexed']} vectors from {totals['docs_processed']} video(s)")
    print(f"           {totals['docs_skipped']} skipped, {totals['docs_orphaned']} orphaned (no captions)")

    errors = totals.get("errors") or []
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:10]:
            print(f"  - {_safe(e)}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")

    # The empty-bot case: fail loudly rather than leaving a dead bot in the directory.
    # Almost always the duration threshold being too high for this channel.
    if totals["vectors_indexed"] == 0:
        sys.exit(
            f"\nNo content ingested — this bot would be empty.\n"
            f"If the channel's videos are shorter than {args.min_duration / 60:.0f}min, "
            f"delete workspace {workspace_id} and re-run with a lower --min-duration."
        )

    if totals.get("topics_created") is not None:
        print(f"Topics   : {totals['topics_created']}")

    print(f"\nDone. workspace_id = {workspace_id}")


asyncio.run(main())
