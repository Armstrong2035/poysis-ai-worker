"""Who counts as an admin.

Account type lives in `public.profiles.is_admin`, set manually per account. The
table has no write policy, so the flag is service-role-only and a user cannot
promote themselves — that is the entire security model, so don't add one.

Admin-ness is a *boundary*, not just a feature gate: a YouTube channel connected
by an internal account is seeded into its own workspace (and therefore its own
vector namespace) rather than joining an existing one. See
`app/api/sources.py::youtube_connect`.

Replaces the previous env-var config (`POYSIS_ADMIN_USER_IDS`) and the magic
`SEED_WORKSPACE_ID` workspace whose "add a channel" action used to trigger
seeding. Both are retired: the account decides, not the target workspace.
"""
from fastapi import Depends, HTTPException

from app.api.security import get_user_id
from app.primitives.database import DatabaseService

db = DatabaseService()


async def is_admin(user_id: str) -> bool:
    """True if this account is Poysis staff.

    Fails closed — an unset flag, a missing profile row, or an unreachable
    database all mean "not an admin", so nothing can silently open an admin path.
    """
    return await db.is_admin_account(user_id)


async def require_admin(user_id: str = Depends(get_user_id)) -> str:
    """FastAPI dependency: 403 unless the caller is an admin.

    Returns the user_id so endpoints can depend on this instead of get_user_id
    and still know who is calling.
    """
    if not await is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user_id
