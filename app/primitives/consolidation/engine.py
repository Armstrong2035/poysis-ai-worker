import time
from typing import Callable, Dict, Any, List, Optional

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.consolidation.processors.base import ProcessedChunk
from app.primitives.knowledge.engine import get_knowledge_engine
from app.primitives.database import DatabaseService
from llama_index.core import Document

BATCH_SIZE = 200

# Floor on the gap between progress writes. Every resolved document now
# heartbeats, and a Drive run resolves several per second — without this the
# run would spend itself on database writes. Loose enough to keep the write
# rate sane, tight enough that a client watching `updated_at` can still tell
# "working" from "stalled".
PROGRESS_INTERVAL_SECONDS = 5


class ConsolidationEngine:
    """
    Consumes the SnapshotRunner stream in batches of 50.
    Only one batch lives in memory at a time — fetch, embed, discard, repeat.
    """

    def __init__(self, db: Optional[DatabaseService] = None):
        self.knowledge = get_knowledge_engine()
        self.db = db

    def _namespace(self, workspace_id: str) -> str:
        return f"consolidation_{workspace_id}"

    _TRANSCRIPT_SOURCES = {"youtube"}

    def _to_documents(self, chunks: List[ProcessedChunk], offset: int) -> List[Document]:
        return [
            Document(
                text=chunk.text,
                id_=f"{chunk.source_id}_{offset + i}",
                metadata={
                    "source_id": chunk.source_id,
                    "source_type": chunk.source_type,
                    "title": chunk.title,
                    "url": chunk.url,
                    **({
                        "page": chunk.extra_metadata.get("page"),
                        "total_pages": chunk.extra_metadata.get("total_pages"),
                    } if chunk.extra_metadata.get("page") else {}),
                }
            )
            for i, chunk in enumerate(chunks)
        ]

    async def run_snapshot(
        self,
        scope: ScopeConfig,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        runner = SnapshotRunner(scope=scope)
        namespace = self._namespace(scope.workspace_id)

        batch: List[ProcessedChunk] = []
        transcript_batch: List[ProcessedChunk] = []
        total_chunks = 0
        total_vectors = 0
        batch_number = 0
        last_reported_docs = -1  # forces an initial event the moment the first doc completes
        last_emit = 0.0

        def _emit(force: bool = False):
            nonlocal last_emit
            if not progress_callback:
                return
            now = time.monotonic()
            if not force and now - last_emit < PROGRESS_INTERVAL_SECONDS:
                return
            last_emit = now
            progress_callback({
                "vectors_indexed": total_vectors,
                "docs_processed": runner.docs_processed,
                "docs_skipped": runner.docs_skipped,
                "docs_orphaned": runner.docs_orphaned,
                "docs_failed": runner.docs_failed,
            })

        # Heartbeat on every resolved document, not just on chunks reaching us.
        runner.on_activity = _emit

        async def _flush_doc_batch():
            nonlocal total_vectors, batch_number
            if not batch:
                return
            documents = self._to_documents(batch, offset=total_chunks - len(batch))
            indexed = await self.knowledge._run_ingestion_pipeline(namespace, documents)
            total_vectors += indexed
            batch_number += 1
            print(f"[ConsolidationEngine] Doc batch {batch_number} — {indexed} vectors")
            batch.clear()

        async def _flush_transcript_batch():
            nonlocal total_vectors
            if not transcript_batch:
                return
            indexed = await self.knowledge.embed_and_store(namespace, list(transcript_batch))
            total_vectors += indexed
            print(f"[ConsolidationEngine] Transcript batch — {indexed} vectors")
            transcript_batch.clear()

        async for chunk in runner.stream():
            total_chunks += 1
            if chunk.source_type in self._TRANSCRIPT_SOURCES:
                transcript_batch.append(chunk)
            else:
                batch.append(chunk)

            if runner.docs_processed != last_reported_docs:
                _emit()
                last_reported_docs = runner.docs_processed

            if len(batch) >= BATCH_SIZE:
                await _flush_doc_batch()
                await self._flush_completed_files(scope.workspace_id, runner)
                _emit()

            if len(transcript_batch) >= BATCH_SIZE:
                await _flush_transcript_batch()
                await self._flush_completed_files(scope.workspace_id, runner)
                _emit()

        await _flush_doc_batch()
        await _flush_transcript_batch()
        await self._flush_completed_files(scope.workspace_id, runner)
        _emit(force=True)

        return {
            "workspace_id": scope.workspace_id,
            "docs_processed": runner.docs_processed,
            "docs_skipped": runner.docs_skipped,
            "docs_orphaned": runner.docs_orphaned,
            "docs_failed": runner.docs_failed,
            "chunks_produced": total_chunks,
            "vectors_indexed": total_vectors,
            "errors": runner.errors,
            "partial": runner.has_more,
        }

    async def _flush_completed_files(self, workspace_id: str, runner: SnapshotRunner):
        if self.db and runner.completed_files:
            await self.db.mark_files_indexed(workspace_id, runner.completed_files.copy())
            runner.completed_files.clear()
