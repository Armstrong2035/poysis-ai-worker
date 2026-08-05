"""
Conversation memory: what the history is allowed to cost, and what it must fix.

Retrieval and generation want different things from a transcript. Generation wants
the conversation; retrieval wants one embeddable sentence, because a follow-up's
subject lives in the *previous* turn — "what about doubt?" embeds to noise. These
tests pin that split, and pin the cost: a first turn must not pay for a rewrite it
doesn't need, and a rewrite that goes wrong must never be worse than no rewrite.
"""

import asyncio

import pytest

from app.api import chat as chat_module
from app.api.chat import (
    ASSISTANT_TURN_CHARS,
    MAX_HISTORY_TURNS,
    Turn,
    _standalone_query,
    _trim_history,
)


def _run(coro):
    """pytest-asyncio is not a declared dependency; drive the coroutine directly."""
    return asyncio.run(coro)


class _StubLLM:
    """Stands in for the rewrite model. Records calls, returns or raises."""

    calls = 0

    def __init__(self, reply=None, raises=False):
        self._reply = reply
        self._raises = raises
        type(self).calls = 0

    async def acomplete(self, prompt):
        type(self).calls += 1
        if self._raises:
            raise RuntimeError("model unavailable")
        return self._reply


@pytest.fixture
def stub_rewrite_llm(monkeypatch):
    """Swap OpenAILike inside _standalone_query; returns an installer."""

    def install(reply=None, raises=False):
        stub = _StubLLM(reply=reply, raises=raises)
        monkeypatch.setattr(
            "llama_index.llms.openai_like.OpenAILike",
            lambda *a, **kw: stub,
        )
        return stub

    return install


# --- what the rewrite must fix -------------------------------------------------


def test_follow_up_is_resolved_before_retrieval(stub_rewrite_llm):
    """The point of the whole step: a fragment becomes something embeddable."""
    stub_rewrite_llm(reply="What does he teach about doubt?")
    history = [
        Turn(role="user", content="what does he teach about faith?"),
        Turn(role="assistant", content="He frames faith as trust under pressure."),
    ]

    resolved = _run(_standalone_query(history, "what about doubt?"))

    assert resolved == "What does he teach about doubt?"


# --- what the rewrite must not cost --------------------------------------------


def test_first_turn_skips_the_model_entirely(stub_rewrite_llm):
    """No history means nothing to resolve.

    This step gates retrieval, so unlike the intent classifier it cannot hide behind
    it. First turns are the common case and must not pay a round-trip for it.
    """
    stub = stub_rewrite_llm(reply="should never be used")

    resolved = _run(_standalone_query([], "what does he teach about faith?"))

    assert resolved == "what does he teach about faith?"
    assert stub.calls == 0, "a first turn must not call the model"


def test_rewrite_failure_falls_back_to_the_raw_query(stub_rewrite_llm):
    """Worst case must equal today's behaviour, never an error."""
    stub_rewrite_llm(raises=True)
    history = [Turn(role="user", content="what does he teach about faith?")]

    assert _run(_standalone_query(history, "what about doubt?")) == "what about doubt?"


@pytest.mark.parametrize(
    "bad_reply",
    ["", "   ", "x" * 401],
    ids=["empty", "whitespace", "runaway"],
)
def test_unusable_rewrite_falls_back(stub_rewrite_llm, bad_reply):
    """A blank or runaway rewrite means the model ignored the instruction.

    Retrieving on either would be strictly worse than retrieving on what the user
    actually typed.
    """
    stub_rewrite_llm(reply=bad_reply)
    history = [Turn(role="user", content="what does he teach about faith?")]

    assert _run(_standalone_query(history, "what about doubt?")) == "what about doubt?"


# --- what the history is allowed to cost ---------------------------------------


def test_history_is_capped_to_recent_turns():
    """Keeps the newest turns — those are what a follow-up refers to."""
    history = [Turn(role="user", content=f"q{i}") for i in range(20)]

    trimmed = _trim_history(history)

    assert len(trimmed) == MAX_HISTORY_TURNS
    assert trimmed[-1].content == "q19", "must keep the most recent turn"


def test_long_assistant_answers_are_truncated_but_user_turns_are_not():
    """Assistant turns exist only to make the next user turn resolvable.

    A full prior answer can exceed the retrieved context. User turns stay intact —
    they are short, and they carry the intent being referred back to.
    """
    long_answer = "a" * (ASSISTANT_TURN_CHARS + 500)
    long_question = "b" * (ASSISTANT_TURN_CHARS + 500)

    trimmed = _trim_history([
        Turn(role="assistant", content=long_answer),
        Turn(role="user", content=long_question),
    ])

    assert len(trimmed[0].content) == ASSISTANT_TURN_CHARS
    assert trimmed[1].content == long_question


def test_trimming_an_empty_history_is_a_no_op():
    assert _trim_history([]) == []
