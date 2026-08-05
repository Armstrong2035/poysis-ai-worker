"""
The order bytes leave the endpoint in — that ordering *is* the feature.

Nothing can render until retrieval lands, but the answer takes seconds longer to
generate. Leading with __SOURCES__ lets a client paint source cards immediately and
stream prose into place, which is most of the perceived latency, for zero model
change. It also changes the wire contract, so a client expecting prose first would
render the marker as visible text — hence the opt-in, and hence these tests.
"""

import asyncio
import json
import types

import pytest

from app.api import chat as chat_module
from app.api.chat import ChatRequest


class _Delta:
    def __init__(self, text):
        self.delta = text


class _StubLLM:
    """Serves every LLM role in the endpoint: classify, key quote, and the answer."""

    async def acomplete(self, prompt):
        # _classify_intent and _extract_key_quote both go through here. Returning
        # "synthesis" keeps the run on the branch under test.
        return "synthesis"

    async def astream_chat(self, messages):
        async def gen():
            for piece in ("Hello", " world"):
                yield _Delta(piece)

        return gen()


class _StubVectorService:
    def detect_score_gap(self, chunks, min_results):
        return chunks


class _StubEngine:
    """Returns one chunk, shaped like a real retrieval hit."""

    def __init__(self):
        self.vector_service = _StubVectorService()

    async def fetch_raw(self, notebook_id, text, top_k, connection_ids, topic_ids):
        return [{
            "id": "c1",
            "text": "some excerpt",
            "score": 0.9,
            "metadata": {
                "title": "A talk",
                "url": "https://youtu.be/x?t=10",
                "source_type": "youtube",
                "source_id": "vid1",
                "category_id": 1,
            },
        }]


class _StubDB:
    async def log_topic_event(self, event):
        return None

    async def get_topics_by_ids(self, *a, **kw):
        return []


@pytest.fixture
def stubbed_chat(monkeypatch):
    """Patch every outbound dependency so only the emission order is under test."""
    # chat() resolves its engine through the shared accessor, not the class.
    monkeypatch.setattr(chat_module, "get_knowledge_engine", _StubEngine)
    monkeypatch.setattr(chat_module, "DatabaseService", _StubDB)
    monkeypatch.setattr(
        "llama_index.llms.openai_like.OpenAILike", lambda *a, **kw: _StubLLM()
    )

    async def _allow(workspace_id, user_id):
        return workspace_id

    monkeypatch.setattr(chat_module, "verify_workspace_ownership", _allow)

    async def _themes(category_ids, workspace_id):
        return []

    monkeypatch.setattr(chat_module, "_collect_themes", _themes)


def _stream(request: ChatRequest) -> str:
    """Run the endpoint and join everything it streamed."""

    async def go():
        response = await chat_module.chat(request, user_id="u1")
        chunks = [c async for c in response.body_iterator]
        return "".join(
            c.decode() if isinstance(c, (bytes, bytearray)) else c for c in chunks
        )

    return asyncio.run(go())


def _request(**overrides) -> ChatRequest:
    return ChatRequest(
        workspace_id="ws_test",
        query="what does he teach about faith?",
        mode="synthesis",
        **overrides,
    )


def test_sources_lead_when_requested(stubbed_chat):
    """The feature: cards can render before a single token of prose exists."""
    body = _stream(_request(sources_first=True))

    assert body.startswith("__SOURCES__"), "sources must be the first bytes on the wire"
    assert body.index("__SOURCES__") < body.index("Hello"), "sources before prose"
    assert body.index("Hello") < body.index("__META__"), "meta still trails"


def test_default_preserves_the_original_contract(stubbed_chat):
    """Off by default: a deployed client expecting prose first must not break.

    If this flips silently, every live bot renders raw JSON above its answer.
    """
    body = _stream(_request())

    assert not body.startswith("__SOURCES__")
    assert body.startswith("Hello")
    assert body.index("Hello") < body.index("__SOURCES__") < body.index("__META__")


def test_sources_payload_is_identical_in_both_orders(stubbed_chat):
    """Only the emission point moves — the content must not."""
    first = _stream(_request(sources_first=True))
    last = _stream(_request())

    def sources_of(body):
        # raw_decode reads just the leading JSON value — whatever follows the payload
        # differs between the two orders (prose in one case, __META__ in the other).
        after = body.split("__SOURCES__", 1)[1]
        return json.JSONDecoder().raw_decode(after.lstrip())[0]

    assert sources_of(first) == sources_of(last)
    assert sources_of(first)[0]["source_id"] == "vid1"
