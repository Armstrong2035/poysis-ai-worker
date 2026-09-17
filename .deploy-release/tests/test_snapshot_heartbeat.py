"""
The snapshot runner must report activity even when nothing succeeds.

A run heartbeats by writing progress to the consolidation_jobs row, and clients
treat that row's `updated_at` as the "is this still alive?" signal. Failures
yield no chunks, so a heartbeat driven only by chunks reaching the engine goes
silent exactly when a run is going wrong — a channel whose remaining videos all
fail captions becomes indistinguishable from a dead worker. These tests pin the
behaviour that keeps those two cases apart.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.primitives.consolidation import snapshot as snapshot_module
from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner


def _item(video_id: str) -> RawSourceItem:
    return RawSourceItem(
        source_id=video_id,
        source_type="youtube",
        title=f"video {video_id}",
        url=f"https://www.youtube.com/watch?v={video_id}",
        etag="2026-01-01T00:00:00Z",
        last_modified=datetime.now(timezone.utc),
        content_type="document",
        size_bytes=0,
    )


class _StubYouTubeConnector:
    """Yields the given videos; fetch_segments fails or succeeds per `failing`."""

    def __init__(self, video_ids, failing: bool):
        self._video_ids = video_ids
        self._failing = failing
        self.listed_total = len(video_ids)
        self.skipped_short = 0

    async def list_items(self, scope):
        for video_id in self._video_ids:
            yield _item(video_id)

    async def fetch_segments(self, item):
        if self._failing:
            raise RuntimeError(f"No captions for video {item.source_id}")
        return [{"start": 0.0, "duration": 1.0, "text": "hello"}]


def _scope() -> ScopeConfig:
    return ScopeConfig(
        workspace_id="ws_test",
        sources=["youtube"],
        youtube_channel_ids=["UCtest"],
    )


@pytest.fixture
def stub_youtube(monkeypatch):
    """Swap the YouTube connector factory; returns a setter for the stub."""

    def install(video_ids, failing):
        monkeypatch.setattr(
            snapshot_module,
            "_youtube_connector",
            lambda *a, **kw: _StubYouTubeConnector(video_ids, failing),
        )

    return install


def _drain(runner):
    """Collect the whole stream.

    Driven with asyncio.run rather than @pytest.mark.asyncio: pytest-asyncio is
    not a declared dependency of this project, and a test that errors out on a
    missing plugin verifies nothing.
    """

    async def go():
        return [chunk async for chunk in runner.stream()]

    return asyncio.run(go())


def test_heartbeats_when_every_document_fails(stub_youtube):
    """The case that made a live job look dead: all fetches fail, no chunks flow.

    Without a heartbeat on the failure path the row's updated_at freezes and a
    working-but-failing run is reported as a stalled one.
    """
    stub_youtube(["vid1", "vid2"], failing=True)
    runner = SnapshotRunner(scope=_scope())

    beats = []
    runner.on_activity = lambda: beats.append(
        (runner.docs_processed, runner.docs_failed)
    )

    chunks = _drain(runner)

    assert chunks == [], "failing fetches must not yield chunks"
    assert runner.docs_failed == 2
    assert runner.docs_processed == 0
    # One beat per failure — this is the whole point: activity is visible even
    # though no document succeeded and no chunk was produced.
    assert beats == [(0, 1), (0, 2)]


def test_counts_failures_separately_from_successes(stub_youtube):
    """docs_failed must not be folded into docs_processed.

    A client showing "N videos done" would otherwise count failures as progress,
    which is how a run that indexed nothing reports a clean finish.
    """
    stub_youtube(["vid1"], failing=False)
    runner = SnapshotRunner(scope=_scope())

    beats = []
    runner.on_activity = lambda: beats.append(runner.docs_processed)

    chunks = _drain(runner)

    assert chunks, "a successful fetch must produce chunks"
    assert runner.docs_processed == 1
    assert runner.docs_failed == 0
    assert beats == [1]


def test_stream_works_without_a_callback(stub_youtube):
    """on_activity is optional — leaving it unset must not break a run."""
    stub_youtube(["vid1"], failing=True)
    runner = SnapshotRunner(scope=_scope())

    assert runner.on_activity is None
    assert _drain(runner) == []
    assert runner.docs_failed == 1
