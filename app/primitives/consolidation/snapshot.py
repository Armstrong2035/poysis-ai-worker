import asyncio
import os
from typing import AsyncIterator, List

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.connectors.google_drive import GoogleDriveConnector
from app.primitives.consolidation.processors.document import DocumentProcessor
from app.primitives.consolidation.processors.spreadsheet import SpreadsheetProcessor
from app.primitives.consolidation.processors.pdf import PDFProcessor
from app.primitives.consolidation.processors.base import ProcessedChunk


class SnapshotRunner:
    """
    Streams chunks one doc at a time — fetch, process, yield, discard.
    Never holds more than one document's chunks in memory at once.
    """

    def __init__(self, scope: ScopeConfig):
        self.scope = scope
        self.docs_processed = 0
        self.errors: List[str] = []
        self._doc_processor = DocumentProcessor()
        self._sheet_processor = SpreadsheetProcessor()
        self._pdf_processor = PDFProcessor()

    async def discover(self) -> dict:
        total_files = 0
        total_bytes = 0
        breakdown = {"document": 0, "spreadsheet": 0, "pdf": 0}
        mime_types_found = {}

        if "google_drive" in self.scope.sources:
            connector = GoogleDriveConnector(access_token=self.scope.google_access_token or "")
            async for item in connector.list_items(self.scope):
                total_files += 1
                total_bytes += item.size_bytes
                if item.content_type in breakdown:
                    breakdown[item.content_type] += 1
                mime_types_found[item.mime_type] = mime_types_found.get(item.mime_type, 0) + 1

        return {
            "workspace_id": self.scope.workspace_id,
            "total_files": total_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "breakdown": breakdown,
            "mime_types_found": mime_types_found,
        }

    async def stream(self) -> AsyncIterator[ProcessedChunk]:
        """
        Async generator — yields chunks one at a time as each doc is processed.
        Tracks stats on self.docs_processed and self.errors.
        """
        if "google_drive" in self.scope.sources:
            connector = GoogleDriveConnector(access_token=self.scope.google_access_token or "")
            async for item in connector.list_items(self.scope):
                try:
                    chunks = await self._process_item(item, connector)
                    for chunk in chunks:
                        yield chunk
                    self.docs_processed += 1
                except Exception as e:
                    self.errors.append(f"[{item.source_id}] {item.title}: {e}")

    async def _process_item(self, item: RawSourceItem, connector: GoogleDriveConnector) -> List[ProcessedChunk]:
        if item.content_type == "document":
            text = await connector.fetch_text(item)
            return await self._doc_processor.process(item, text)

        if item.content_type == "spreadsheet":
            if item.mime_type == "application/vnd.google-apps.spreadsheet":
                text = await connector.fetch_text(item)
                file_path = await self._write_temp(text, ".csv")
            else:
                file_path = await connector.fetch_file(item)
            try:
                return await self._sheet_processor.process(item, file_path)
            finally:
                self._cleanup(file_path)

        if item.content_type == "pdf":
            file_path = await connector.fetch_file(item)
            try:
                return await self._pdf_processor.process(item, file_path)
            finally:
                self._cleanup(file_path)

        return []

    async def _write_temp(self, text: str, suffix: str) -> str:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8")
        tmp.write(text)
        tmp.close()
        return tmp.name

    def _cleanup(self, file_path: str) -> None:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
