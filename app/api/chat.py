from collections import defaultdict
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import os

from app.primitives.knowledge.engine import KnowledgeEngine
from app.api.security import get_user_id, verify_workspace_ownership

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    workspace_id: str
    query: str
    top_k: Optional[int] = 5
    min_score: Optional[float] = 0.4
    model: Optional[str] = None
    instructions: Optional[str] = None                  # system prompt from playground branding
    allowed_connection_ids: Optional[List[str]] = None  # connection-level scope, e.g. ["youtube"]
    allowed_topic_ids: Optional[List[int]] = None       # topic-level scope: owner-approved category_ids


def _diversify(chunks: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    """
    Round-robin pick from per-source buckets so no single video dominates.
    Chunks arrive sorted by score; each bucket preserves that order.
    """
    by_source: Dict[str, List] = defaultdict(list)
    for c in chunks:
        key = c.get("metadata", {}).get("source_id") or c["id"]
        by_source[key].append(c)

    buckets = list(by_source.values())
    result = []
    while len(result) < top_k and any(buckets):
        for bucket in buckets:
            if bucket and len(result) < top_k:
                result.append(bucket.pop(0))
        buckets = [b for b in buckets if b]
    return result


@router.post("")
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Workspace-scoped streaming chat endpoint.
    Retrieves from consolidation_{workspace_id} namespace and streams a grounded answer.
    Applies source diversity so results span multiple videos, not just the closest one.
    Response format: raw text tokens followed by \\n\\n__SOURCES__{json}.
    """
    await verify_workspace_ownership(request.workspace_id, user_id)

    engine = KnowledgeEngine()
    namespace = f"consolidation_{request.workspace_id}"

    async def generate():
        from llama_index.llms.google_genai import GoogleGenAI

        # Fetch a wide pool so diversity filtering has candidates from many sources
        candidates = await engine.fetch_raw(
            notebook_id=namespace,
            text=request.query,
            top_k=request.top_k * 6,
            source_types=request.allowed_connection_ids,
            topic_ids=request.allowed_topic_ids,
        )

        above_threshold = [c for c in candidates if c.get("score", 0) >= request.min_score]
        diverse = _diversify(above_threshold, request.top_k)

        if not diverse:
            yield "I couldn't find relevant information in your knowledge base to answer that question."
            yield f"\n\n__SOURCES__{json.dumps([])}"
            return

        context_parts = []
        for c in diverse:
            meta = c.get("metadata", {})
            label = meta.get("title") or meta.get("source_file") or "unknown"
            start_time = meta.get("start_time", "")
            header = f"[{label}" + (f" @ {start_time}" if start_time else "") + "]"
            context_parts.append(f"{header}\n{c['text']}")
        context = "\n\n---\n\n".join(context_parts)

        system = request.instructions or (
            "Answer the following question based solely on the provided context from the user's "
            "knowledge base. Be concise and direct. If the context doesn't contain enough "
            "information, say so."
        )

        prompt = (
            f"{system}\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {request.query}\n\n"
            "Answer:"
        )

        llm = GoogleGenAI(model=request.model or "gemini-2.0-flash", api_key=os.getenv("GEMINI_API_KEY"))
        streaming_response = await llm.astream_complete(prompt)
        async for delta in streaming_response:
            yield delta.delta

        sources = [
            {
                "title": c["metadata"].get("title"),
                "url": c["metadata"].get("url"),          # already includes ?t= deep-link
                "source_type": c["metadata"].get("source_type"),
                "source_id": c["metadata"].get("source_id"),
                "timestamp_start_ms": c["metadata"].get("timestamp_start_ms"),
                "timestamp_end_ms": c["metadata"].get("timestamp_end_ms"),
                "start_time": c["metadata"].get("start_time"),
                "score": round(c["score"], 4),
                "snippet": c["text"][:200] + ("..." if len(c["text"]) > 200 else ""),
            }
            for c in diverse
        ]
        yield f"\n\n__SOURCES__{json.dumps(sources)}"

    return StreamingResponse(generate(), media_type="text/event-stream")
