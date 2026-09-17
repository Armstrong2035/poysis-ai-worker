from datetime import datetime, timezone, timedelta
from typing import AsyncIterator

import httpx

from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.connectors.nango_base import NangoConnector
from app.primitives.consolidation.scope import ScopeConfig

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionConnector(NangoConnector):
    def __init__(self, connection_id: str, provider: str = "notion"):
        super().__init__(connection_id=connection_id, provider=provider)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def list_items(self, scope: ScopeConfig) -> AsyncIterator[RawSourceItem]:
        token = await self._get_token()
        headers = self._headers(token)

        cutoff: datetime | None = None
        if scope.time_window_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=scope.time_window_days)

        start_cursor: str | None = None
        fetched = 0
        limit = scope.doc_limit if scope.doc_limit != -1 else float("inf")

        async with httpx.AsyncClient(timeout=30) as client:
            while fetched < limit:
                body: dict = {"page_size": min(100, int(limit - fetched)) if limit != float("inf") else 100}
                if start_cursor:
                    body["start_cursor"] = start_cursor

                resp = await client.post(
                    f"{_NOTION_API}/search",
                    headers=headers,
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()

                for obj in data.get("results", []):
                    obj_type = obj.get("object")
                    if obj_type not in ("page", "database"):
                        continue

                    last_edited = obj.get("last_edited_time", "")
                    if last_edited and cutoff:
                        dt = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
                        if dt < cutoff:
                            continue

                    title = _extract_title(obj)
                    url = obj.get("url", "")
                    obj_id = obj["id"]

                    yield RawSourceItem(
                        source_id=obj_id,
                        source_type="notion",
                        title=title,
                        url=url,
                        etag=last_edited,
                        last_modified=datetime.fromisoformat(last_edited.replace("Z", "+00:00")) if last_edited else datetime.now(timezone.utc),
                        content_type="document",
                        size_bytes=0,
                    )
                    fetched += 1
                    if fetched >= limit:
                        return

                if not data.get("has_more"):
                    break
                start_cursor = data.get("next_cursor")

    async def fetch_text(self, item: RawSourceItem) -> str:
        token = await self._get_token()
        return await _fetch_blocks_as_text(item.source_id, self._headers(token))


async def _fetch_blocks_as_text(block_id: str, headers: dict, depth: int = 0) -> str:
    """Recursively fetch Notion block children and return plain text."""
    if depth > 3:
        return ""

    lines: list[str] = []
    start_cursor: str | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params: dict = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor

            resp = await client.get(
                f"{_NOTION_API}/blocks/{block_id}/children",
                headers=headers,
                params=params,
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()

            for block in data.get("results", []):
                text = _block_to_text(block)
                if text:
                    lines.append(text)
                if block.get("has_children"):
                    child_text = await _fetch_blocks_as_text(block["id"], headers, depth + 1)
                    if child_text:
                        lines.append(child_text)

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")

    return "\n".join(lines)


def _block_to_text(block: dict) -> str:
    block_type = block.get("type", "")
    content = block.get(block_type, {})
    rich_text = content.get("rich_text", [])
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _extract_title(obj: dict) -> str:
    obj_type = obj.get("object")
    if obj_type == "page":
        props = obj.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                parts = prop.get("title", [])
                return "".join(p.get("plain_text", "") for p in parts)
    elif obj_type == "database":
        parts = obj.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts)
    return "Untitled"
