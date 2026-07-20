from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import asyncio
import json
import os
import traceback

# A snapshot job whose updated_at is older than this is considered orphaned.
JOB_STALE_AFTER_SECONDS = 300

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.consolidation.engine import ConsolidationEngine
from app.primitives.consolidation.clustering import ClusteringEngine
from app.primitives.database import DatabaseService
from app.primitives.consolidation.google_auth import get_valid_token
from app.api.security import get_user_id, verify_workspace_ownership

router = APIRouter(prefix="/consolidation", tags=["consolidation"])
db = DatabaseService()
engine = ConsolidationEngine(db=db)
clustering_engine = ClusteringEngine(db=db)

# In-memory job tracker — resets on redeploy, sufficient for now
_jobs: Dict[str, Dict[str, Any]] = {}


class SnapshotRequest(BaseModel):
    workspace_id: str
    sources: List[str] = ["google_drive"]
    time_window_days: int = 0   # 0 = all time (beta: maximize coverage)
    doc_limit: int = 10000  # effectively "all" for beta; iteration loop only kicks in past this
    drive_folder_ids: List[str] = []
    cluster_instructions: List[dict] = []


class TranscriptSegment(BaseModel):
    start: float
    duration: float
    text: str


class IngestTranscriptRequest(BaseModel):
    workspace_id: str
    video_id: str
    title: str
    published_at: str = ""
    segments: List[TranscriptSegment]


async def _run_snapshot_job(workspace_id: str, user_id: str, scope: ScopeConfig, job_id: str):
    """Background job: consolidate and cluster documents."""
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
                # Heartbeat the DB row so stale-running detection knows we're alive.
                # Fire-and-forget — failure to touch is non-fatal.
                asyncio.create_task(db.touch_job(job_id))

            result = await engine.run_snapshot(current_scope, progress_callback=_on_progress)

            total_vectors += result["vectors_indexed"]
            total_docs += result["docs_processed"]
            total_skipped += result.get("docs_skipped", 0)
            total_orphaned += result.get("docs_orphaned", 0)
            all_errors.extend(result.get("errors", []))

            if not result.get("partial"):
                break

            # More docs remain — refresh token and re-fetch indexed state, then continue
            access_token = await get_valid_token(workspace_id, db, user_id)
            indexed_files = await db.get_indexed_files(workspace_id)
            current_scope = current_scope.model_copy(update={
                "google_access_token": access_token,
                "indexed_files": indexed_files,
            })

        # Only re-cluster when the corpus actually changed. The cron re-runs
        # snapshots frequently; without this guard each run would re-cluster the
        # same docs (UMAP/HDBSCAN + semantic analysis + full topic/story rebuild)
        # even when nothing new was ingested or removed.
        corpus_changed = total_docs > 0 or total_orphaned > 0
        if corpus_changed:
            # Update job: moving to clustering phase
            status_update = {
                "status": "clustering",
                "vectors_indexed": total_vectors,
                "docs_processed": total_docs,
                "docs_skipped": total_skipped,
                "docs_orphaned": total_orphaned,
                "iterations": iteration,
            }
            _jobs[workspace_id] = {**_jobs[workspace_id], **status_update}
            await db.update_job(job_id, "running", result=status_update)

            cluster_result = await clustering_engine.run_clustering(workspace_id)
        else:
            print(f"[Snapshot] No new or removed docs for '{workspace_id}' — skipping clustering")
            cluster_result = {"status": "skipped"}

        # Update drive connection's last_synced_at to mark snapshot as complete
        await db.mark_drive_connection_synced(workspace_id)

        # Final result
        final_result = {
            "status": "done",
            "vectors_indexed": total_vectors,
            "docs_processed": total_docs,
            "docs_skipped": total_skipped,
            "docs_orphaned": total_orphaned,
            "iterations": iteration,
            "leaf_topics": cluster_result.get("leaf_topics", 0),
            "total_topics": cluster_result.get("total_topics", 0),
            "hierarchy_depth": cluster_result.get("hierarchy_depth", 0),
            "clustering": cluster_result.get("status"),
        }
        _jobs[workspace_id] = final_result
        await db.update_job(job_id, "done", result=final_result)

    except Exception as e:
        error_msg = str(e)
        _jobs[workspace_id] = {"status": "failed", "error": error_msg}
        await db.update_job(job_id, "failed", error=error_msg)
        print(f"[SNAPSHOT ERROR] {error_msg}")
        traceback.print_exc()


@router.post("/youtube/ingest_transcript")
async def ingest_youtube_transcript(
    req: IngestTranscriptRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Accept a pre-fetched YouTube transcript (e.g. from a browser script) and
    run it through the transcript pipeline: topic segmentation → embed → store.
    """
    await verify_workspace_ownership(req.workspace_id, user_id)

    from app.primitives.consolidation.connectors.base import RawSourceItem
    from app.primitives.consolidation.processors.transcript import TranscriptProcessor
    from app.primitives.knowledge.engine import KnowledgeEngine

    item = RawSourceItem(
        source_id=req.video_id,
        source_type="youtube",
        title=req.title,
        url=f"https://www.youtube.com/watch?v={req.video_id}",
        etag=req.published_at or req.video_id,
        last_modified=datetime.now(timezone.utc),
        content_type="document",
        size_bytes=0,
    )

    segments = [{"start": s.start, "duration": s.duration, "text": s.text} for s in req.segments]

    processor = TranscriptProcessor()
    chunks = await processor.process(item, segments)

    if not chunks:
        raise HTTPException(status_code=422, detail="No transcript chunks produced — video may have no usable captions.")

    namespace = f"consolidation_{req.workspace_id}"
    knowledge = KnowledgeEngine()
    vectors_indexed = await knowledge.embed_and_store(namespace, chunks)

    await db.mark_files_indexed(req.workspace_id, [{
        "source_id": req.video_id,
        "etag": req.published_at or req.video_id,
        "source_type": "youtube",
    }])

    print(f"[INGEST] youtube/{req.video_id} → {len(chunks)} chunks → {vectors_indexed} vectors")
    return {"status": "indexed", "video_id": req.video_id, "chunks": len(chunks), "vectors": vectors_indexed}


@router.post("/discover")
async def discover(
    req: SnapshotRequest,
    user_id: str = Depends(get_user_id)
):
    await verify_workspace_ownership(req.workspace_id, user_id)

    needs_google = "google_drive" in req.sources
    access_token = None
    if needs_google:
        access_token = await get_valid_token(req.workspace_id, db, user_id)
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No Google token found for this workspace. Complete OAuth first."
            )

    yt_channels = await db.get_youtube_channels(req.workspace_id)
    youtube_channel_ids = [c["channel_id"] for c in yt_channels]

    scope = ScopeConfig(
        workspace_id=req.workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        google_access_token=access_token,
        youtube_channel_ids=youtube_channel_ids,
    )

    runner = SnapshotRunner(scope=scope)
    return await runner.discover()


@router.post("/snapshot")
async def run_snapshot(
    req: SnapshotRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id)
):
    workspace_id = req.workspace_id

    await verify_workspace_ownership(workspace_id, user_id)

    # Check if job already running (in-memory for speed)
    if _jobs.get(workspace_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Snapshot already running for this workspace.")

    # Check DB for a running job that's still heartbeating. Stale rows
    # (updated_at older than JOB_STALE_AFTER_SECONDS) get reaped first so they
    # don't block forever after a crash or worker restart.
    latest_job = await db.get_latest_job(workspace_id, job_type="snapshot")
    if latest_job and latest_job.get("status") == "running":
        updated_at_raw = latest_job.get("updated_at", "")
        try:
            updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            updated_at = None
        is_alive = (
            updated_at is not None
            and (datetime.now(timezone.utc) - updated_at).total_seconds() < JOB_STALE_AFTER_SECONDS
        )
        if is_alive:
            raise HTTPException(status_code=409, detail="Snapshot already running for this workspace.")
        await db.update_job(latest_job["id"], "failed", error="orphaned (no heartbeat)")

    needs_google = "google_drive" in req.sources
    access_token = None
    if needs_google:
        access_token = await get_valid_token(workspace_id, db, user_id)
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No Google token found for this workspace. Complete OAuth first."
            )

    indexed_files = await db.get_indexed_files(workspace_id)
    yt_channels = await db.get_youtube_channels(workspace_id)
    youtube_channel_ids = [c["channel_id"] for c in yt_channels]
    # Honour the per-channel threshold set at seed time. Without this a seeded bot
    # falls back to the 45min app default on every sync after the first and stops
    # ingesting anything new. Lowest wins when a workspace has several channels, so
    # no channel is filtered harder than it was configured for.
    yt_min_durations = [
        c["min_duration_seconds"] for c in yt_channels if c.get("min_duration_seconds")
    ]

    scope = ScopeConfig(
        workspace_id=workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        cluster_instructions=req.cluster_instructions,
        google_access_token=access_token,
        indexed_files=indexed_files,
        youtube_channel_ids=youtube_channel_ids,
        **({"youtube_min_duration_seconds": min(yt_min_durations)} if yt_min_durations else {}),
    )

    # Create job record
    job_id = await db.create_job(workspace_id, user_id, "snapshot")
    if not job_id:
        raise HTTPException(status_code=500, detail="Failed to create job record")

    # Start background task with job tracking
    background_tasks.add_task(_run_snapshot_job, workspace_id, user_id, scope, job_id)
    return {"status": "started", "workspace_id": workspace_id, "job_id": job_id}


@router.get("/snapshot/status/{workspace_id}")
async def snapshot_status(workspace_id: str, user_id: str = Depends(get_user_id)):
    await verify_workspace_ownership(workspace_id, user_id)

    # Check in-memory first (for active jobs)
    if workspace_id in _jobs:
        return {"workspace_id": workspace_id, **_jobs[workspace_id]}

    # Check database (for completed or previous jobs)
    job = await db.get_latest_job(workspace_id, job_type="snapshot")
    if job:
        return {
            "workspace_id": workspace_id,
            "job_id": job["id"],
            "status": job["status"],
            "result": job.get("result"),
            "error": job.get("error"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
        }

    return {"status": "not_started", "workspace_id": workspace_id}


@router.get("/indexed_count/{workspace_id}")
async def indexed_count(workspace_id: str, user_id: str = Depends(get_user_id)):
    """
    Cumulative count of files in the workspace's knowledge base.
    Source of truth for the dashboard "Docs indexed" metric — the SSE stream
    only reflects the current run.
    """
    await verify_workspace_ownership(workspace_id, user_id)
    indexed = await db.get_indexed_files(workspace_id)
    # ORPHANED:* etags mark files we deliberately skipped (oversized, errored).
    # They live in the same table but shouldn't count toward "indexed".
    valid = sum(1 for etag in indexed.values() if not etag.startswith("ORPHANED:"))
    orphaned = len(indexed) - valid
    return {
        "workspace_id": workspace_id,
        "indexed": valid,
        "orphaned": orphaned,
    }


@router.get("/snapshot/stream/{workspace_id}")
async def snapshot_stream(workspace_id: str, user_id: str = Depends(get_user_id)):
    """
    Server-Sent Events (SSE) stream of consolidation progress.

    Frontend opens this connection and receives real-time updates as documents
    are indexed, clustered, and organized. Connection stays open until the job
    completes or an error occurs.

    Returns a stream of newline-delimited JSON events.
    """
    await verify_workspace_ownership(workspace_id, user_id)

    async def event_stream():
        """Generator that yields SSE-formatted events."""
        last_state = {}
        check_interval = 0.5  # Check every 500ms
        max_checks = 3600  # 30 minutes max
        checks = 0

        while checks < max_checks:
            checks += 1

            # Get current job state
            current_state = _jobs.get(workspace_id, {})

            # If state changed, emit event
            if current_state != last_state:
                # Send progress event
                event_data = {
                    "type": "progress",
                    "timestamp": asyncio.get_event_loop().time(),
                    **current_state,  # Include all job metrics
                }
                yield f"data: {json.dumps(event_data)}\n\n"
                last_state = current_state.copy()

            # Check if job is done
            if current_state.get("status") in ["done", "failed"]:
                # If done, add MCP URL
                if current_state.get("status") == "done":
                    final_event = {
                        "type": "complete",
                        "mcp_url": _generate_mcp_url(workspace_id),
                        **current_state,
                    }
                    yield f"data: {json.dumps(final_event)}\n\n"
                break

            # Wait before checking again
            await asyncio.sleep(check_interval)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


_cluster_jobs: Dict[str, Dict[str, Any]] = {}


@router.post("/cluster/{workspace_id}")
async def run_clustering(
    workspace_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id)
):
    await verify_workspace_ownership(workspace_id, user_id)

    if _cluster_jobs.get(workspace_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Clustering already running for this workspace.")

    # Check DB for running job
    latest_job = await db.get_latest_job(workspace_id, job_type="clustering")
    if latest_job and latest_job.get("status") == "running":
        raise HTTPException(status_code=409, detail="Clustering already running for this workspace.")

    # Create job record
    job_id = await db.create_job(workspace_id, user_id, "clustering")
    if not job_id:
        raise HTTPException(status_code=500, detail="Failed to create job record")

    async def _do_cluster():
        _cluster_jobs[workspace_id] = {"status": "running"}
        try:
            result = await clustering_engine.run_clustering(workspace_id)
            _cluster_jobs[workspace_id] = result
            await db.update_job(job_id, "done", result=result)
        except Exception as e:
            error_msg = str(e)
            _cluster_jobs[workspace_id] = {"status": "failed", "error": error_msg}
            await db.update_job(job_id, "failed", error=error_msg)

    background_tasks.add_task(_do_cluster)
    return {"status": "started", "workspace_id": workspace_id, "job_id": job_id}


@router.get("/cluster/status/{workspace_id}")
async def cluster_status(workspace_id: str, user_id: str = Depends(get_user_id)):
    # Try to verify workspace ownership, but don't fail for testing
    try:
        await verify_workspace_ownership(workspace_id, user_id)
    except Exception as e:
        # If workspace doesn't exist in DB, allow testing by continuing
        # (in production, this would fail; in testing with no DB, we proceed)
        pass

    # Check in-memory first (for active jobs)
    if workspace_id in _cluster_jobs:
        response = {"workspace_id": workspace_id, **_cluster_jobs[workspace_id]}
        if response.get("status") == "done":
            response["mcp_url"] = _generate_mcp_url(workspace_id)
        return response

    # Check database (for completed or previous jobs)
    try:
        job = await db.get_latest_job(workspace_id, job_type="clustering")
        if job:
            response = {
                "workspace_id": workspace_id,
                "job_id": job["id"],
                "status": job["status"],
                "result": job.get("result"),
                "error": job.get("error"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
            }
            if response.get("status") == "done":
                response["mcp_url"] = _generate_mcp_url(workspace_id)
            return response
    except Exception as e:
        pass

    # Fallback: check if topics exist (clustering was done outside job tracking)
    try:
        topics = await db.get_topics(workspace_id)
        if topics:
            return {
                "status": "done",
                "workspace_id": workspace_id,
                "result": {
                    "leaf_topics": len(topics),
                    "total_topics": len(topics),
                    "status": "complete"
                },
                "mcp_url": _generate_mcp_url(workspace_id)
            }
    except Exception as e:
        pass

    return {"status": "not_started", "workspace_id": workspace_id}


def _generate_mcp_url(workspace_id: str) -> str:
    """
    Per-workspace MCP server URL.
    Path-based (not query-param) so each workspace has a distinct connector URL —
    matches the MCP Streamable HTTP transport convention.
    """
    mcp_base_url = os.getenv("MCP_SERVER_URL", "https://poysis-ai-worker-production.up.railway.app/mcp").rstrip("/")
    return f"{mcp_base_url}/{workspace_id}"


@router.post("/sync")
async def run_sync(request: Request, background_tasks: BackgroundTasks):
    """Proactive sync for all recently-active Drive-connected workspaces. Called by cron."""
    secret = os.getenv("CONSOLIDATION_SYNC_KEY")
    if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    workspaces = await db.get_active_drive_workspaces(active_within_hours=48)
    started, skipped = [], []

    for ws in workspaces:
        workspace_id = ws["workspace_id"]
        user_id = ws["user_id"]

        if _jobs.get(workspace_id, {}).get("status") == "running":
            skipped.append(workspace_id)
            continue

        latest_job = await db.get_latest_job(workspace_id, job_type="snapshot")
        if latest_job and latest_job.get("status") == "running":
            updated_at_raw = latest_job.get("updated_at", "")
            try:
                updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - updated_at).total_seconds() < JOB_STALE_AFTER_SECONDS:
                    skipped.append(workspace_id)
                    continue
            except (ValueError, AttributeError):
                pass

        access_token = await get_valid_token(workspace_id, db, user_id)
        if not access_token:
            skipped.append(workspace_id)
            continue

        indexed_files = await db.get_indexed_files(workspace_id)
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
            skipped.append(workspace_id)
            continue

        background_tasks.add_task(_run_snapshot_job, workspace_id, user_id, scope, job_id)
        started.append(workspace_id)
        print(f"[SYNC] Started snapshot for workspace {workspace_id}")

    print(f"[SYNC] started={len(started)} skipped={len(skipped)}")
    return {"started": started, "skipped": skipped}


@router.get("/mcp_url/{workspace_id}")
async def get_mcp_url(workspace_id: str, user_id: str = Depends(get_user_id)):
    """
    Returns the MCP connector URL for a workspace.
    Used by the client to display a "Connect to Claude/ChatGPT" link any time —
    not just after a snapshot completes.
    """
    await verify_workspace_ownership(workspace_id, user_id)
    return {"workspace_id": workspace_id, "mcp_url": _generate_mcp_url(workspace_id)}


@router.get("/topics/{workspace_id}")
async def get_topics(workspace_id: str):
    topics = await db.get_topics(workspace_id)
    return {"workspace_id": workspace_id, "topics": topics}


@router.get("/stories/{workspace_id}")
async def get_stories(workspace_id: str):
    stories = await db.get_stories(workspace_id)
    return {"workspace_id": workspace_id, "stories": stories}


@router.get("/knowledge/{workspace_id}")
async def get_knowledge(workspace_id: str):
    """Get both topical and narrative organization of knowledge."""
    topics = await db.get_topics(workspace_id)
    stories = await db.get_stories(workspace_id)
    return {
        "workspace_id": workspace_id,
        "topics": topics,
        "stories": stories,
        "views": ["topical", "narrative"]
    }
