import os
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.primitives.consolidation.google_auth import (
    build_auth_url,
    exchange_code,
    fetch_google_email,
)
from app.primitives.database import DatabaseService

router = APIRouter(prefix="/auth", tags=["auth"])
db = DatabaseService()


def _client_redirect(params: str) -> RedirectResponse:
    client_url = os.getenv("CLIENT_URL", "/")
    return RedirectResponse(f"{client_url}?{params}")


@router.get("/google")
async def google_auth(workspace_id: str, user_id: str):
    """
    Step 1 — redirect the user to Google's consent screen.
    workspace_id and user_id are packed into OAuth state and returned on callback.
    """
    if not workspace_id or not user_id:
        return _client_redirect("drive=error&reason=missing_params")

    state = f"{workspace_id},{user_id}"
    return RedirectResponse(build_auth_url(state))


@router.get("/google/callback")
async def google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """
    Step 2 — Google redirects here after user approves.
    Exchanges the code, looks up the Google account email, validates the token by
    counting Drive files, and persists to both drive_connections and consolidation_workspaces.
    On error, redirects back to the client with a reason rather than raising — the user
    is in a browser, not an API client.
    """
    if error:
        return _client_redirect(f"drive=denied&reason={error}")
    if not code or not state:
        return _client_redirect("drive=error&reason=missing_params")

    try:
        workspace_id, user_id = state.split(",")
    except ValueError:
        return _client_redirect("drive=error&reason=invalid_state")

    try:
        tokens = await exchange_code(code)
        google_email = await fetch_google_email(tokens["access_token"])
    except Exception as e:
        print(f"[AUTH] OAuth exchange failed: {e}")
        return _client_redirect("drive=error&reason=token_exchange")

    saved = await db.save_drive_connection(
        user_id=user_id,
        workspace_id=workspace_id,
        google_account_email=google_email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_expiry=tokens["expiry"],
    )
    if not saved:
        return _client_redirect("drive=error&reason=db")

    synced = await db.save_google_tokens(
        workspace_id=workspace_id,
        user_id=user_id,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expiry=tokens["expiry"],
    )
    if not synced:
        # Tokens are in drive_connections but consolidation pipeline can't see them.
        # Surface this rather than reporting success.
        return _client_redirect("drive=error&reason=consolidation_sync")

    print(f"[AUTH] Drive connected: workspace={workspace_id} user={user_id} email={google_email}")
    return _client_redirect("drive=connected")
