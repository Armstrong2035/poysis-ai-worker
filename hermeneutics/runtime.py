"""Poysis runtime factory: DeepSeek inference and existing Postgres evidence."""

from functools import lru_cache

from .adapters import PoysisEvidenceAdapter
from .deepseek import DeepSeekModel
from .engine import HermeneuticsEngine
from .postgres import PostgresRepository


class WorkspaceAdapter:
    """For the Poysis API, authorized membership grants workspace-wide evidence.

    Other clients must use their own factory/grants; caller-provided client IDs
    are never accepted by the API.
    """
    def __init__(self, scope):
        if scope.client_id != "poysis":
            raise PermissionError("Unknown client")
        self.scope = scope

    async def retrieve(self, scope, operation):
        from app.primitives.marketing.store import PostgresMarketingStore

        if scope != self.scope:
            raise PermissionError("Workspace mismatch")
        adapter = PoysisEvidenceAdapter(scope, PostgresMarketingStore(),
                                        dataset_grants=[operation.dataset] if operation.dataset else [])
        if operation.kind == "semantic":
            # Preserve the embedding space used at ingestion. A language model
            # switch must not silently swap vector models and corrupt similarity.
            embedder, vectors = document_clients()
            adapter.embedder, adapter.vectors = embedder, vectors
            adapter.allow_all_documents = True
        return await adapter.retrieve(scope, operation)


@lru_cache(maxsize=1)
def document_clients():
    from app.primitives.knowledge.engine import get_knowledge_engine
    engine = get_knowledge_engine()
    class StoredVectorEmbedder:
        async def get_embedding(self, text, task_type="retrieval_query"):
            return await engine.embed_model.aget_query_embedding(text)
    return StoredVectorEmbedder(), engine.vector_service


@lru_cache(maxsize=1)
def build_engine():
    model = DeepSeekModel()
    return HermeneuticsEngine(PostgresRepository(), WorkspaceAdapter, model,
                              model_timeout=190, retrieval_timeout=45)
