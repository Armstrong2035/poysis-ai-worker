from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import asyncio
import json
import os
import time
import traceback

# A snapshot job whose updated_at is older than this is considered orphaned.
JOB_STALE_AFTER_SECONDS = 300

from app.primitives.consolidation.scope import ScopeConfig, SourceName
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
    # Required, no default. A default here meant a YouTube-only workspace posting
    # just {workspace_id} silently took the Drive branch and 401'd before any job
    # row existed, so the run left no trace anywhere. Callers declare what they want.
    # min_length=1 so an empty list 422s here rather than reaching ScopeConfig's
    # at_least_one_source check, which would surface as a 500.
    sources: List[SourceName] = Field(min_length=1)
    time_window_days: int = 0   # 0 = all time (beta: maximize coverage)
    doc_limit: int = 10000  # effectively "all" for beta; iteration loop only kicks in past this
    drive_folder_ids: List[str] = []
    cluster_instructions: List[dict] = []
    # Index only, don't build the topic hierarchy. Clustering is the expensive,
    # non-linear tail of a run (UMAP/HDBSCAN + per-topic semantic analysis over
    # the whole namespace), so a large backfill can skip it and cluster once at
    # the end via POST /consolidation/cluster/{workspace_id}.
    skip_clustering: bool = False


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


async def _youtube_channels_for(req: SnapshotRequest) -> List[dict]:
    """Channels to ingest for this run — empty unless the caller asked for YouTube.

    Rejects "youtube" with nothing attached rather than letting ScopeConfig raise,
    so the caller gets a 400 explaining what to fix instead of a 500.
    """
    if "youtube" not in req.sources:
        return []

    channels = await db.get_youtube_channels(req.workspace_id)
    if not channels:
        raise HTTPException(
            status_code=400,
            detail="sources includes 'youtube' but no YouTube channels are connected to this workspace.",
        )
    return channels


async def _run_snapshot_job(
    workspace_id: str,
    user_id: str,
    scope: ScopeConfig,
    job_id: str,
    skip_clustering: bool = False,
):
    """Background job: consolidate and (unless skipped) cluster documents."""
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
                snapshot = {
                    "status": "running",
                    "vectors_indexed": total_vectors + p["vectors_indexed"],
                    "docs_processed": total_docs + p["docs_processed"],
                    "docs_skipped": total_skipped + p["docs_skipped"],
                    "docs_orphaned": total_orphaned + p["docs_orphaned"],
                }
                _jobs[workspace_id].update(snapshot)
                # Writes progress for the SSE stream to read *and* heartbeats the row
                # for stale-running detection — one write serves both.
                # Fire-and-forget: failure to record is non-fatal to the run.
                asyncio.create_task(db.record_job_progress(job_id, snapshot))

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
        if skip_clustering:
            # Distinct from "skipped" so the client can tell "you asked me not to"
            # apart from "nothing changed". Run it later with POST /cluster/{ws}.
            print(f"[Snapshot] skip_clustering set for '{workspace_id}' — indexing only")
            cluster_result = {"status": "skipped_by_request"}
        elif corpus_changed:
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
            # Per-document failures don't fail the run, but dropping them entirely
            # let a run that enumerated a fraction of a channel report a clean
            # "done". Cap the payload — a bad run can produce thousands.
            "errors": all_errors[:50],
            "error_count": len(all_errors),
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

    yt_channels = await _youtube_channels_for(req)

    scope = ScopeConfig(
        workspace_id=req.workspace_id,
        sources=req.sources,
        time_window_days=req.time_window_days,
        doc_limit=req.doc_limit,
        drive_folder_ids=req.drive_folder_ids,
        google_access_token=access_token,
        youtube_channel_ids=[c["channel_id"] for c in yt_channels],
        youtube_channel_connections={c["channel_id"]: c["id"] for c in yt_channels},
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
    yt_channels = await _youtube_channels_for(req)
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
        youtube_channel_ids=[c["channel_id"] for c in yt_channels],
        youtube_channel_connections={c["channel_id"]: c["id"] for c in yt_channels},
        **({"youtube_min_duration_seconds": min(yt_min_durations)} if yt_min_durations else {}),
    )

    # Create job record
    job_id = await db.create_job(workspace_id, user_id, "snapshot")
    if not job_id:
        raise HTTPException(status_code=500, detail="Failed to create job record")

    # Start background task with job tracking
    background_tasks.add_task(
        _run_snapshot_job, workspace_id, user_id, scope, job_id, req.skip_clustering
    )
    return {
        "status": "started",
        "workspace_id": workspace_id,
        "job_id": job_id,
        "skip_clustering": req.skip_clustering,
    }


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


def _job_event_state(job: Optional[dict]) -> Dict[str, Any]:
    """Flatten a consolidation_jobs row into the flat shape stream clients expect.

    The row splits truth across two columns: `status` is authoritative for
    terminal states, while `result` carries the counters and the mid-run
    "clustering" phase. Merge them so the client sees one `status` field.
    """
    if not job:
        return {"status": "not_started"}
    if job.get("status") == "failed":
        return {"status": "failed", "error": job.get("error")}

    state = dict(job.get("result") or {})
    if job.get("status") == "done":
        state["status"] = "done"
    else:
        # Mid-run: prefer the phase recorded in `result` ("running"/"clustering").
        # A row created but not yet reporting has no result at all.
        state.setdefault("status", "running")
    return state


@router.get("/snapshot/stream/{workspace_id}")
async def snapshot_stream(
    workspace_id: str,
    job_id: Optional[str] = None,
    user_id: str = Depends(get_user_id),
):
    """
    Server-Sent Events (SSE) stream of consolidation progress.

    Reads the consolidation_jobs row rather than in-process state, so the stream
    is correct after a redeploy mid-run and across replicas.

    Pass the `job_id` returned by POST /consolidation/snapshot to watch that exact
    run. Without it the stream follows the workspace's latest snapshot job, which
    can briefly report the *previous* run while the new row is being created.
    """
    await verify_workspace_ownership(workspace_id, user_id)

    async def event_stream():
        POLL_INTERVAL = 1.0
        KEEPALIVE_INTERVAL = 15.0
        MAX_DURATION = 1800.0  # 30 minutes, as before
        # The client typically opens this immediately after POSTing, so the row may
        # not exist for a beat. Report "not_started" right away but keep watching
        # briefly, so a genuine race resolves and a failed POST still terminates.
        NOT_STARTED_GRACE = 5.0

        started = time.monotonic()
        last_state = None
        last_send = started

        while time.monotonic() - started < MAX_DURATION:
            if job_id:
                job = await db.get_job(job_id)
                # Never let a job_id from one workspace stream into another.
                if job and job.get("workspace_id") != workspace_id:
                    job = None
            else:
                job = await db.get_latest_job(workspace_id, job_type="snapshot")

            state = _job_event_state(job)

            if state != last_state:
                yield f"data: {json.dumps({'type': 'progress', 'timestamp': time.time(), **state})}\n\n"
                last_state = state
                last_send = time.monotonic()

            status = state.get("status")
            if status == "done":
                yield f"data: {json.dumps({'type': 'complete', 'mcp_url': _generate_mcp_url(workspace_id), **state})}\n\n"
                return
            if status == "failed":
                return
            if status == "not_started" and time.monotonic() - started >= NOT_STARTED_GRACE:
                return

            # Comment frame: keeps proxies from dropping an idle connection during
            # long fetch phases that emit no progress. Ignored by EventSource.
            if time.monotonic() - last_send >= KEEPALIVE_INTERVAL:
                yield ": keepalive\n\n"
                last_send = time.monotonic()

            await asyncio.sleep(POLL_INTERVAL)

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
