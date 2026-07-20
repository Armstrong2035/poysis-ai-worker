from collections import defaultdict
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import json
import os
import re
from app.primitives.knowledge.engine import KnowledgeEngine
from app.primitives.database import DatabaseService
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
    creator_name: Optional[str] = None                  # author of the body of work, for voice ("Across his talks, X…")
    allowed_connection_ids: Optional[List[str]] = None  # connection-level scope, e.g. ["youtube"]
    allowed_topic_ids: Optional[List[int]] = None       # topic-level scope: owner-approved category_ids


# Deliberately permits inference across excerpts. An extraction-style prompt
# ("answer solely from the context, else say so") makes the model refuse any question
# whose answer isn't stated verbatim — which is most real questions, since chunks are
# excerpts from longer talks and documents.
def _clean_creator_name(creator_name: Optional[str]) -> Optional[str]:
    """
    The client sends a bot's display label (e.g. "Emmanuel Iren Live Notebook").
    Strip the app-shell words so the voice never says "… Notebook's point is" — the
    prompt still tells the model to prefer the person's natural name from the excerpts.
    """
    if not creator_name or not creator_name.strip():
        return None
    name = creator_name.strip()
    for suffix in (" Notebook", " Playground", " Bot", " Live", " Official"):
        while name.lower().endswith(suffix.lower()):
            name = name[: -len(suffix)].strip()
    return name or None


def _synthesis_contract(creator_name: Optional[str]) -> str:
    """
    The platform's retrieval-behavior contract, voiced as a warm *interpreter of a
    body of work* rather than a search engine. When we know whose work it is, the
    answer speaks with that person at its center ("a theme he returns to is…") instead
    of "the documents say" — that framing is what makes a notebook feel like the creator.
    """
    who = _clean_creator_name(creator_name)
    subject = f"{who}'s body of work" if who else "a single creator's body of work"
    speaker = who or "the creator"

    return (
        f"You are the interpreter of {subject} — a distinct collection of talks and "
        "writing, not a general search engine. The context below is excerpted from it.\n\n"
        "VOICE: warm, direct, and human — like a knowledgeable friend introducing you to "
        f"{speaker}'s thinking, not an academic abstract. Refer to the creator by their "
        "natural name as it appears in the excerpts (prefer the plain personal name, e.g. "
        f"\"{speaker}\", never a product label), or with the pronouns the material makes "
        "clear. NEVER call this a 'notebook' or repeat a display label — that breaks the "
        "spell. When it fits, open with the single central idea the material keeps returning "
        "to, then let the rest flow from it.\n\n"
        f"Put {speaker} at the center: 'He teaches…', '{speaker} keeps returning to…', "
        "'Across these teachings…' — not 'the documents say' or 'the context mentions.' "
        "Synthesize across the excerpts: draw out the recurring principles, connect what "
        "different passages say, and build one coherent view. The answer is rarely stated "
        "outright in a single passage — that synthesis is the job, not a liberty.\n\n"
        "Ground every claim in the context. Don't import outside knowledge or invent "
        "specifics — that includes how MUCH was said: never fabricate quantities like "
        "'dozens of sermons.' Speak to what's actually in the excerpts. Refer to sources "
        "by their titles when it helps the reader place an idea. Where sources differ, "
        "say so. Only decline if the context is genuinely unrelated to the question — not "
        "merely because it lacks a verbatim statement."
    )


def _build_system_prompt(instructions: Optional[str], creator_name: Optional[str] = None) -> str:
    """
    Layer a bot's branding prompt on top of the synthesis contract.

    `instructions` used to REPLACE the system prompt, which meant every bot shipping
    a persona ("you are a warm guide to X's work") silently dropped the grounding and
    synthesis rules and reverted to the model's default hedging. Each bot author then
    had to rediscover the same prompt engineering. The contract is the platform's
    retrieval behavior, not a default to be overwritten — so persona is appended to it
    and the contract is restated last, where it binds hardest.
    """
    contract = _synthesis_contract(creator_name)
    if not instructions or not instructions.strip():
        return contract

    return (
        f"{contract}\n\n"
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


async def _collect_themes(category_ids: List[int], workspace_id: str, limit: int = 6) -> List[str]:
    """
    Recurring themes for the excerpts that informed the answer — the `key_themes` of the
    clusters they belong to. This leverages the consolidation clustering: it tells the
    reader what this creator connects to their question. Ordered by the excerpts' relevance
    (category_ids arrive most-relevant-first). Non-fatal: returns [] on any failure.
    """
    if not category_ids:
        return []
    try:
        topics = await DatabaseService().get_topics(workspace_id)
    except Exception:
        return []
    by_id = {t.get("topic_id"): t for t in topics}
    themes: List[str] = []
    for cid in category_ids:
        topic = by_id.get(cid)
        if not topic:
            continue
        for theme in (topic.get("key_themes") or []):
            if theme and theme not in themes:
                themes.append(theme)
    return themes[:limit]


_WORD_RE = re.compile(r"[a-z0-9']+")


def _quote_grounded(quote: str, chunks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Return the chunk a quote is drawn from if its words substantially appear there, else
    None. Guards against a fabricated "quote" the excerpts never actually contained — the
    model may lightly clean caption noise, but the words must still come from a real source.
    """
    q_words = _WORD_RE.findall(quote.lower())
    if len(q_words) < 4:
        return None
    q_set = set(q_words)
    best, best_ratio = None, 0.0
    for c in chunks:
        c_set = set(_WORD_RE.findall((c.get("text") or "").lower()))
        if not c_set:
            continue
        ratio = len(q_set & c_set) / len(q_set)
        if ratio > best_ratio:
            best, best_ratio = c, ratio
    return best if best_ratio >= 0.6 else None


async def _extract_key_quote(diverse: List[Dict[str, Any]], llm) -> Optional[Dict[str, Any]]:
    """
    Pick the single most memorable line from the excerpts, lightly cleaned, and only return
    it if it's grounded in one of them. Runs as its own cheap call so its latency can hide
    behind the main answer's streaming. Non-fatal: returns None on any failure.
    """
    try:
        joined = "\n\n".join(f"[{i}] {c.get('text', '')}" for i, c in enumerate(diverse))
        prompt = (
            "From the excerpts below, choose the SINGLE most memorable, quotable sentence — "
            "the one line that best captures a central idea. Lightly clean caption artifacts "
            "and punctuation for readability, but keep the speaker's actual words; never invent "
            "or paraphrase into something not said. Respond with ONLY JSON: "
            '{"quote": "<the line>", "index": <the [n] it came from>}. '
            'If nothing is genuinely quotable, respond {"quote": "", "index": -1}.\n\n'
            f"Excerpts:\n{joined}"
        )
        resp = await llm.acomplete(prompt)
        match = re.search(r"\{.*\}", str(resp), re.DOTALL)
        if not match:
            return None
        quote = (json.loads(match.group(0)).get("quote") or "").strip()
        if not quote:
            return None
        source = _quote_grounded(quote, diverse)
        if not source:
            return None
        meta = source.get("metadata", {})
        return {
            "text": quote,
            "title": meta.get("title"),
            "url": meta.get("url"),
            "start_time": meta.get("start_time"),
        }
    except Exception:
        return None


@router.post("")
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Workspace-scoped streaming chat endpoint.
    Retrieves from consolidation_{workspace_id} namespace and streams a grounded answer.
    Applies source diversity so results span multiple videos, not just the closest one.
    Response format: raw text tokens, then \\n\\n__SOURCES__{json}, then \\n\\n__META__{json}
    (scale, recurring themes, one grounded key quote — for the answer's supporting cards).
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
                connection_ids=request.allowed_connection_ids,
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

            # Key quote runs concurrently on a cheap model so its latency hides behind
            # the main answer's streaming; awaited once at the end for the meta block.
            quote_llm = OpenAILike(
                model=_TIER_MODELS[_DEFAULT_TIER],
                api_key=os.getenv("OPENROUTER_API_KEY"),
                api_base="https://openrouter.ai/api/v1",
                temperature=0.0,
                max_tokens=256,
                is_chat_model=True,
            )
            quote_task = asyncio.create_task(_extract_key_quote(diverse, quote_llm))

            context_parts = []
            for c in diverse:
                meta = c.get("metadata", {})
                label = meta.get("title") or meta.get("source_file") or "unknown"
                start_time = meta.get("start_time", "")
                header = f"[{label}" + (f" @ {start_time}" if start_time else "") + "]"
                context_parts.append(f"{header}\n{c['text']}")
            context = "\n\n---\n\n".join(context_parts)

            system = _build_system_prompt(request.instructions, request.creator_name)

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

            # Meta cards: what the answer was synthesized from, the recurring themes it
            # touches (from clustering), and one grounded key quote. Each piece degrades
            # to empty/None independently so a slow or failed part never blocks the rest.
            category_ids: List[int] = []
            for c in diverse:
                cid = c.get("metadata", {}).get("category_id")
                if cid is not None and cid not in category_ids:
                    category_ids.append(cid)
            themes = await _collect_themes(category_ids, request.workspace_id)
            distinct_sources = {
                (c.get("metadata", {}).get("source_id") or c["id"]) for c in diverse
            }
            try:
                key_quote = await quote_task
            except Exception:
                key_quote = None
            meta = {
                "scale": {"sources": len(distinct_sources), "excerpts": len(diverse)},
                "themes": themes,
                "key_quote": key_quote,
            }
            yield f"\n\n__META__{json.dumps(meta)}"

            # Persist the turn as a topic-graph / training event. Fire-and-forget so it
            # never blocks or breaks the response (log_topic_event swallows its own errors).
            asyncio.create_task(DatabaseService().log_topic_event({
                "workspace_id": request.workspace_id,
                "user_id": user_id,
                "query": request.query,
                "topic_ids": category_ids,
                "themes": themes,
                "source_ids": list(distinct_sources),
                "top_score": round(diverse[0]["score"], 4) if diverse else None,
            }))

        except Exception as e:
            print(f"[CHAT] generation failed for workspace={request.workspace_id}: {e}")
            yield f"\n\n__ERROR__{json.dumps({'message': 'Something went wrong generating a response. Please try again.'})}"

    return StreamingResponse(generate(), media_type="text/event-stream")
