"""YouTube connector — lists public channel videos and fetches captions.

No OAuth required. Uses:
  - YouTube Data API v3 (YOUTUBE_API_KEY env var) to list videos.
  - youtube-transcript-api to pull captions without credentials.
"""
import os
from datetime import datetime, timezone
from typing import AsyncIterator, List

import httpx

from app.primitives.consolidation.connectors.base import BaseConnector, RawSourceItem
from app.primitives.consolidation.scope import ScopeConfig

_YT_API = "https://www.googleapis.com/youtube/v3"


class YouTubeConnector(BaseConnector):
    def __init__(self, channel_ids: List[str]):
        self.channel_ids = channel_ids
        self.api_key = os.environ.get("YOUTUBE_API_KEY", "")

    async def list_items(self, scope: ScopeConfig) -> AsyncIterator[RawSourceItem]:
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")

        limit = scope.doc_limit if scope.doc_limit != -1 else float("inf")
        fetched = 0

        async with httpx.AsyncClient(timeout=30) as client:
            for channel_id in self.channel_ids:
                page_token: str | None = None
                while fetched < limit:
                    batch = min(50, int(limit - fetched)) if limit != float("inf") else 50
                    params: dict = {
                        "part": "id,snippet",
                        "channelId": channel_id,
                        "type": "video",
                        "maxResults": batch,
                        "key": self.api_key,
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    resp = await client.get(f"{_YT_API}/search", params=params)
                    resp.raise_for_status()
                    data = resp.json()

                    for entry in data.get("items", []):
                        video_id = entry.get("id", {}).get("videoId")
                        if not video_id:
                            continue
                        snippet = entry.get("snippet", {})
                        title = snippet.get("title", "Untitled")
                        published_at = snippet.get("publishedAt", "")
                        last_modified = (
                            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                            if published_at
                            else datetime.now(timezone.utc)
                        )
                        yield RawSourceItem(
                            source_id=video_id,
                            source_type="youtube",
                            title=title,
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            etag=published_at or video_id,
                            last_modified=last_modified,
                            content_type="document",
                            size_bytes=0,
                        )
                        fetched += 1
                        if fetched >= limit:
                            return

                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break

    async def fetch_segments(self, item: RawSourceItem) -> List[dict]:
        """Return raw transcript segments: [{start, duration, text}, ...]."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            ytt = YouTubeTranscriptApi()
            fetched = ytt.fetch(item.source_id)
            return [{"start": s.start, "duration": s.duration, "text": s.text} for s in fetched]
        except Exception as e:
            raise RuntimeError(f"No captions for video {item.source_id}: {e}")

    async def fetch_text(self, item: RawSourceItem) -> str:
        """Plain text fallback — no timestamps. Use fetch_segments for timed output."""
        segments = await self.fetch_segments(item)
        return " ".join(seg["text"] for seg in segments)

    async def fetch_file(self, item: RawSourceItem) -> str:
        raise NotImplementedError("YouTube source does not support binary download")
