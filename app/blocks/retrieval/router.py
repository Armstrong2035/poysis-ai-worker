from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import tempfile
import shutil
from app.primitives.knowledge.engine import KnowledgeEngine
from app.primitives.knowledge.vector_store import VectorService
from app.primitives.knowledge.embedder import Embedder
from app.api.security import get_user_id, verify_workspace_ownership

router = APIRouter(tags=["retrieval"])

_CONSOLIDATION_PREFIX = "consolidation_"


async def _authorize_notebook(notebook_id: str, user_id: str) -> str:
    """
    Authorize a raw-namespace endpoint.

    These endpoints address vectors by namespace (`notebook_id`) rather than by
    workspace_id, so there's no workspace to hand verify_workspace_ownership
    directly. Every namespace consolidation writes is `consolidation_{workspace_id}`,
    so the workspace is derived from that prefix. A notebook_id that doesn't match
    the convention has no owner to check against and is refused rather than allowed
    — otherwise an arbitrary string would read or write an unguarded namespace.
    """
    if not notebook_id.startswith(_CONSOLIDATION_PREFIX):
        raise HTTPException(status_code=403, detail="Invalid notebook_id.")

    workspace_id = notebook_id[len(_CONSOLIDATION_PREFIX):]
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Invalid notebook_id.")
    await verify_workspace_ownership(workspace_id, user_id)
    return workspace_id


class SearchRequest(BaseModel):
    query: str
    notebook_id: str
    limit: Optional[int] = 5
    min_score: Optional[float] = 0.5  # Block-level policy decision
    topic_id: Optional[int] = None

@router.post("/search")
async def search_documents(
    request: SearchRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Retrieval Block: Fetches semantically relevant chunks for RAG.
    Policy: Filter by min_score, return top N results, format for consumption.
    Optional topic_id narrows Pinecone search to BERTopic-enriched chunks.
    """
    # Outside the try: the bare `except Exception` below would turn a 401/403 into a 500.
    await _authorize_notebook(request.notebook_id, user_id)

    try:
        engine = KnowledgeEngine()
        
        # Delegate raw fetch to the Engine (no opinions)
        raw_results = await engine.fetch_raw(
            notebook_id=request.notebook_id,
            text=request.query,
            top_k=request.limit * 2,  # Fetch extra, then filter
            topic_id=request.topic_id,
        )
        
        # Apply block-level policy: filter by minimum score
        filtered = [r for r in raw_results if r.get("score", 0) >= request.min_score]
        
        # Apply limit
        top_results = filtered[:request.limit]
        
        # Format for the Next.js consumer
        results = [
            {
                "id": r["id"],
                "score": round(r["score"], 4),
                "text": r.get("text") or r.get("metadata", {}).get("text"),
                "metadata": r.get("metadata", {})
            }
            for r in top_results
        ]
        
        return {"results": results, "total_candidates": len(raw_results)}

    except Exception as e:
        print(f"[RETRIEVAL ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

class IngestRequest(BaseModel):
    notebook_id: str
    documents: List[Dict[str, Any]]

@router.post("/ingest")
async def ingest_documents(
    request: IngestRequest,
    user_id: str = Depends(get_user_id),
):
    """Endpoint for Next.js to push text chunks into the Knowledge Engine."""
    from app.blocks.retrieval.indexer import IndexerService

    # Outside the try: the bare `except Exception` below would turn a 401/403 into a 500.
    await _authorize_notebook(request.notebook_id, user_id)

    try:
        indexer = IndexerService()
        count = await indexer.ingest_documents(
            notebook_id=request.notebook_id,
            documents=request.documents
        )
        return {"message": "Ingestion complete", "count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".txt", ".docx"}

@router.post("/ingest-file")
async def ingest_file(
    notebook_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
):
    """
    File Ingestion Endpoint: Accepts a multipart file upload and indexes it
    into the notebook's knowledge index using LlamaParse + LlamaIndex pipeline.
    Supported: PDF, Excel (.xlsx/.xls), CSV, TXT, DOCX.
    """
    # 1. Validate file type
    _, ext = os.path.splitext(file.filename or "")
    ext = ext.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    
    # 2. Authorize the target namespace before doing any work (parsing, embedding,
    #    writing). Also subsumes the empty-notebook_id check — "" fails the prefix test.
    await _authorize_notebook(notebook_id, user_id)

    # 3. Save to a secure temp file (preserving the original extension)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(file.file, tmp)

        print(f"[INGEST-FILE] Received '{file.filename}' ({file.content_type}) for notebook '{notebook_id}'")

        # 4. Run the Ingestion Pipeline
        engine = KnowledgeEngine()
        count = await engine.ingest_file(notebook_id, tmp_path)

        return {
            "message": "File ingestion complete",
            "filename": file.filename,
            "notebook_id": notebook_id,
            "vectors_indexed": count
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[INGEST-FILE ERROR] {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 5. Always clean up the temp file
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
            print(f"[INGEST-FILE] Temp file cleaned up: {tmp_path}")

# ============================================================================
# Workspace-level Knowledge API (for MCP + external clients)
# ============================================================================

class QueryKnowledgeRequest(BaseModel):
    workspace_id: str
    query: str
    top_k: Optional[int] = 5
    min_score: Optional[float] = 0.5


@router.post("/query_knowledge_base")
async def query_knowledge_base(
    request: QueryKnowledgeRequest,
    user_id: str = Depends(get_user_id)
):
    """
    Query user's consolidated knowledge base.
    Returns matching results with sources (title, url, source_type).

    Used by: MCP clients, Claude, ChatGPT, etc.
    """
    try:
        await verify_workspace_ownership(request.workspace_id, user_id)

        # Use the SAME embedder + namespace that consolidation wrote with.
        # Embedder mismatch silently returns garbage scores; namespace mismatch returns nothing.
        engine = KnowledgeEngine()
        namespace = f"consolidation_{request.workspace_id}"

        raw_results = await engine.fetch_raw(
            notebook_id=namespace,
            text=request.query,
            top_k=request.top_k * 2,
        )

        # Filter by min_score
        filtered = [r for r in raw_results if r.get("score", 0) >= request.min_score]
        top_results = filtered[:request.top_k]

        # Format results with sources
        results = []
        for r in top_results:
            metadata = r.get("metadata", {})
            results.append({
                "id": r["id"],
                "score": round(r["score"], 4),
                "text": metadata.get("_text", ""),
                "source": {
                    "title": metadata.get("title"),
                    "url": metadata.get("url"),
                    "source_type": metadata.get("source_type"),
                    "source_id": metadata.get("source_id"),
                }
            })

        return {
            "query": request.query,
            "results": results,
            "total": len(results),
            "workspace_id": request.workspace_id
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[QUERY_KNOWLEDGE ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list_documents")
async def list_documents(
    workspace_id: str,
    search: Optional[str] = None,
    source_type: Optional[str] = None,
    topic_id: Optional[str] = None,
    user_id: str = Depends(get_user_id)
):
    """
    Search documents in user's knowledge base by name or source type.
    Returns document metadata: title, url, source_type, snippet.

    Parameters:
    - workspace_id: required
    - search: optional, filter by document title (case-insensitive)
    - source_type: optional, filter by source (e.g., "google_drive", "notion")
    - topic_id: optional, narrow to documents in a specific BERTopic cluster

    Used by: MCP clients, Claude, ChatGPT, etc. Also backs cluster-level
    "which documents are in here" browsing in the client (Knowledge Map /
    Canvas), via topic_id.
    """
    try:
        await verify_workspace_ownership(workspace_id, user_id)

        # Must match the namespace consolidation actually wrote with (see the
        # identical note on /search above) — this endpoint was previously
        # querying namespace=workspace_id directly, which is never populated;
        # every real chunk lives under "consolidation_{workspace_id}".
        vector_service = VectorService()
        all_docs = vector_service.list_documents_with_snippets(
            namespace=f"consolidation_{workspace_id}",
            snippet_words=200,
            topic_id=topic_id
        )

        # Filter by search term (case-insensitive)
        if search:
            search_lower = search.lower()
            all_docs = [d for d in all_docs if search_lower in d.get("title", "").lower()]

        # Filter by source_type
        if source_type:
            all_docs = [d for d in all_docs if d.get("source_type") == source_type]

        return {
            "workspace_id": workspace_id,
            "search_query": search,
            "source_filter": source_type,
            "documents": all_docs,
            "total": len(all_docs)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[LIST_DOCUMENTS ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))
