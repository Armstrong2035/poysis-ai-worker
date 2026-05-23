import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.primitives.consolidation.google_auth import build_auth_url, exchange_code
from app.primitives.database import DatabaseService

router = APIRouter(prefix="/auth", tags=["auth"])
db = DatabaseService()



@router.get("/google")
async def google_auth(workspace_id: str, user_id: str):
    """
    Step 1 — redirect the user to Google's consent screen.
    The workspace_id and user_id are passed as OAuth state (comma-separated) and returned on callback.
    """
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")

    state = f"{workspace_id},{user_id}"
    url = build_auth_url(state)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(code: str, state: str):
    """
    Step 2 — Google redirects here after user approves.
    Exchanges the code for tokens and stores them against the workspace and user.
    State format: "workspace_id,user_id"
    """
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    try:
        workspace_id, user_id = state.split(",")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state format.")

    tokens = await exchange_code(code)

    saved = await db.save_google_tokens(
        workspace_id=workspace_id,
        user_id=user_id,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expiry=tokens["expiry"],
    )

    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save tokens.")

    client_url = os.getenv("CLIENT_URL", "/")
    return RedirectResponse(f"{client_url}?google_auth=success&workspace_id={workspace_id}")
