from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import tempfile
import shutil
from app.primitives.knowledge.engine import KnowledgeEngine

router = APIRouter(tags=["retrieval"])

class SearchRequest(BaseModel):
    query: str
    notebook_id: str
    limit: Optional[int] = 5
    min_score: Optional[float] = 0.5  # Block-level policy decision

@router.post("/search")
async def search_documents(request: SearchRequest):
    """
    Retrieval Block: Fetches semantically relevant chunks for RAG.
    Policy: Filter by min_score, return top N results, format for consumption.
    """
    try:
        engine = KnowledgeEngine()
        
        # Delegate raw fetch to the Engine (no opinions)
        raw_results = await engine.fetch_raw(
            notebook_id=request.notebook_id,
            text=request.query,
            top_k=request.limit * 2  # Fetch extra, then filter
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
async def ingest_documents(request: IngestRequest):
    """Endpoint for Next.js to push text chunks into the Knowledge Engine."""
    from app.blocks.retrieval.indexer import IndexerService
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
    file: UploadFile = File(...)
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
    
    # 2. Validate notebook_id
    if not notebook_id:
        raise HTTPException(status_code=400, detail="notebook_id is required.")

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

class AskRequest(BaseModel):
    notebook_id: str
    query: str
    stream: Optional[bool] = True

@router.post("/ask")
async def ask_question(request: AskRequest):
    """
    RAG Intelligence Block: Streams a synthesized answer from the documents.
    Returns a StreamingResponse — the client receives tokens as they arrive.
    """
    try:
        engine = KnowledgeEngine()
        
        if request.stream:
            # Streaming path — tokens arrive in ~1s
            return StreamingResponse(
                engine.stream_answer(request.notebook_id, request.query),
                media_type="text/event-stream"
            )
        else:
            # Non-streaming path — full answer returned as JSON
            result = await engine.answer_question(request.notebook_id, request.query)
            return result

    except Exception as e:
        print(f"[ASK ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))
