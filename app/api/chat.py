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

# If the primary model (below) is down, rate-limited, or otherwise unavailable, OpenRouter
# retries these in order before failing the request.
_FALLBACK_MODELS = ["deepseek/deepseek-v4-flash"]

# Client sends a tier name, not a raw model ID — keeps model choice out of the
# client and lets us swap the underlying model per tier without a client release.
_TIER_MODELS = {
    "quick": "google/gemini-3.5-flash",
    "thinking": "anthropic/claude-sonnet-5",
    "expert": "anthropic/claude-opus-4-8",
}
_DEFAULT_TIER = "quick"


class ChatRequest(BaseModel):
    workspace_id: str
    query: str
    top_k: Optional[int] = 8            # synthesis across sources needs more material than lookup does
    min_score: Optional[float] = 0.4
    model: Optional[str] = None  # tier name: "quick" | "thinking" | "expert"
    temperature: Optional[float] = 0.4                  # grounded, but high enough that the model synthesizes instead of defaulting to "not enough information"
    max_tokens: Optional[int] = 2048               # OpenRouter checks credit against a model's max possible output, not actual usage — leaving this unset causes spurious 402s on models with large output ceilings (e.g. Gemini 2.5 Flash's 65535)
    instructions: Optional[str] = None                  # system prompt from playground branding
    allowed_connection_ids: Optional[List[str]] = None  # connection-level scope, e.g. ["youtube"]
    allowed_topic_ids: Optional[List[int]] = None       # topic-level scope: owner-approved category_ids


# Deliberately permits inference across excerpts. An extraction-style prompt
# ("answer solely from the context, else say so") makes the model refuse any question
# whose answer isn't stated verbatim — which is most real questions, since chunks are
# excerpts from longer talks and documents.
_SYNTHESIS_CONTRACT = (
    "You are answering from the user's knowledge base. The context below is "
    "excerpted from longer talks and documents.\n\n"
    "Synthesize across the excerpts. The answer will rarely be stated outright in "
    "any single passage — draw out the principles, connect what different sources "
    "say, and build a coherent response from them. That is the job, not a liberty.\n\n"
    "Ground every claim in the context. Don't import outside knowledge or invent "
    "specifics. Where sources differ, say so. Only decline if the context is "
    "genuinely unrelated to the question — not merely because it lacks a direct "
    "statement."
)


def _build_system_prompt(instructions: Optional[str]) -> str:
    """
    Layer a bot's branding prompt on top of the synthesis contract.

    `instructions` used to REPLACE the system prompt, which meant every bot shipping
    a persona ("you are a warm guide to X's work") silently dropped the grounding and
    synthesis rules and reverted to the model's default hedging. Each bot author then
    had to rediscover the same prompt engineering. The contract is the platform's
    retrieval behavior, not a default to be overwritten — so persona is appended to it
    and the contract is restated last, where it binds hardest.
    """
    if not instructions or not instructions.strip():
        return _SYNTHESIS_CONTRACT

    return (
        f"{_SYNTHESIS_CONTRACT}\n\n"
        "---\n\n"
        f"Voice and role for this assistant:\n{instructions.strip()}\n\n"
        "---\n\n"
        "The voice and role above govern how you sound and what you focus on. They do "
        "not relax the grounding rules: stay within the provided context, synthesize "
        "across it rather than declining for lack of a verbatim answer, and never "
        "invent specifics to stay in character."
    )


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
    On failure mid-stream, emits \\n\\n__ERROR__{json} instead of dropping the connection silently.
    """
    await verify_workspace_ownership(request.workspace_id, user_id)

    engine = KnowledgeEngine()
    namespace = f"consolidation_{request.workspace_id}"

    async def generate():
        from llama_index.llms.openai_like import OpenAILike

        try:
            # Fetch a wide pool so diversity filtering has candidates from many sources
            candidates = await engine.fetch_raw(
                notebook_id=namespace,
                text=request.query,
                top_k=request.top_k * 6,
                source_types=request.allowed_connection_ids,
                topic_ids=request.allowed_topic_ids,
            )

            above_threshold = [c for c in candidates if c.get("score", 0) >= request.min_score]
            # min_score is the floor (guards the "nothing relevant" case below); the gap
            # detector then trims to the natural relevance cliff within what clears it.
            gapped = engine.vector_service.detect_score_gap(above_threshold, min_results=request.top_k)
            diverse = _diversify(gapped, request.top_k)

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

            system = _build_system_prompt(request.instructions)

            prompt = (
                f"{system}\n\n"
                f"Context:\n{context}\n\n"
                f"Question: {request.query}\n\n"
                "Answer:"
            )

            llm = OpenAILike(
                model=_TIER_MODELS.get(request.model, _TIER_MODELS[_DEFAULT_TIER]),
                api_key=os.getenv("OPENROUTER_API_KEY"),
                api_base="https://openrouter.ai/api/v1",
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                is_chat_model=True,
                additional_kwargs={"extra_body": {"models": _FALLBACK_MODELS}},
            )
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

        except Exception as e:
            print(f"[CHAT] generation failed for workspace={request.workspace_id}: {e}")
            yield f"\n\n__ERROR__{json.dumps({'message': 'Something went wrong generating a response. Please try again.'})}"

    return StreamingResponse(generate(), media_type="text/event-stream")
