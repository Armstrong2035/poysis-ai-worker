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
from typing import AsyncIterator, Dict, List, Optional

import httpx

from app.primitives.consolidation.connectors.base import BaseConnector, RawSourceItem
from app.primitives.consolidation.scope import ScopeConfig

_YT_API = "https://www.googleapis.com/youtube/v3"
MIN_DURATION_SECONDS = 2700  # skip anything shorter than 45 minutes


class YouTubeConnector(BaseConnector):
    def __init__(
        self,
        channel_ids: List[str],
        min_duration_seconds: int = MIN_DURATION_SECONDS,
        channel_connections: Optional[Dict[str, str]] = None,
    ):
        self.channel_ids = channel_ids
        self.min_duration_seconds = min_duration_seconds
        # channel_id -> connection id (youtube_channels.id); tags each yielded item.
        self.channel_connections = channel_connections or {}
        self.api_key = os.environ.get("YOUTUBE_API_KEY", "")
        # Videos dropped for being shorter than the threshold. A channel that lists
        # videos but yields none is indistinguishable from an empty channel unless
        # the caller can see this — see SnapshotRunner, which turns it into an error.
        self.skipped_short = 0
        # Videos seen on the channel before any filtering, so callers can report
        # "29 of 1030" rather than a bare 29 with no denominator.
        self.listed_total = 0

    async def list_items(self, scope: ScopeConfig) -> AsyncIterator[RawSourceItem]:
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")

        limit = scope.doc_limit if scope.doc_limit != -1 else float("inf")
        fetched = 0

        async with httpx.AsyncClient(timeout=30) as client:
            for channel_id in self.channel_ids:
                # Enumerate via the channel's uploads playlist, not search.list.
                # search.list stops paginating at ~500 results however many videos
                # the channel has — it silently capped a 1,600-video channel — and
                # costs 100 quota units per call against a 10,000/day budget.
                # playlistItems pages the full catalogue at 1 unit per call.
                uploads_playlist_id = await _uploads_playlist_id(client, channel_id, self.api_key)
                if not uploads_playlist_id:
                    raise RuntimeError(f"Channel {channel_id} has no uploads playlist")

                page_token: str | None = None
                while fetched < limit:
                    params: dict = {
                        "part": "snippet,contentDetails",
                        "playlistId": uploads_playlist_id,
                        "maxResults": 50,
                        "key": self.api_key,
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    data = await _get_with_retry(client, f"{_YT_API}/playlistItems", params)

                    # Collect video IDs and metadata from this page
                    page_items = []
                    for entry in data.get("items", []):
                        details = entry.get("contentDetails", {})
                        snippet = entry.get("snippet", {})
                        video_id = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
                        if not video_id:
                            continue  # deleted or private entry
                        page_items.append({
                            "video_id": video_id,
                            # playlistItems dates the *addition*; videoPublishedAt is
                            # the upload date, and it is what etag/last_modified mean.
                            "published_at": details.get("videoPublishedAt")
                            or snippet.get("publishedAt", ""),
                            "title": snippet.get("title", "Untitled"),
                        })
                    self.listed_total += len(page_items)

                    # Batch-fetch durations (1 quota unit per 50 videos)
                    durations = await _fetch_durations(
                        client, [p["video_id"] for p in page_items], self.api_key
                    )

                    for item in page_items:
                        video_id = item["video_id"]
                        duration_s = durations.get(video_id, 0)
                        if duration_s < self.min_duration_seconds:
                            self.skipped_short += 1
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
                            connection_id=self.channel_connections.get(channel_id),
                        )
                        fetched += 1
                        if fetched >= limit:
                            return

                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break

    async def fetch_segments(self, item: RawSourceItem) -> List[dict]:
        """Return raw transcript segments: [{start, duration, text}, ...].

        Backend is chosen by YT_CAPTIONS_BACKEND: "ytdlp" routes through yt-dlp, which
        reaches YouTube's caption endpoint with the same hardened client it uses for video
        info — surviving the datacenter-IP blocks that 429 the default library. Anything
        else (default) keeps the youtube-transcript-api path. Both emit the same shape.
        """
        backend = os.getenv("YT_CAPTIONS_BACKEND", "library").lower()
        if backend == "ytdlp":
            return await self._fetch_segments_ytdlp(item)
        return await self._fetch_segments_library(item)

    async def _fetch_segments_library(self, item: RawSourceItem) -> List[dict]:
        """youtube-transcript-api path. Blocked from datacenter IPs on its own; routes
        through a residential proxy (YT_DLP_PROXY, shared with the yt-dlp backend) when set
        so it survives from Railway. Uses requests under the hood, so proxying is clean."""
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.proxies import GenericProxyConfig
        proxy = os.getenv("YT_DLP_PROXY")
        proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None
        ytt = YouTubeTranscriptApi(proxy_config=proxy_config)
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

    async def _fetch_segments_ytdlp(self, item: RawSourceItem) -> List[dict]:
        """yt-dlp path — fetches the json3 caption track off-thread (yt-dlp is sync)."""
        segments = await asyncio.to_thread(_ytdlp_json3_segments, item.url, item.source_id)
        if not segments:
            raise RuntimeError(f"No captions for video {item.source_id} (yt-dlp)")
        return segments

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


def _best_thumbnail(thumbnails: dict) -> Optional[str]:
    """Pick the highest-res thumbnail URL the API returned, or None."""
    if not thumbnails:
        return None
    for key in ("maxres", "standard", "high", "medium", "default"):
        t = thumbnails.get(key)
        if t and t.get("url"):
            return t["url"]
    return None


async def list_channel_playlists(channel_id: str, api_key: str) -> List[Dict]:
    """List a channel's public playlists via the Data API (key-auth, no scraping).

    Returns [{playlist_id, title, description, item_count, thumbnail}]. Paginates until
    the channel is exhausted. Used to offer playlists as ready-made categories.
    """
    playlists: List[Dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        page_token: Optional[str] = None
        while True:
            params = {
                "part": "snippet,contentDetails",
                "channelId": channel_id,
                "maxResults": 50,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            data = await _get_with_retry(client, f"{_YT_API}/playlists", params)
            for entry in data.get("items", []):
                snippet = entry.get("snippet", {})
                playlists.append({
                    "playlist_id": entry.get("id"),
                    "title": snippet.get("title", "Untitled"),
                    "description": snippet.get("description", ""),
                    "item_count": entry.get("contentDetails", {}).get("itemCount", 0),
                    "thumbnail": _best_thumbnail(snippet.get("thumbnails", {})),
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return playlists


async def list_playlist_videos(playlist_id: str, api_key: str) -> List[Dict]:
    """List the videos in a playlist via the Data API. Metadata only — no transcripts.

    Returns [{video_id, title, thumbnail, published_at, position}], skipping deleted or
    private entries (which carry no resolvable videoId).
    """
    videos: List[Dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        page_token: Optional[str] = None
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            data = await _get_with_retry(client, f"{_YT_API}/playlistItems", params)
            for entry in data.get("items", []):
                snippet = entry.get("snippet", {})
                video_id = entry.get("contentDetails", {}).get("videoId") or \
                    snippet.get("resourceId", {}).get("videoId")
                if not video_id:
                    continue  # deleted/private entry
                videos.append({
                    "video_id": video_id,
                    "title": snippet.get("title", "Untitled"),
                    "thumbnail": _best_thumbnail(snippet.get("thumbnails", {})),
                    "published_at": entry.get("contentDetails", {}).get("videoPublishedAt")
                    or snippet.get("publishedAt", ""),
                    "position": snippet.get("position", len(videos)),
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return videos


async def _uploads_playlist_id(
    client: httpx.AsyncClient, channel_id: str, api_key: str
) -> Optional[str]:
    """The channel's auto-generated uploads playlist — every public video, paginable."""
    data = await _get_with_retry(
        client,
        f"{_YT_API}/channels",
        {"part": "contentDetails", "id": channel_id, "key": api_key},
    )
    items = data.get("items", [])
    if not items:
        return None
    return items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")


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


def _ytdlp_json3_segments(url: str, video_id: str) -> List[dict]:
    """Download the English json3 caption track via yt-dlp and map it to timed segments.

    Lets yt-dlp do the fetch (its hardened HTTP client is what gets past the datacenter-IP
    blocking), writing the track to a temp file, then parses json3 into {start, duration,
    text}. Returns [] when the video simply has no English captions. Synchronous — call via
    a thread from async code.
    """
    import glob
    import json as _json
    import tempfile

    from yt_dlp import YoutubeDL

    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,        # manual captions, preferred when present
            "writeautomaticsub": True,     # fall back to auto-generated
            "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
            "subtitlesformat": "json3",
            "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
        }
        # YouTube blocks caption fetches from datacenter IPs; route through a residential
        # proxy when configured so the exit IP looks like an ordinary viewer.
        proxy = os.getenv("YT_DLP_PROXY")
        if proxy:
            ydl_opts["proxy"] = proxy
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = sorted(glob.glob(os.path.join(tmp, "*.json3")))
        if not files:
            return []
        with open(files[0], "r", encoding="utf-8") as f:
            data = _json.load(f)

    segments: List[dict] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue  # timing-only or empty events
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        segments.append({
            "start": ev.get("tStartMs", 0) / 1000.0,
            "duration": ev.get("dDurationMs", 0) / 1000.0,
            "text": text,
        })
    return segments
