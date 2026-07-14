"""YouTube connector — lists public channel videos and fetches captions.

No OAuth required. Uses:
  - YouTube Data API v3 (YOUTUBE_API_KEY env var) to list videos.
  - youtube-transcript-api to pull captions without credentials.

Only yields videos longer than MIN_DURATION_SECONDS to skip shorts and
music videos that never carry sermon transcripts.
"""
import asyncio
import os
import re
from datetime import datetime, timezone
from typing import AsyncIterator, List

import httpx

from app.primitives.consolidation.connectors.base import BaseConnector, RawSourceItem
from app.primitives.consolidation.scope import ScopeConfig

_YT_API = "https://www.googleapis.com/youtube/v3"
MIN_DURATION_SECONDS = 2700  # skip anything shorter than 45 minutes


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

                    data = await _get_with_retry(client, f"{_YT_API}/search", params)

                    # Collect video IDs and metadata from this page
                    page_items = []
                    for entry in data.get("items", []):
                        video_id = entry.get("id", {}).get("videoId")
                        if not video_id:
                            continue
                        snippet = entry.get("snippet", {})
                        page_items.append({
                            "video_id": video_id,
                            "title": snippet.get("title", "Untitled"),
                            "published_at": snippet.get("publishedAt", ""),
                        })

                    # Batch-fetch durations (1 quota unit per 50 videos)
                    durations = await _fetch_durations(
                        client, [p["video_id"] for p in page_items], self.api_key
                    )

                    for item in page_items:
                        video_id = item["video_id"]
                        duration_s = durations.get(video_id, 0)
                        if duration_s < MIN_DURATION_SECONDS:
                            continue  # skip shorts and clips

                        published_at = item["published_at"]
                        last_modified = (
                            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                            if published_at
                            else datetime.now(timezone.utc)
                        )
                        yield RawSourceItem(
                            source_id=video_id,
                            source_type="youtube",
                            title=item["title"],
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
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        for attempt in range(4):
            try:
                fetched = ytt.fetch(item.source_id)
                return [{"start": s.start, "duration": s.duration, "text": s.text} for s in fetched]
            except Exception as e:
                if "429" in str(e) and attempt < 3:
                    wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                    print(f"[YouTube] 429 on '{item.title}' — waiting {wait}s before retry {attempt + 1}/3")
                    await asyncio.sleep(wait)
                else:
                    raise RuntimeError(f"No captions for video {item.source_id}: {e}")

    async def fetch_text(self, item: RawSourceItem) -> str:
        """Plain text fallback — no timestamps. Use fetch_segments for timed output."""
        segments = await self.fetch_segments(item)
        return " ".join(seg["text"] for seg in segments)

    async def fetch_file(self, item: RawSourceItem) -> str:
        raise NotImplementedError("YouTube source does not support binary download")


async def resolve_channel(raw_input: str, api_key: str) -> tuple[str, str]:
    """Resolve a pasted channel URL, @handle, or raw channel ID to (channel_id, title).

    The Data API has no single lookup for every shape a user might paste. Raw IDs and
    /channel/UC... URLs resolve exactly via `id=`. Everything else (@handle, or the
    legacy /c/ and /user/ vanity paths) is resolved via `forHandle=`, which only works
    if the channel's current handle matches the pasted name — legacy /c/ and /user/
    URLs aren't guaranteed to still match if the channel changed its handle since.
    """
    raw_input = raw_input.strip()

    if re.fullmatch(r"UC[\w-]{22}", raw_input):
        lookup = {"id": raw_input}
    else:
        path = raw_input
        m = re.search(r"youtube\.com/([^?#]+)", raw_input, re.IGNORECASE)
        if m:
            path = m.group(1).strip("/")
        parts = path.split("/")

        if parts[0] == "channel" and len(parts) > 1:
            lookup = {"id": parts[1]}
        elif parts[0] in ("c", "user") and len(parts) > 1:
            lookup = {"forHandle": f"@{parts[1].lstrip('@')}"}
        else:
            lookup = {"forHandle": f"@{parts[0].lstrip('@')}"}

    async with httpx.AsyncClient(timeout=15) as client:
        data = await _get_with_retry(client, f"{_YT_API}/channels", {**lookup, "part": "snippet", "key": api_key})

    items = data.get("items", [])
    if not items:
        raise ValueError(f"Could not find a YouTube channel matching '{raw_input}'")

    return items[0]["id"], items[0]["snippet"]["title"]


async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict, retries: int = 3) -> dict:
    """GET with exponential backoff on 403/5xx — YouTube search is occasionally flaky."""
    for attempt in range(retries):
        resp = await client.get(url, params=params)
        if resp.status_code in (403, 500, 502, 503) and attempt < retries - 1:
            wait = 2 ** attempt
            print(f"[YouTube] {resp.status_code} on attempt {attempt + 1} — retrying in {wait}s")
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


async def _fetch_durations(
    client: httpx.AsyncClient, video_ids: List[str], api_key: str
) -> dict:
    """Return {video_id: duration_seconds} for a batch of video IDs."""
    if not video_ids:
        return {}
    resp = await client.get(
        f"{_YT_API}/videos",
        params={
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": api_key,
        },
    )
    resp.raise_for_status()
    result = {}
    for item in resp.json().get("items", []):
        vid_id = item["id"]
        iso = item.get("contentDetails", {}).get("duration", "")
        result[vid_id] = _parse_iso8601_duration(iso)
    return result


def _parse_iso8601_duration(iso: str) -> int:
    """Parse ISO 8601 duration string (e.g. PT1H23M45S) to total seconds."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mins, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mins * 60 + s
