"""Admin-only HTTP surface, mounted at /admin.

Read-only diagnostics only — no frontend work needed, curl or a browser is enough.
Seeding is deliberately NOT exposed here: it is triggered by connecting a channel
to the seeder workspace through the normal UI (see app/api/sources.py), so there is
one write path rather than two that can drift.

Every route depends on `require_admin`, so adding a route here is admin-gated by
construction rather than by remembering to add a check.
"""
from fastapi import APIRouter, Depends

from app.admin.auth import require_admin, seed_workspace_id
from app.primitives.consolidation import seeding
from app.primitives.database import DatabaseService

router = APIRouter(prefix="/admin", tags=["admin"])
db = DatabaseService()


@router.get("/whoami")
async def whoami(user_id: str = Depends(require_admin)):
    """Confirm admin config is wired up. Reaching this at all means you're an admin."""
    return {
        "user_id": user_id,
        "is_admin": True,
        "seed_workspace_id": seed_workspace_id(),
        "seed_mode_active": seed_workspace_id() is not None,
        "default_seed_min_duration_seconds": seeding.DEFAULT_SEED_MIN_DURATION,
    }


@router.get("/bots")
async def list_bots(user_id: str = Depends(require_admin)):
    """Every workspace this admin owns that has a YouTube channel attached.

    The operational view while seeding: which bots exist, and whether each actually
    has content. `vectors: 0` means a bot that seeded but ingested nothing — usually
    min_duration_seconds set too high for that channel.
    """
    from app.primitives.knowledge.vector_store import VectorService

    channels = await db.find_youtube_channels_for_user(user_id)
    if not channels:
        return {"bots": [], "total": 0}

    vs = VectorService()
    counts = vs.count_vectors_by_namespace(
        [f"consolidation_{c['workspace_id']}" for c in channels]
    )

    bots = [
        {
            "workspace_id": c["workspace_id"],
            "channel_name": c["channel_name"],
            "channel_id": c["channel_id"],
            "min_duration_seconds": c.get("min_duration_seconds"),
            "vectors": counts.get(f"consolidation_{c['workspace_id']}", 0),
            "created_at": c.get("created_at"),
        }
        for c in channels
    ]
    bots.sort(key=lambda b: b["vectors"])  # empty bots first — they need attention
    return {"bots": bots, "total": len(bots)}
