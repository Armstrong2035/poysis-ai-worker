"""Who counts as an admin, and which workspace is the directory seeder.

Config is environment-only and read per call rather than cached at import, so
changing it doesn't require a redeploy of anything that imported this early.

    POYSIS_ADMIN_USER_IDS   comma-separated auth.users ids
    SEED_WORKSPACE_ID       workspace whose "add a channel" action seeds a new bot

Both default to empty, which disables every admin path. Nothing here grants
access to workspace data — ordinary ownership checks still apply on top.
"""
import os
from typing import Optional, Set

from fastapi import Depends, HTTPException

from app.api.security import get_user_id


def _admin_ids() -> Set[str]:
    raw = os.getenv("POYSIS_ADMIN_USER_IDS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def is_admin(user_id: str) -> bool:
    """True if this account is Poysis staff.

    Use for gating a feature to your own account before releasing it broadly.
    Empty/unset config means nobody is an admin — deliberately fails closed, so a
    missing env var can't silently open an admin path in production.
    """
    return bool(user_id) and user_id in _admin_ids()


def seed_workspace_id() -> Optional[str]:
    """The workspace that acts as the directory seeder, or None if unconfigured."""
    return os.getenv("SEED_WORKSPACE_ID") or None


def is_seed_workspace(workspace_id: str) -> bool:
    """True if adding a channel to this workspace should seed a new bot instead."""
    seeder = seed_workspace_id()
    return bool(seeder) and workspace_id == seeder


async def require_admin(user_id: str = Depends(get_user_id)) -> str:
    """FastAPI dependency: 403 unless the caller is an admin.

    Returns the user_id so endpoints can depend on this instead of get_user_id
    and still know who is calling.
    """
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user_id
