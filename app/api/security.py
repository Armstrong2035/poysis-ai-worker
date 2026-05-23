"""Security utilities for user validation and authorization."""

from fastapi import Depends, HTTPException, Header
from typing import Optional
from app.primitives.database import DatabaseService

db = DatabaseService()


async def get_user_id(x_user_id: Optional[str] = Header(None)) -> str:
    """
    Extract user_id from X-User-ID header.
    Raises 401 if missing.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=401,
            detail="Missing X-User-ID header. Please include your user ID in the request."
        )
    return x_user_id


async def verify_workspace_ownership(
    workspace_id: str,
    user_id: str = Depends(get_user_id)
) -> str:
    """
    Verify that the user owns the specified workspace.
    Returns the workspace_id if valid, raises 403 if not.
    """
    if not db.client:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        workspace = await db.get_workspace(workspace_id)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Check if user_id matches (allows None for legacy data, but enforces for new data)
        workspace_user = workspace.get("user_id")
        if workspace_user and workspace_user != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        return workspace_id

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SECURITY] Error verifying workspace ownership: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify workspace")
