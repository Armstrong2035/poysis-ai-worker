"""Import a channel's playlists as human-authored categories.

Transcripts are what's currently blocked from the datacenter, not the Data API — so a
channel's playlists (its own taxonomy) are readable right now. This turns selected
playlists into real `consolidation_topics` rows, locks them (`topic_overrides.locked`)
so a later AI recluster preserves them, and records video membership + metadata in
`topic_documents` so notebooks are browsable before any transcript exists.

Overlay semantics: this labels content, it does not scope ingestion — the whole channel
is still ingested; playlists just give some of its videos a durable category.
"""
import asyncio
import os
from typing import Dict, List

from app.primitives.consolidation.connectors.youtube import (
    list_channel_playlists,
    list_playlist_videos,
)
from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService


class PlaylistError(Exception):
    """Playlist import refused or failed. Message is safe to surface to the caller."""


def _require_api_key() -> str:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise PlaylistError("YOUTUBE_API_KEY is not set.")
    return api_key


async def list_playlists_for_workspace(db: DatabaseService, workspace_id: str) -> List[Dict]:
    """Every playlist across the workspace's connected channel(s), each tagged with its
    channel so the caller can group them."""
    api_key = _require_api_key()
    channels = await db.get_youtube_channels(workspace_id)
    if not channels:
        raise PlaylistError("No YouTube channel is connected to this workspace.")

    playlists: List[Dict] = []
    for ch in channels:
        for p in await list_channel_playlists(ch["channel_id"], api_key):
            p["channel_id"] = ch["channel_id"]
            p["channel_name"] = ch.get("channel_name")
            playlists.append(p)
    return playlists


async def import_playlists_as_categories(
    db: DatabaseService, workspace_id: str, user_id: str, playlist_ids: List[str]
) -> Dict:
    """Turn the given playlists into locked categories. Idempotent: a playlist whose title
    already matches a topic reuses that topic id, so re-importing refreshes rather than
    duplicates."""
    if not playlist_ids:
        raise PlaylistError("No playlists selected.")
    api_key = _require_api_key()

    # Fetch the workspace's playlists once and index by id, so we work from fresh
    # server-side data rather than client-supplied titles.
    available = {p["playlist_id"]: p for p in await list_playlists_for_workspace(db, workspace_id)}
    missing = [pid for pid in playlist_ids if pid not in available]
    if missing:
        raise PlaylistError(f"Playlist(s) not found on the connected channel(s): {missing}")

    # Reuse an existing top-level topic id when the label matches (mirrors the categorizer's
    # label-keyed id reuse at categorizer.py:229); otherwise allocate max+1.
    existing = await db.get_topics(workspace_id)
    by_label = {
        (t.get("label") or "").strip().lower(): t["topic_id"]
        for t in existing
        if t.get("parent_topic_id") is None
    }
    next_id = max((t["topic_id"] for t in existing), default=-1) + 1

    source_to_topic: Dict[str, int] = {}   # video_id -> topic_id, for vector stamping
    topic_label: Dict[int, str] = {}       # topic_id -> label, for vector stamping
    created: List[Dict] = []

    for pid in playlist_ids:
        title = available[pid]["title"]
        label_key = title.strip().lower()
        if label_key in by_label:
            topic_id = by_label[label_key]
        else:
            topic_id = next_id
            next_id += 1
            by_label[label_key] = topic_id
        topic_label[topic_id] = title

        videos = await list_playlist_videos(pid, api_key)
        for v in videos:
            v["playlist_id"] = pid
            v["playlist_title"] = title
            source_to_topic[v["video_id"]] = topic_id

        await db.save_topics(workspace_id, [{
            "topic_id": topic_id,
            "label": title,
            "doc_count": len(videos),
            "parent_topic_id": None,
        }])
        await db.lock_topics(workspace_id, user_id, [{"topic_id": topic_id, "label": title}])
        await db.save_topic_documents(workspace_id, topic_id, videos)

        created.append({"topic_id": topic_id, "label": title, "video_count": len(videos)})

    stamped = await _stamp_existing_vectors(workspace_id, source_to_topic, topic_label)
    for c in created:
        c["vectors_stamped"] = stamped.get(c["topic_id"], 0)

    return {"workspace_id": workspace_id, "categories": created}


async def _stamp_existing_vectors(
    workspace_id: str, source_to_topic: Dict[str, int], topic_label: Dict[int, str]
) -> Dict[int, int]:
    """Tag any already-ingested vectors for these videos with their playlist category, so
    chat scoping works immediately on transcribed workspaces. No-op when nothing is ingested
    yet (fresh channels) — the topic_documents membership carries the intent until backfill.
    Returns {topic_id: vectors_updated}."""
    if not source_to_topic:
        return {}
    vs = VectorService()
    namespace = f"consolidation_{workspace_id}"

    refs = await asyncio.to_thread(vs.fetch_vector_source_ids, namespace)
    updates = []
    counts: Dict[int, int] = {}
    for r in refs:
        tid = source_to_topic.get(r["source_id"])
        if tid is None:
            continue
        updates.append({
            "id": r["id"],
            "metadata": {"category_id": tid, "category_label": topic_label.get(tid, "")},
        })
        counts[tid] = counts.get(tid, 0) + 1
    if updates:
        await asyncio.to_thread(vs.update_vector_metadata_batch, updates, namespace)
    return counts
