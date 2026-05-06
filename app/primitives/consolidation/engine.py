from typing import Dict, Any

from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner, SnapshotResult
from app.primitives.knowledge.engine import KnowledgeEngine
from llama_index.core import Document


class ConsolidationEngine:
    """
    Orchestrates the full consolidation pipeline:
      1. SnapshotRunner fetches + processes files into chunks
      2. KnowledgeEngine embeds chunks into Pinecone under a workspace namespace
    """

    def __init__(self):
        self.knowledge = KnowledgeEngine()

    def _namespace(self, workspace_id: str) -> str:
        return f"consolidation_{workspace_id}"

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
        documents = [
            Document(
                text=chunk.text,
                id_=f"{chunk.source_id}_{i}",
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
            for i, chunk in enumerate(result.all_chunks)
        ]

        vectors_indexed = await self.knowledge._run_ingestion_pipeline(
            namespace, documents, chunk=False
        )

        return {
            "workspace_id": scope.workspace_id,
            "docs_processed": result.docs_processed,
            "chunks_produced": len(result.all_chunks),
            "vectors_indexed": vectors_indexed,
            "errors": result.errors,
        }
