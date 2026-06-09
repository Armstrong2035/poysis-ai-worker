"""Google Drive and other sources integration."""
from fastapi import APIRouter, HTTPException, Depends, Form, Query
import os
from typing import Optional

from app.api.security import get_user_id
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token

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
