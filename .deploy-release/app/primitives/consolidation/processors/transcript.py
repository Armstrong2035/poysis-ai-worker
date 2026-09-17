"""Source-agnostic transcript processor.

Consumes a list of timed segments — {"start": float, "text": str, "duration": float} —
and produces ProcessedChunks where each chunk covers a time window. Every chunk carries
precise timestamps so retrieval results include a deep-link the end user can click to
jump straight to that moment in the source.

Segments are expected in chronological order. Any source (YouTube, Drive recordings,
Zoom exports, etc.) can feed this processor as long as it provides the segment shape.
"""
from typing import List

from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.processors.base import ProcessedChunk

_WINDOW_SECONDS = 60  # seconds of audio per chunk


class TranscriptProcessor:
    def __init__(self, window_seconds: int = _WINDOW_SECONDS):
        self.window_seconds = window_seconds

    async def process(
        self, item: RawSourceItem, segments: List[dict]
    ) -> List[ProcessedChunk]:
        if not segments:
            return []

        chunks: List[ProcessedChunk] = []
        window: List[dict] = []
        window_start: float = segments[0]["start"]

        for seg in segments:
            window.append(seg)
            elapsed = seg["start"] - window_start

            if elapsed >= self.window_seconds:
                chunks.append(self._make_chunk(item, window))
                window = []
                window_start = seg["start"] + seg.get("duration", 0)

        if window:
            chunks.append(self._make_chunk(item, window))

        return chunks

    def _make_chunk(self, item: RawSourceItem, window: List[dict]) -> ProcessedChunk:
        start_s = window[0]["start"]
        last = window[-1]
        end_s = last["start"] + last.get("duration", 0)

        lines = [f"[{_fmt(seg['start'])}] {seg['text'].strip()}" for seg in window]
        text = "\n".join(lines)

        # Deep-link URL so the end user can jump straight to this moment
        ts_url = f"{item.url}&t={int(start_s)}" if "?" in item.url else f"{item.url}?t={int(start_s)}"

        return ProcessedChunk(
            text=text,
            source_id=item.source_id,
            source_type=item.source_type,
            title=item.title,
            url=ts_url,
            timestamp_start_ms=int(start_s * 1000),
            timestamp_end_ms=int(end_s * 1000),
            extra_metadata={
                "start_time": _fmt(start_s),
                "end_time": _fmt(end_s),
                "start_seconds": start_s,
                **({"connection_id": item.connection_id} if item.connection_id else {}),
            },
        )


def _fmt(seconds: float) -> str:
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
