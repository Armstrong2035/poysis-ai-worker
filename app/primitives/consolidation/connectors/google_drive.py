import os
import tempfile
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator
import httpx

from app.primitives.consolidation.connectors.base import BaseConnector, RawSourceItem
from app.primitives.consolidation.scope import ScopeConfig

# Google Docs mime types we can export as plain text
TEXT_EXPORTABLE = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
}

# Google Sheets exported as CSV
SHEET_EXPORTABLE = {
    "application/vnd.google-apps.spreadsheet": "text/csv",
}

# Binary files we download directly
PDF_MIME = "application/pdf"
BINARY_SPREADSHEETS = {"text/csv", "application/vnd.ms-excel",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

SUPPORTED_MIME_TYPES = (
    set(TEXT_EXPORTABLE) | set(SHEET_EXPORTABLE) | {PDF_MIME} | BINARY_SPREADSHEETS
)


def _content_type_for_mime(mime: str) -> str:
    if mime in TEXT_EXPORTABLE:
        return "document"
    if mime in SHEET_EXPORTABLE or mime in BINARY_SPREADSHEETS:
        return "spreadsheet"
    if mime == PDF_MIME:
        return "pdf"
    return "document"


class GoogleDriveConnector(BaseConnector):
    BASE_URL = "https://www.googleapis.com/drive/v3"

    def __init__(self, access_token: str):
        self.access_token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def list_items(self, scope: ScopeConfig) -> AsyncIterator[RawSourceItem]:
        query_parts = ["trashed = false"]

        if scope.time_window_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=scope.time_window_days)
            query_parts.append(f"modifiedTime > '{cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}'")

        if scope.drive_folder_ids:
            parent_clauses = " or ".join(
                f"'{fid}' in parents" for fid in scope.drive_folder_ids
            )
            query_parts.append(f"({parent_clauses})")

        mime_clause = " or ".join(f"mimeType = '{m}'" for m in SUPPORTED_MIME_TYPES)
        query_parts.append(f"({mime_clause})")

        q = " and ".join(query_parts)
        page_token = None
        fetched = 0
        limit = scope.doc_limit if scope.doc_limit != -1 else float("inf")

        async with httpx.AsyncClient() as client:
            while fetched < limit:
                params = {
                    "q": q,
                    "fields": "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,size)",
                    "pageSize": min(100, int(limit - fetched)) if limit != float("inf") else 100,
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{self.BASE_URL}/files",
                    headers=self._headers,
                    params=params
                )
                resp.raise_for_status()
                data = resp.json()

                for f in data.get("files", []):
                    mime = f.get("mimeType", "")
                    yield RawSourceItem(
                        source_id=f["id"],
                        source_type="google_drive",
                        title=f.get("name", "Untitled"),
                        url=f.get("webViewLink", ""),
                        etag=f.get("modifiedTime", ""),
                        last_modified=datetime.fromisoformat(
                            f["modifiedTime"].replace("Z", "+00:00")
                        ),
                        content_type=_content_type_for_mime(mime),
                        mime_type=mime,
                        size_bytes=int(f.get("size", 0)),
                    )
                    fetched += 1
                    if fetched >= limit:
                        return

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

    async def fetch_text(self, item: RawSourceItem) -> str:
        """Export Google Docs / Slides as plain text."""
        export_mime = TEXT_EXPORTABLE.get(item.mime_type) or \
                      SHEET_EXPORTABLE.get(item.mime_type, "text/plain")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.BASE_URL}/files/{item.source_id}/export",
                headers=self._headers,
                params={"mimeType": export_mime},
            )
            resp.raise_for_status()
            return resp.text

    async def fetch_file(self, item: RawSourceItem) -> str:
        """Download binary file (PDF, Excel, CSV) to a temp file. Returns file path."""
        ext = _ext_from_mime(item.mime_type)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.close()

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "GET",
                f"{self.BASE_URL}/files/{item.source_id}",
                headers=self._headers,
                params={"alt": "media"},
            ) as resp:
                resp.raise_for_status()
                with open(tmp.name, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192):
                        f.write(chunk)

        return tmp.name


def _ext_from_mime(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "text/csv": ".csv",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }.get(mime, ".bin")
