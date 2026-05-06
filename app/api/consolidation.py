from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token

router = APIRouter(prefix="/consolidation", tags=["consolidation"])
db = DatabaseService()


class SnapshotRequest(BaseModel):
    workspace_id: str
    sources: List[str] = ["google_drive"]
    time_window_days: int = 90
    doc_limit: int = 500
    drive_folder_ids: List[str] = []
    cluster_instructions: List[dict] = []


@router.post("/discover")
async def discover(req: SnapshotRequest):
    access_token = await get_valid_token(req.workspace_id, db)
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="No Google token found for this workspace. Complete OAuth first."
        )

    scope = ScopeConfig(
        workspace_id=req.workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        google_access_token=access_token,
    )

    runner = SnapshotRunner(scope=scope)
    return await runner.discover()


@router.post("/snapshot")
async def run_snapshot(req: SnapshotRequest):
    access_token = await get_valid_token(req.workspace_id, db)
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="No Google token found for this workspace. Complete OAuth first."
        )

    scope = ScopeConfig(
        workspace_id=req.workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        cluster_instructions=req.cluster_instructions,
        google_access_token=access_token,
    )

    runner = SnapshotRunner(scope=scope)
    result = await runner.run()

    return {
        "workspace_id": result.workspace_id,
        "docs_processed": result.docs_processed,
        "docs_skipped": result.docs_skipped,
        "chunks_produced": len(result.all_chunks),
        "errors": result.errors,
    }
