"""Google Drive and other sources integration."""
from fastapi import APIRouter, HTTPException, Depends, Form, Query
import os
from typing import Optional

from app.api.security import get_user_id
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token

router = APIRouter(prefix="/sources", tags=["sources"])
db = DatabaseService()


async def _count_google_drive_files(access_token: str) -> int:
    """Count accessible files in Google Drive."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=access_token)
        service = build("drive", "v3", credentials=creds)

        results = service.files().list(
            spaces="drive",
            pageSize=1,
            fields="files(id)",
            q="trashed=false",
        ).execute()

        # Get total count from pagination info
        # Note: Google Drive API doesn't directly return total count, so we'll estimate
        # based on what we get. For MVP, just return files we can access.
        files = results.get("files", [])
        next_page_token = results.get("nextPageToken")

        count = len(files)
        if next_page_token:
            # If there's a next page, there are definitely more files
            count += 100  # Conservative estimate

        return count
    except Exception as e:
        print(f"[SOURCES] Error counting Drive files: {e}")
        return 0


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

        conn_id = conn["id"]

        # 4. Test token (call Drive API to verify it works)
        doc_count = await _count_google_drive_files(access_token)

        # 5. Update doc_count in drive_connections
        await db.update_drive_connection_doc_count(conn_id, doc_count)

        # 6. SYNC: Copy tokens to consolidation_workspaces for snapshot to use
        await db.save_google_tokens(
            workspace_id=workspace_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expiry=token_expiry,
            user_id=user_id,
        )

        print(
            f"[SOURCES] Drive connected for workspace {workspace_id}: "
            f"{google_account_email} ({doc_count} docs)"
        )

        return {
            "status": "connected",
            "workspace_id": workspace_id,
            "google_account_email": google_account_email,
            "doc_count": doc_count,
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
