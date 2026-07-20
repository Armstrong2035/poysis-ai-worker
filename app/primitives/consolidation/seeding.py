"""Seed a directory bot from a single YouTube channel.

One bot = one workspace = one channel. That mapping is load-bearing: every channel
attached to a workspace is ingested into the same `consolidation_{workspace_id}`
namespace, and chunk metadata records the *video* id, not the channel — so two
channels sharing a workspace blend with no way to separate them afterwards short of
re-resolving every video against the YouTube API.

Both entry points (the `seed_bot.py` CLI and the seed-mode branch of
`/sources/youtube/connect`) go through here, so that invariant is enforced once.
"""
import os
import uuid
from typing import Callable, Dict, Optional

from app.primitives.consolidation.clustering import ClusteringEngine
from app.primitives.consolidation.connectors.youtube import resolve_channel
from app.primitives.consolidation.engine import ConsolidationEngine
from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.database import DatabaseService

# Directory channels are general-purpose talks, not 45-minute sermons. The app-wide
# ScopeConfig default (2700) would silently exclude every video on most channels, so
# seeding uses its own default and persists it per channel.
DEFAULT_SEED_MIN_DURATION = 600  # 10 minutes


class SeedError(Exception):
    """Seeding refused or failed. Message is safe to surface to the caller."""


async def resolve_and_check(db: DatabaseService, channel: str) -> tuple[str, str]:
    """Resolve a channel reference and refuse if it's already seeded.

    Runs before any writes: a rejected seed leaves nothing behind.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise SeedError("YOUTUBE_API_KEY is not set.")

    try:
        channel_id, channel_title = await resolve_channel(channel, api_key)
    except ValueError as e:
        raise SeedError(str(e))

    existing = await db.find_workspaces_for_youtube_channel(channel_id)
    if existing:
        raise SeedError(
            f"Channel '{channel_title}' is already seeded in workspace {existing[0]}. "
            "Delete that workspace first to re-seed."
        )
    return channel_id, channel_title


async def create_bot_workspace(
    db: DatabaseService,
    user_id: str,
    channel_id: str,
    name: str,
    min_duration_seconds: int = DEFAULT_SEED_MIN_DURATION,
) -> str:
    """Create a fresh workspace and attach exactly this one channel to it."""
    workspace_id = str(uuid.uuid4())
    if not await db.create_workspace(workspace_id, user_id, name=name):
        raise SeedError("Failed to create workspace.")

    if not await db.save_youtube_channel(
        workspace_id, user_id, channel_id, name, min_duration_seconds=min_duration_seconds
    ):
        raise SeedError(
            f"Workspace {workspace_id} was created but attaching the channel failed. "
            "Delete it and retry."
        )
    return workspace_id


async def ingest_and_cluster(
    db: DatabaseService,
    workspace_id: str,
    channel_id: str,
    min_duration_seconds: int,
    doc_limit: int = 500,
    skip_clustering: bool = False,
    progress_callback: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    """Run the snapshot to completion, then build topics. Returns run totals."""
    scope = ScopeConfig(
        workspace_id=workspace_id,
        youtube_channel_ids=[channel_id],
        youtube_min_duration_seconds=min_duration_seconds,
        doc_limit=doc_limit,
    )

    engine = ConsolidationEngine(db=db)
    totals = {"vectors_indexed": 0, "docs_processed": 0, "docs_skipped": 0, "docs_orphaned": 0}
    errors = []

    while True:
        result = await engine.run_snapshot(scope, progress_callback=progress_callback)
        for k in totals:
            totals[k] += result.get(k, 0)
        errors.extend(result.get("errors", []))
        if not result.get("partial"):
            break
        # Carry forward what's already indexed so the next pass doesn't redo it.
        indexed = await db.get_indexed_files(workspace_id)
        scope = scope.model_copy(update={"indexed_files": indexed})

    totals["errors"] = errors
    totals["topics_created"] = None

    if totals["vectors_indexed"] and not skip_clustering:
        cluster_result = await ClusteringEngine(db=db).run_clustering(workspace_id)
        totals["topics_created"] = cluster_result.get("topics_created")

    return totals
