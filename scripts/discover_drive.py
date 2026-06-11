"""Report total Drive file count/breakdown for a workspace (read-only).

Usage:
    python scripts/discover_drive.py <workspace_id>
"""
import asyncio
import sys

from app.api.consolidation import db
from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.consolidation.google_auth import get_valid_token


async def _get_workspace_owner(workspace_id: str) -> str | None:
    res = (
        db.client.table("consolidation_workspaces")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .execute()
    )
    return res.data[0]["user_id"] if res.data else None


async def discover_drive(workspace_id: str) -> dict:
    user_id = await _get_workspace_owner(workspace_id)
    if not user_id:
        print(f"No workspace found for '{workspace_id}'.")
        return {}

    access_token = await get_valid_token(workspace_id, db, user_id)
    if not access_token:
        print("No valid Google token for this workspace.")
        return {}

    scope = ScopeConfig(
        workspace_id=workspace_id,
        sources=["google_drive"],
        time_window_days=0,
        doc_limit=10000,
        google_access_token=access_token,
    )
    runner = SnapshotRunner(scope=scope)
    result = await runner.discover()
    print(result)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/discover_drive.py <workspace_id>")
        sys.exit(1)
    asyncio.run(discover_drive(sys.argv[1]))
