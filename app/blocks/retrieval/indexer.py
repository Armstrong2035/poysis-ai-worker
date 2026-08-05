from typing import List, Dict, Any
from app.primitives.knowledge.engine import KnowledgeEngine, get_knowledge_engine

class IndexerService:
    def __init__(self):
        self.engine = get_knowledge_engine()
        
    async def ingest_documents(self, notebook_id: str, documents: List[Dict[str, Any]]) -> int:
        """
        Consumes documents and delegates to the KnowledgeEngine.
        """
        return await self.engine.upsert_documents(notebook_id, documents)
