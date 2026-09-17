"""Resume a stuck/incomplete consolidation snapshot for a workspace.

Safe to re-run: skips already-indexed files (via consolidation_indexed_files)
and re-runs clustering at the end. Use when a user's snapshot job failed
partway through and you want to finish it without asking them to retry.

Usage:
    python scripts/resume_consolidation.py <workspace_id>
"""
import asyncio
import sys
from datetime import datetime, timezone

from app.api.consolidation import _run_snapshot_job, db, JOB_STALE_AFTER_SECONDS
from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.google_auth import get_valid_token


async def _get_workspace_owner(workspace_id: str) -> str | None:
    res = (
        db.client.table("consolidation_workspaces")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .execute()
    )
    return res.data[0]["user_id"] if res.data else None


async def resume_consolidation(workspace_id: str):
    user_id = await _get_workspace_owner(workspace_id)
    if not user_id:
        print(f"No workspace found for '{workspace_id}'.")
        return

    latest_job = await db.get_latest_job(workspace_id, job_type="snapshot")
    if latest_job and latest_job.get("status") == "running":
        updated_at = datetime.fromisoformat(latest_job["updated_at"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - updated_at).total_seconds() < JOB_STALE_AFTER_SECONDS:
            print(f"Snapshot job {latest_job['id']} is still running (heartbeat active). Aborting.")
            return

    access_token = await get_valid_token(workspace_id, db, user_id)
    if not access_token:
        print("No valid Google token for this workspace.")
        return

    indexed_files = await db.get_indexed_files(workspace_id)
    print(f"Already indexed: {len(indexed_files)} files")

    scope = ScopeConfig(
        workspace_id=workspace_id,
        sources=["google_drive"],
        time_window_days=0,
        doc_limit=10000,
        google_access_token=access_token,
        indexed_files=indexed_files,
    )

    job_id = await db.create_job(workspace_id, user_id, "snapshot")
    if not job_id:
        print("Failed to create job record.")
        return
    print(f"Job created: {job_id}")

    await _run_snapshot_job(workspace_id, user_id, scope, job_id)
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/resume_consolidation.py <workspace_id>")
        sys.exit(1)
    asyncio.run(resume_consolidation(sys.argv[1]))
