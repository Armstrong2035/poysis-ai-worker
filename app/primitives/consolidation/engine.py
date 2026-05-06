from typing import Dict, Any, List

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner, SnapshotResult
from app.primitives.consolidation.processors.base import ProcessedChunk
from app.primitives.knowledge.engine import KnowledgeEngine
from llama_index.core import Document

BATCH_SIZE = 50


class ConsolidationEngine:
    """
    Orchestrates the full consolidation pipeline:
      1. SnapshotRunner fetches + processes files into chunks
      2. KnowledgeEngine embeds and indexes chunks in batches of 50
    """

    def __init__(self):
        self.knowledge = KnowledgeEngine()

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
        result: SnapshotResult = await runner.run()

        if not result.all_chunks:
            return {
                "workspace_id": scope.workspace_id,
                "docs_processed": result.docs_processed,
                "chunks_produced": 0,
                "vectors_indexed": 0,
                "errors": result.errors,
            }

        namespace = self._namespace(scope.workspace_id)
        total_vectors = 0
        chunks = result.all_chunks

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i: i + BATCH_SIZE]
            documents = self._to_documents(batch, offset=i)
            indexed = await self.knowledge._run_ingestion_pipeline(
                namespace, documents, chunk=True
            )
            total_vectors += indexed
            print(f"[ConsolidationEngine] Batch {i // BATCH_SIZE + 1} — {indexed} vectors indexed")

        return {
            "workspace_id": scope.workspace_id,
            "docs_processed": result.docs_processed,
            "chunks_produced": len(result.all_chunks),
            "vectors_indexed": total_vectors,
            "errors": result.errors,
        }
