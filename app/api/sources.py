"""Google Drive and other sources integration."""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Form, Query
import httpx
import os
from typing import Optional

from app.api.security import get_user_id
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token
from app.primitives.nango import client as nango

router = APIRouter(prefix="/sources", tags=["sources"])
db = DatabaseService()


@router.post("/gdrive/connect")
async def gdrive_connect(
    workspace_id: str = Form(...),
    google_account_email: str = Form(...),
    access_token: str = Form(...),
    refresh_token: Optional[str] = Form(None),
    token_expiry: Optional[str] = Form(None),
    user_id: str = Depends(get_user_id),
):
    """
    Sync Google Drive connection from drive_connections → consolidation_workspaces.

    Called by frontend after successful OAuth when user approves Drive access.
    Verifies workspace ownership, saves tokens, validates the token, counts documents,
    and syncs to consolidation_workspaces.
    """
    try:
        print(f"[SOURCES] gdrive_connect: workspace_id={workspace_id}, user_id={user_id}, email={google_account_email}")

        # 1. SECURITY: Verify user owns this workspace
        workspace = await db.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=404, detail="Workspace not found"
            )
        if workspace.get("user_id") != user_id:
            raise HTTPException(
                status_code=403, detail="You do not have access to this workspace"
            )

        # 2. Save the tokens to drive_connections (workspace-specific)
        saved = await db.save_drive_connection(
            user_id=user_id,
            workspace_id=workspace_id,
            google_account_email=google_account_email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=token_expiry,
        )
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save connection")

        # 3. Get the connection record to get its ID
        conn = await db.get_drive_connection(user_id, workspace_id, google_account_email)
        if not conn:
            raise HTTPException(status_code=500, detail="Connection save failed")

        # SYNC: Copy tokens to consolidation_workspaces for snapshot to use
        await db.save_google_tokens(
            workspace_id=workspace_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expiry=token_expiry,
            user_id=user_id,
        )

        print(
            f"[SOURCES] Drive connected for workspace {workspace_id}: "
            f"{google_account_email}"
        )

        return {
            "status": "connected",
            "workspace_id": workspace_id,
            "google_account_email": google_account_email,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SOURCES] Error connecting Google Drive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect Drive: {str(e)}")


@router.post("/gdrive/disconnect")
async def gdrive_disconnect(
    workspace_id: str = Form(...),
    google_account_email: str = Form(...),
    user_id: str = Depends(get_user_id),
):
    """Remove a Google Drive connection for a workspace."""
    try:
        # Verify workspace ownership
        workspace = await db.get_workspace(workspace_id)
        if not workspace or workspace.get("user_id") != user_id:
            raise HTTPException(
                status_code=403, detail="You do not have access to this workspace"
            )

        success = await db.delete_drive_connection(user_id, workspace_id, google_account_email)
        if not success:
            raise HTTPException(status_code=404, detail="Connection not found")

        print(f"[SOURCES] Drive disconnected: {workspace_id} / {google_account_email}")
        return {"status": "disconnected", "workspace_id": workspace_id, "google_account_email": google_account_email}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SOURCES] Error disconnecting Drive: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to disconnect Drive: {str(e)}"
        )


@router.get("/drive/connections")
async def list_drive_connections(
    user_id: str = Depends(get_user_id),
    workspace_id: str = Query(None),
):
    """List Google Drive connections for the user, optionally filtered by workspace."""
    try:
        connections = await db.list_drive_connections(user_id, workspace_id)
        return {"connections": connections}
    except Exception as e:
        print(f"[SOURCES] Error listing Drive connections: {e}")
        raise HTTPException(status_code=500, detail="Failed to list connections")


# ---------------------------------------------------------------------------
# Nango-managed sources (Slack, Notion, GitHub, etc.)
# ---------------------------------------------------------------------------

@router.get("/nango")
async def list_nango_connections(
    workspace_id: str = Query(...),
    user_id: str = Depends(get_user_id),
):
    """List all Nango-managed source connections for a workspace."""
    workspace = await db.get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    connections = await db.get_nango_connections(workspace_id)
    return {"connections": connections}


async def _seed_directory_bot(
    background_tasks: BackgroundTasks,
    user_id: str,
    channel_url: str,
    channel_name: str,
    min_duration_seconds: int,
):
    """Seed-mode branch: connecting a channel to the seeder workspace creates a
    NEW workspace for that channel rather than adding a source to the seeder.

    Deliberately does not touch the seeder workspace — one bot is one workspace is
    one channel, and attaching a second channel anywhere blends the two namespaces
    irreversibly.
    """
    from app.primitives.consolidation import seeding

    try:
        channel_id, resolved_title = await seeding.resolve_and_check(db, channel_url)
    except seeding.SeedError as e:
        raise HTTPException(status_code=409, detail=str(e))

    name = channel_name or resolved_title
    try:
        new_workspace_id = await seeding.create_bot_workspace(
            db, user_id, channel_id, name, min_duration_seconds=min_duration_seconds
        )
    except seeding.SeedError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Ingestion is sequential and rate-limited (~5s/video), far too long to hold the
    # request open. Progress is observable via /consolidation/snapshot/status/{id}.
    background_tasks.add_task(
        seeding.ingest_and_cluster, db, new_workspace_id, channel_id, min_duration_seconds
    )

    print(f"[SOURCES] Seeded directory bot: {channel_id} ({name}) → NEW workspace {new_workspace_id}")
    return {
        "status": "seeding",
        "mode": "seeded",
        "workspace_id": new_workspace_id,
        "channel_id": channel_id,
        "channel_name": name,
        "min_duration_seconds": min_duration_seconds,
        "message": (
            f"Seeding a new bot for '{name}' in workspace {new_workspace_id}. "
            "Ingestion runs in the background; it was not added to the seeder workspace."
        ),
    }


@router.post("/youtube/connect")
async def youtube_connect(
    background_tasks: BackgroundTasks,
    workspace_id: str = Form(...),
    channel_url: str = Form(...),
    channel_name: str = Form(""),
    min_duration_seconds: int = Form(0),
    user_id: str = Depends(get_user_id),
):
    """Save a YouTube channel to a workspace (no OAuth — public channels only).

    channel_url accepts a raw channel ID, a youtube.com URL (/channel/, /@handle,
    /c/, /user/), or a bare @handle — resolved server-side to the actual channel ID.

    If workspace_id is the configured SEED_WORKSPACE_ID, this seeds a brand-new
    directory bot instead — see _seed_directory_bot.
    """
    workspace = await db.get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="YouTube integration is not configured")

    from app.admin.auth import is_admin, is_seed_workspace
    from app.primitives.consolidation import seeding

    if is_seed_workspace(workspace_id):
        # Refuse rather than fall through. Falling through would attach the channel
        # to the seeder workspace itself, blending it with every other channel added
        # there — the one irreversible mistake this flow exists to prevent. A
        # misconfigured POYSIS_ADMIN_USER_IDS must fail loudly, not quietly corrupt.
        if not is_admin(user_id):
            raise HTTPException(
                status_code=403,
                detail="This workspace seeds directory bots and is admin-only.",
            )
        return await _seed_directory_bot(
            background_tasks,
            user_id,
            channel_url,
            channel_name,
            min_duration_seconds or seeding.DEFAULT_SEED_MIN_DURATION,
        )

    from app.primitives.consolidation.connectors.youtube import resolve_channel
    try:
        channel_id, resolved_title = await resolve_channel(channel_url, api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"YouTube API error while resolving channel: {e}")

    resolved_name = channel_name or resolved_title
    saved = await db.save_youtube_channel(
        workspace_id=workspace_id,
        user_id=user_id,
        channel_id=channel_id,
        channel_name=resolved_name,
    )
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save YouTube channel")

    print(f"[SOURCES] YouTube channel connected: {channel_id} ({resolved_name}) → workspace {workspace_id}")
    return {"status": "connected", "workspace_id": workspace_id, "channel_id": channel_id, "channel_name": resolved_name}


@router.delete("/youtube/{channel_id}")
async def youtube_disconnect(
    channel_id: str,
    workspace_id: str = Query(...),
    user_id: str = Depends(get_user_id),
):
    """Remove a YouTube channel from a workspace."""
    workspace = await db.get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    deleted = await db.delete_youtube_channel(workspace_id, channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel not found")

    print(f"[SOURCES] YouTube channel disconnected: {channel_id} ← workspace {workspace_id}")
    return {"status": "disconnected", "workspace_id": workspace_id, "channel_id": channel_id}


@router.get("/youtube/channels")
async def list_youtube_channels(
    workspace_id: str = Query(...),
    user_id: str = Depends(get_user_id),
):
    """List YouTube channels connected to a workspace."""
    workspace = await db.get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    channels = await db.get_youtube_channels(workspace_id)
    return {"channels": channels}


@router.delete("/nango/{provider}")
async def disconnect_nango_source(
    provider: str,
    workspace_id: str = Query(...),
    user_id: str = Depends(get_user_id),
):
    """Disconnect a Nango-managed source and remove the token from Nango."""
    workspace = await db.get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Best-effort removal from Nango (don't fail if Nango is unreachable)
    try:
        await nango.delete_connection(connection_id=workspace_id, provider=provider)
    except Exception as e:
        print(f"[SOURCES] Nango delete_connection failed (continuing): {e}")

    deleted = await db.delete_nango_connection(workspace_id, provider)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")

    print(f"[SOURCES] Nango disconnected: provider={provider} workspace={workspace_id}")
    return {"status": "disconnected", "provider": provider, "workspace_id": workspace_id}
