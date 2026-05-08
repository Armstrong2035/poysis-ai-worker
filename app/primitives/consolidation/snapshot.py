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


MAX_CHUNKS_PER_DOC = 2000
SIZE_WARNING_BYTES = 5 * 1024 * 1024  # 5 MB


class SnapshotRunner:
    """
    Streams chunks one doc at a time — fetch, process, yield, discard.
    Never holds more than one document's chunks in memory at once.
    """

    def __init__(self, scope: ScopeConfig):
        self.scope = scope
        self.docs_processed = 0
        self.docs_skipped = 0
        self.docs_orphaned = 0
        self.has_more = False
        self.errors: List[str] = []
        self.completed_files: List[dict] = []  # flushed to DB after each batch
        self._doc_processor = DocumentProcessor()
        self._sheet_processor = SpreadsheetProcessor()
        self._pdf_processor = PDFProcessor()

    async def discover(self) -> dict:
        total_files = 0
        total_bytes = 0
        breakdown = {"document": 0, "spreadsheet": 0, "pdf": 0}
        mime_types_found = {}
        large_files = []

        if "google_drive" in self.scope.sources:
            connector = GoogleDriveConnector(access_token=self.scope.google_access_token or "")
            async for item in connector.list_items(self.scope):
                total_files += 1
                total_bytes += item.size_bytes
                if item.content_type in breakdown:
                    breakdown[item.content_type] += 1
                mime_types_found[item.mime_type] = mime_types_found.get(item.mime_type, 0) + 1

                if item.size_bytes >= SIZE_WARNING_BYTES:
                    large_files.append({
                        "source_id": item.source_id,
                        "title": item.title,
                        "size_mb": round(item.size_bytes / (1024 * 1024), 2),
                        "content_type": item.content_type,
                        "recommendation": (
                            f"Large file — will be capped at {MAX_CHUNKS_PER_DOC} chunks. "
                            "Consider splitting or filtering before indexing."
                        ),
                    })

        return {
            "workspace_id": self.scope.workspace_id,
            "total_files": total_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "breakdown": breakdown,
            "mime_types_found": mime_types_found,
            "large_files": large_files,
        }

    async def stream(self) -> AsyncIterator[ProcessedChunk]:
        """
        Async generator — yields chunks one at a time as each doc is processed.
        - Skips already-indexed files (etag match) and previously orphaned unchanged files.
        - Caps chunks per doc at MAX_CHUNKS_PER_DOC.
        - Orphans files that fail so they are not retried until the file changes.
        """
        if "google_drive" in self.scope.sources:
            connector = GoogleDriveConnector(access_token=self.scope.google_access_token or "")
            items_seen = 0
            async for item in connector.list_items(self.scope):
                items_seen += 1
                indexed_etag = self.scope.indexed_files.get(item.source_id)

                if indexed_etag:
                    if indexed_etag == item.etag:
                        self.docs_skipped += 1
                        continue
                    # Previously orphaned — skip unless the file has changed
                    if indexed_etag.startswith("ORPHANED:") and indexed_etag[len("ORPHANED:"):] == item.etag:
                        self.docs_orphaned += 1
                        continue

                # Auto-orphan files over the size threshold — retry automatically if file changes
                if item.size_bytes >= SIZE_WARNING_BYTES:
                    print(f"[SnapshotRunner] '{item.title}' ({item.size_bytes / (1024*1024):.1f}MB) exceeds size threshold — orphaning")
                    self.errors.append(f"[{item.source_id}] {item.title}: file too large ({item.size_bytes / (1024*1024):.1f}MB), orphaned")
                    self.completed_files.append({"source_id": item.source_id, "etag": f"ORPHANED:{item.etag}"})
                    self.docs_orphaned += 1
                    continue

                try:
                    import time
                    size_kb = item.size_bytes / 1024
                    print(f"[STEP 1 FETCH ] '{item.title}' | {size_kb:.1f}KB | type={item.content_type}")
                    t0 = time.perf_counter()
                    chunks = await self._process_item(item, connector)
                    process_secs = time.perf_counter() - t0
                    chunk_count = len(chunks)
                    print(f"[STEP 2 PARSE ] '{item.title}' | {chunk_count} chunks | {process_secs:.2f}s")
                    if chunk_count > MAX_CHUNKS_PER_DOC:
                        print(f"[STEP 2 PARSE ] WARNING: {chunk_count} chunks exceeds {MAX_CHUNKS_PER_DOC} — large document, indexing in full")
                    for chunk in chunks:
                        yield chunk
                    self.docs_processed += 1
                    self.completed_files.append({"source_id": item.source_id, "etag": item.etag})
                    print(f"[STEP 2 PARSE ] '{item.title}' queued for embedding ✓")
                except Exception as e:
                    size_kb = item.size_bytes / 1024
                    print(f"[STEP 1 FETCH ] FAILED '{item.title}' | {size_kb:.1f}KB | {e}")
                    self.errors.append(f"[{item.source_id}] {item.title}: {e}")
                    # Orphan so we don't retry an unchanging broken file on every snapshot
                    self.completed_files.append({"source_id": item.source_id, "etag": f"ORPHANED:{item.etag}"})

            if self.scope.doc_limit > 0 and items_seen >= self.scope.doc_limit:
                self.has_more = True

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
