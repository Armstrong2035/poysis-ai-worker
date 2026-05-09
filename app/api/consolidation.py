from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.consolidation.engine import ConsolidationEngine
from app.primitives.consolidation.clustering import ClusteringEngine
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token

router = APIRouter(prefix="/consolidation", tags=["consolidation"])
db = DatabaseService()
engine = ConsolidationEngine(db=db)
clustering_engine = ClusteringEngine(db=db)

# In-memory job tracker — resets on redeploy, sufficient for now
_jobs: Dict[str, Dict[str, Any]] = {}


class SnapshotRequest(BaseModel):
    workspace_id: str
    sources: List[str] = ["google_drive"]
    time_window_days: int = 90
    doc_limit: int = 300
    drive_folder_ids: List[str] = []
    cluster_instructions: List[dict] = []


async def _run_snapshot_job(workspace_id: str, scope: ScopeConfig):
    _jobs[workspace_id] = {"status": "running", "vectors_indexed": 0, "docs_processed": 0, "errors": []}
    total_vectors = 0
    total_docs = 0
    total_skipped = 0
    total_orphaned = 0
    all_errors = []
    iteration = 0
    current_scope = scope

    try:
        while True:
            iteration += 1
            print(f"[Snapshot] Iteration {iteration} for workspace '{workspace_id}'")

            def _on_progress(p: dict):
                _jobs[workspace_id].update({
                    "vectors_indexed": total_vectors + p["vectors_indexed"],
                    "docs_processed": total_docs + p["docs_processed"],
                    "docs_skipped": total_skipped + p["docs_skipped"],
                    "docs_orphaned": total_orphaned + p["docs_orphaned"],
                })

            result = await engine.run_snapshot(current_scope, progress_callback=_on_progress)

            total_vectors += result["vectors_indexed"]
            total_docs += result["docs_processed"]
            total_skipped += result.get("docs_skipped", 0)
            total_orphaned += result.get("docs_orphaned", 0)
            all_errors.extend(result.get("errors", []))

            if not result.get("partial"):
                break

            # More docs remain — refresh token and re-fetch indexed state, then continue
            access_token = await get_valid_token(workspace_id, db)
            indexed_files = await db.get_indexed_files(workspace_id)
            current_scope = current_scope.model_copy(update={
                "google_access_token": access_token,
                "indexed_files": indexed_files,
            })

        _jobs[workspace_id] = {
            "status": "clustering",
            "vectors_indexed": total_vectors,
            "docs_processed": total_docs,
            "docs_skipped": total_skipped,
            "docs_orphaned": total_orphaned,
            "errors": all_errors,
            "iterations": iteration,
        }

        cluster_result = await clustering_engine.run_clustering(workspace_id)
        _jobs[workspace_id] = {
            "status": "done",
            "vectors_indexed": total_vectors,
            "docs_processed": total_docs,
            "docs_skipped": total_skipped,
            "docs_orphaned": total_orphaned,
            "errors": all_errors,
            "iterations": iteration,
            "topics_found": cluster_result.get("topics_found", 0),
            "clustering": cluster_result.get("status"),
        }
    except Exception as e:
        _jobs[workspace_id] = {"status": "failed", "error": str(e)}


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
async def run_snapshot(req: SnapshotRequest, background_tasks: BackgroundTasks):
    workspace_id = req.workspace_id

    if _jobs.get(workspace_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Snapshot already running for this workspace.")

    access_token = await get_valid_token(workspace_id, db)
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="No Google token found for this workspace. Complete OAuth first."
        )

    indexed_files = await db.get_indexed_files(workspace_id)

    scope = ScopeConfig(
        workspace_id=workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        cluster_instructions=req.cluster_instructions,
        google_access_token=access_token,
        indexed_files=indexed_files,
    )

    background_tasks.add_task(_run_snapshot_job, workspace_id, scope)
    return {"status": "started", "workspace_id": workspace_id}


@router.get("/snapshot/status/{workspace_id}")
async def snapshot_status(workspace_id: str):
    job = _jobs.get(workspace_id)
    if not job:
        return {"status": "not_started", "workspace_id": workspace_id}
    return {"workspace_id": workspace_id, **job}


_cluster_jobs: Dict[str, Dict[str, Any]] = {}


@router.post("/cluster/{workspace_id}")
async def run_clustering(workspace_id: str, background_tasks: BackgroundTasks):
    if _cluster_jobs.get(workspace_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Clustering already running for this workspace.")

    async def _do_cluster():
        _cluster_jobs[workspace_id] = {"status": "running"}
        try:
            result = await clustering_engine.run_clustering(workspace_id)
            _cluster_jobs[workspace_id] = result
        except Exception as e:
            _cluster_jobs[workspace_id] = {"status": "failed", "error": str(e)}

    background_tasks.add_task(_do_cluster)
    return {"status": "started", "workspace_id": workspace_id}


@router.get("/cluster/status/{workspace_id}")
async def cluster_status(workspace_id: str):
    job = _cluster_jobs.get(workspace_id)
    if not job:
        return {"status": "not_started", "workspace_id": workspace_id}
    return {"workspace_id": workspace_id, **job}


@router.get("/topics/{workspace_id}")
async def get_topics(workspace_id: str):
    topics = await db.get_topics(workspace_id)
    return {"workspace_id": workspace_id, "topics": topics}
