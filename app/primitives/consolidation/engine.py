from typing import Dict, Any, List, Optional

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner
from app.primitives.consolidation.processors.base import ProcessedChunk
from app.primitives.knowledge.engine import KnowledgeEngine
from app.primitives.database import DatabaseService
from llama_index.core import Document

BATCH_SIZE = 50


class ConsolidationEngine:
    """
    Consumes the SnapshotRunner stream in batches of 50.
    Only one batch lives in memory at a time — fetch, embed, discard, repeat.
    """

    def __init__(self, db: Optional[DatabaseService] = None):
        self.knowledge = KnowledgeEngine()
        self.db = db

    def _namespace(self, workspace_id: str) -> str:
        return f"consolidation_{workspace_id}"

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
                        "page": chunk.extra_metadata["page"],
                        "total_pages": chunk.extra_metadata["total_pages"],
                    } if chunk.extra_metadata.get("page") else {}),
                }
            )
            for i, chunk in enumerate(chunks)
        ]

    async def run_snapshot(self, scope: ScopeConfig) -> Dict[str, Any]:
        runner = SnapshotRunner(scope=scope)
        namespace = self._namespace(scope.workspace_id)

        batch: List[ProcessedChunk] = []
        total_chunks = 0
        total_vectors = 0
        batch_number = 0

        async for chunk in runner.stream():
            batch.append(chunk)
            total_chunks += 1

            if len(batch) >= BATCH_SIZE:
                documents = self._to_documents(batch, offset=total_chunks - len(batch))
                indexed = await self.knowledge._run_ingestion_pipeline(namespace, documents, chunk=False)
                total_vectors += indexed
                batch_number += 1
                print(f"[ConsolidationEngine] Batch {batch_number} — {indexed} vectors indexed")
                await self._flush_completed_files(scope.workspace_id, runner)
                batch.clear()

        # Embed any remaining chunks
        if batch:
            documents = self._to_documents(batch, offset=total_chunks - len(batch))
            indexed = await self.knowledge._run_ingestion_pipeline(namespace, documents, chunk=False)
            total_vectors += indexed
            print(f"[ConsolidationEngine] Final batch — {indexed} vectors indexed")
            await self._flush_completed_files(scope.workspace_id, runner)

        return {
            "workspace_id": scope.workspace_id,
            "docs_processed": runner.docs_processed,
            "docs_skipped": runner.docs_skipped,
            "chunks_produced": total_chunks,
            "vectors_indexed": total_vectors,
            "errors": runner.errors,
        }

    async def _flush_completed_files(self, workspace_id: str, runner: SnapshotRunner):
        if self.db and runner.completed_files:
            await self.db.mark_files_indexed(workspace_id, runner.completed_files.copy())
            runner.completed_files.clear()
