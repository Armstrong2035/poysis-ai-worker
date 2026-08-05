from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from llama_index.core.llms import ChatMessage, MessageRole
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import json
import os
import re
from app.primitives.knowledge.engine import KnowledgeEngine, get_knowledge_engine
from app.primitives.database import DatabaseService
from app.api.security import get_user_id, verify_workspace_ownership

router = APIRouter(prefix="/chat", tags=["chat"])

# Every provider here speaks the OpenAI wire format, so one OpenAILike client covers
# all of them — a tier only has to name a base URL and the env var holding its key.
_PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
}

# Client sends a tier name, not a raw model ID — keeps model choice out of the
# client and lets us swap the underlying model (or provider) without a client release.
_TIER_MODELS = {
    "quick": ("deepseek", "deepseek-chat"),
    "thinking": ("openai", "gpt-4.1"),
    "expert": ("openai", "gpt-4.1"),
}
_DEFAULT_TIER = "quick"

# The model behind the internal calls — intent classification, follow-up rewriting,
# key-quote extraction — kept separate from the answer tiers on purpose. Those calls
# are short, latency-critical and invisible to the user, and two of them sit on the
# critical path; they should not inherit whatever model someone picks for prose.
_UTILITY_MODEL = ("openai", "gpt-4.1-mini")

# A tier naming a provider whose key isn't configured falls back here rather than
# failing the request. A missing DEEPSEEK_API_KEY should degrade to the old model,
# not 500 every chat in production.
_FALLBACK_MODEL = ("openai", "gpt-4.1-mini")


def _build_llm(spec, *, temperature: float, max_tokens: int):
    """Construct a client for a (provider, model) pair, falling back if unconfigured."""
    from llama_index.llms.openai_like import OpenAILike

    provider, model = spec
    api_base, key_env = _PROVIDERS[provider]
    api_key = os.getenv(key_env)
    if not api_key:
        print(f"[CHAT] {key_env} not set — falling back to {_FALLBACK_MODEL[1]}")
        provider, model = _FALLBACK_MODEL
        api_base, key_env = _PROVIDERS[provider]
        api_key = os.getenv(key_env)

    return OpenAILike(
        model=model,
        api_key=api_key,
        api_base=api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        is_chat_model=True,
    )

# History caps. The client owns the transcript (the server is stateless, and public
# bots share one user_id — server-side history would leak between visitors), so these
# are the server's guardrails against an unbounded prompt.
MAX_HISTORY_TURNS = 6            # 3 exchanges — enough for "tell me more" to resolve
MAX_HISTORY_TURNS_ACCEPTED = 20  # beyond this the client is misusing the field
ASSISTANT_TURN_CHARS = 300       # answers are long; only their gist carries the thread


class Turn(BaseModel):
    role: str      # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    workspace_id: str
    query: str
    history: List[Turn] = []     # oldest → newest, excluding the current query
    top_k: Optional[int] = 8            # synthesis across sources needs more material than lookup does
    min_score: Optional[float] = 0.4
    model: Optional[str] = None  # tier name: "quick" | "thinking" | "expert"
    mode: Optional[str] = None   # "retrieval" | "synthesis"; None → classify the query
    # Emit __SOURCES__ before the prose instead of after it. Nothing can render until
    # retrieval lands either way, but the answer takes seconds more to generate — so
    # leading with sources lets the client paint cards immediately and stream prose
    # into place, which is most of the perceived latency for zero model change.
    # Opt-in because it changes the wire order: a client that expects prose first
    # would render the marker as text. Flip the default once clients have migrated.
    sources_first: Optional[bool] = False
    temperature: Optional[float] = 0.4                  # grounded, but high enough that the model synthesizes instead of defaulting to "not enough information"
    max_tokens: Optional[int] = 800                # backstop for brief, screenshot-friendly answers (prompt does the real work)
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
        "LENGTH: keep it brief and screenshot-worthy — a few tight sentences, ideally under "
        "~120 words. Lead with the core idea in the very first line; cut preamble, "
        "throat-clearing, and 'in summary' endings. Short paragraphs, no filler. Depth comes "
        "from precision, not length.\n\n"
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


async def _classify_intent(query: str) -> str:
    """
    Decide whether a query wants plain RETRIEVAL (find/list/look up specific sources) or
    SYNTHESIS (an interpreted answer drawn across the body of work). The distinction is
    verb/intent-driven, so a cheap LLM classifies it rather than embedding proximity, which
    keys on topic. Runs concurrently with retrieval, so its latency hides behind that fetch.

    Fails toward "synthesis" — the platform's default behavior — on any error, so the
    classifier can never make a query worse than it is today.
    """
    try:
        llm = _build_llm(_UTILITY_MODEL, temperature=0.0, max_tokens=5)
        prompt = (
            "Classify the user's query as either RETRIEVAL or SYNTHESIS.\n\n"
            "RETRIEVAL — they want to find, list, or browse specific sources, or look up a "
            "stated item. They want pointers to material, not an essay.\n"
            "  \"list my youtube videos\" → retrieval\n"
            "  \"which talks mention prayer\" → retrieval\n"
            "  \"find the video about fasting\" → retrieval\n\n"
            "SYNTHESIS — they want an interpreted answer drawn across the body of work.\n"
            "  \"what does he teach about faith\" → synthesis\n"
            "  \"explain his view on suffering\" → synthesis\n"
            "  \"summarize his thinking on prayer\" → synthesis\n\n"
            "Answer with ONLY one word: retrieval or synthesis.\n\n"
            f"Query: {query}\nAnswer:"
        )
        resp = str(await llm.acomplete(prompt)).strip().lower()
        return "retrieval" if "retriev" in resp else "synthesis"
    except Exception:
        return "synthesis"


def _trim_history(history: List[Turn]) -> List[Turn]:
    """Keep the last few turns, with assistant answers cut to their opening.

    Assistant turns are the expensive ones — a full prior answer can be longer than
    the context we retrieve. Their job here is only to make the *next* user turn
    resolvable ("tell me more about that"), and the opening lines carry that.
    """
    recent = history[-MAX_HISTORY_TURNS:]
    return [
        Turn(
            role=t.role,
            content=(
                t.content[:ASSISTANT_TURN_CHARS]
                if t.role == "assistant" and len(t.content) > ASSISTANT_TURN_CHARS
                else t.content
            ),
        )
        for t in recent
    ]


async def _standalone_query(history: List[Turn], query: str) -> str:
    """Rewrite a follow-up into a self-contained question for retrieval.

    Retrieval and generation need different things from the history. Generation wants
    the conversation; retrieval wants one embeddable sentence — "what about doubt?"
    embeds to noise, because the subject lives in the previous turn, not this one.

    Unlike _classify_intent this cannot run concurrently with retrieval: it decides
    what to retrieve. That is why it is skipped whenever there is no history, which is
    the common case — a first turn pays nothing. Any failure falls back to the raw
    query, so the worst case is today's behaviour.
    """
    if not history:
        return query
    try:
        llm = _build_llm(_UTILITY_MODEL, temperature=0.0, max_tokens=60)
        transcript = "\n".join(f"{t.role}: {t.content}" for t in history)
        prompt = (
            "Rewrite the follow-up question as a standalone question that can be "
            "understood without the conversation. Resolve pronouns and references "
            "using the conversation. Keep the user's wording and intent — do not "
            "answer it, expand it, or add topics they didn't raise. If it already "
            "stands alone, repeat it unchanged.\n\n"
            "Reply with ONLY the rewritten question.\n\n"
            f"Conversation:\n{transcript}\n\n"
            f"Follow-up: {query}\n\nStandalone:"
        )
        rewritten = str(await llm.acomplete(prompt)).strip().strip('"')
        # A blank or runaway rewrite means the model ignored the instruction.
        if not rewritten or len(rewritten) > 400:
            return query
        return rewritten
    except Exception:
        return query


async def _retrieve(engine: KnowledgeEngine, request: ChatRequest, query: str) -> List[Dict[str, Any]]:
    """
    Shared retrieval core for both chat modes: over-fetch a wide pool so diversity has
    candidates from many sources, apply the min_score floor, trim to the natural relevance
    cliff, then round-robin across sources. Returns the final diversified chunks (maybe empty).
    """
    candidates = await engine.fetch_raw(
        notebook_id=f"consolidation_{request.workspace_id}",
        text=query,
        top_k=request.top_k * 6,
        connection_ids=request.allowed_connection_ids,
        topic_ids=request.allowed_topic_ids,
    )
    above_threshold = [c for c in candidates if c.get("score", 0) >= request.min_score]
    # min_score is the floor (guards the "nothing relevant" case); the gap detector then
    # trims to the natural relevance cliff within what clears it.
    gapped = engine.vector_service.detect_score_gap(above_threshold, min_results=request.top_k)
    return _diversify(gapped, request.top_k)


def _build_sources(diverse: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Source cards for the response, emitted by both retrieval and synthesis modes."""
    return [
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


@router.post("")
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Workspace-scoped chat endpoint that serves two modes over one streaming response.
    Retrieves from consolidation_{workspace_id} with source diversity, then:
      - SYNTHESIS (default): streams a grounded answer, then \\n\\n__SOURCES__{json},
        then \\n\\n__META__{json} (scale, themes, one grounded key quote). Byte-for-byte
        the original contract — no leading marker. With `sources_first`, __SOURCES__
        leads instead (followed by \\n\\n), so the client can paint source cards while
        the answer is still generating; __META__ still trails.
      - RETRIEVAL: leads with \\n\\n__MODE__{json} (so a client renders a source list, not
        a typing indicator), then __SOURCES__ and a leaner __META__ (scale, themes; no
        key quote). No synthesized prose, no synthesis LLM call.
    The mode is `request.mode` when given, otherwise inferred by _classify_intent (run
    concurrently with retrieval). On failure mid-stream, emits \\n\\n__ERROR__{json}.
    """
    await verify_workspace_ownership(request.workspace_id, user_id)

    if len(request.history) > MAX_HISTORY_TURNS_ACCEPTED:
        raise HTTPException(
            status_code=422,
            detail=f"history exceeds {MAX_HISTORY_TURNS_ACCEPTED} turns.",
        )

    engine = get_knowledge_engine()

    async def generate():
        try:
            # Classify intent concurrently with retrieval when no explicit mode is given:
            # both must happen, retrieval is network-bound, so the classifier's latency
            # hides behind the fetch. _classify_intent never raises (defaults to synthesis).
            classify_task = (
                asyncio.create_task(_classify_intent(request.query))
                if request.mode is None else None
            )
            # Resolve references before retrieving — a follow-up's subject lives in the
            # previous turn, so the raw text would embed to noise. No-op on a first turn.
            history = _trim_history(request.history)
            search_query = await _standalone_query(history, request.query)
            diverse = await _retrieve(engine, request, search_query)
            mode = request.mode if classify_task is None else await classify_task

            if not diverse:
                # No results: retrieval mode still self-identifies (leading __MODE__, empty
                # source list, no prose) so the client renders its empty state; synthesis
                # returns the human "couldn't find" line as before.
                if mode == "retrieval":
                    yield f"__MODE__{json.dumps({'mode': 'retrieval'})}"
                else:
                    yield "I couldn't find relevant information in your knowledge base to answer that question."
                yield f"\n\n__SOURCES__{json.dumps([])}"
                return

            # Shared meta inputs for both modes' __META__ and the topic-graph log.
            category_ids: List[int] = []
            for c in diverse:
                cid = c.get("metadata", {}).get("category_id")
                if cid is not None and cid not in category_ids:
                    category_ids.append(cid)
            distinct_sources = {
                (c.get("metadata", {}).get("source_id") or c["id"]) for c in diverse
            }

            if mode == "retrieval":
                # Sources only — no answer to synthesize, and no key quote (that's an LLM
                # call; retrieval stays LLM-free). Themes are a cheap DB lookup, kept.
                yield f"__MODE__{json.dumps({'mode': 'retrieval'})}"
                yield f"\n\n__SOURCES__{json.dumps(_build_sources(diverse))}"
                themes = await _collect_themes(category_ids, request.workspace_id)
                meta = {
                    "scale": {"sources": len(distinct_sources), "excerpts": len(diverse)},
                    "themes": themes,
                }
                yield f"\n\n__META__{json.dumps(meta)}"
            else:
                # Key quote runs concurrently on a cheap model so its latency hides behind
                # the main answer's streaming; awaited once at the end for the meta block.
                quote_llm = _build_llm(_UTILITY_MODEL, temperature=0.0, max_tokens=256)
                quote_task = asyncio.create_task(_extract_key_quote(diverse, quote_llm))

                # Built before the answer either way — only the emission point moves.
                sources_payload = json.dumps(_build_sources(diverse))
                if request.sources_first:
                    yield f"__SOURCES__{sources_payload}\n\n"

                context_parts = []
                for c in diverse:
                    meta = c.get("metadata", {})
                    label = meta.get("title") or meta.get("source_file") or "unknown"
                    start_time = meta.get("start_time", "")
                    header = f"[{label}" + (f" @ {start_time}" if start_time else "") + "]"
                    context_parts.append(f"{header}\n{c['text']}")
                context = "\n\n---\n\n".join(context_parts)

                system = _build_system_prompt(request.instructions, request.creator_name)

                # Real message roles rather than one concatenated string: prior turns
                # have to be attributable for the model to follow the thread, and it
                # keeps the static system contract as a cacheable prefix.
                messages = [ChatMessage(role=MessageRole.SYSTEM, content=system)]
                for turn in history:
                    messages.append(
                        ChatMessage(
                            role=(
                                MessageRole.ASSISTANT
                                if turn.role == "assistant"
                                else MessageRole.USER
                            ),
                            content=turn.content,
                        )
                    )
                # Context rides with the current question, not the system prompt — it is
                # retrieved fresh for this turn and must not look like a standing rule.
                messages.append(
                    ChatMessage(
                        role=MessageRole.USER,
                        content=f"Context:\n{context}\n\nQuestion: {request.query}",
                    )
                )

                llm = _build_llm(
                    _TIER_MODELS.get(request.model, _TIER_MODELS[_DEFAULT_TIER]),
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
                streaming_response = await llm.astream_chat(messages)
                async for delta in streaming_response:
                    yield delta.delta

                if not request.sources_first:
                    yield f"\n\n__SOURCES__{sources_payload}"

                # Meta cards: recurring themes it touches (from clustering) and one grounded
                # key quote. Each degrades to empty/None independently so a slow or failed
                # part never blocks the rest.
                themes = await _collect_themes(category_ids, request.workspace_id)
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

            # Persist the turn as a topic-graph / training event (both modes — retrieval
            # turns are graph/training data too). Fire-and-forget so it never blocks or
            # breaks the response (log_topic_event swallows its own errors).
            asyncio.create_task(DatabaseService().log_topic_event({
                "workspace_id": request.workspace_id,
                "user_id": user_id,
                # The resolved question, not the fragment the user typed. "what about
                # doubt?" is useless as graph/training data; the standalone form is the
                # one that actually drove retrieval.
                "query": search_query,
                "topic_ids": category_ids,
                "themes": themes,
                "source_ids": list(distinct_sources),
                "top_score": round(diverse[0]["score"], 4) if diverse else None,
            }))

        except Exception as e:
            print(f"[CHAT] generation failed for workspace={request.workspace_id}: {e}")
            yield f"\n\n__ERROR__{json.dumps({'message': 'Something went wrong generating a response. Please try again.'})}"

    return StreamingResponse(generate(), media_type="text/event-stream")
