import asyncio
from typing import Dict, Any, Optional

from app.primitives.knowledge.vector_store import VectorService
from app.primitives.database import DatabaseService
from app.primitives.consolidation.categorizer import CategorizerEngine

MIN_DOCS_TO_CLUSTER = 10


class ClusteringEngine:
    def __init__(self, db: Optional[DatabaseService] = None):
        self.vector_service = VectorService()
        self.db = db

    async def run_clustering(self, workspace_id: str) -> Dict[str, Any]:
        namespace = f"consolidation_{workspace_id}"

        print(f"[Clustering] Fetching document list for '{namespace}'...")
        docs = await asyncio.to_thread(
            self.vector_service.list_documents_with_snippets, namespace
        )

        if len(docs) < MIN_DOCS_TO_CLUSTER:
            print(f"[Clustering] Only {len(docs)} documents — skipping (need {MIN_DOCS_TO_CLUSTER}+)")
            return {"status": "skipped", "reason": f"Too few documents ({len(docs)})", "workspace_id": workspace_id}

        return await CategorizerEngine(self.db, self.vector_service).run_categorization(workspace_id)
