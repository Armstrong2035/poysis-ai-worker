"""Poysis adapters. Dependencies and grants are supplied by the trusted host.

The engine package does not import the Poysis application, initialize cloud
clients, or read credentials. Other clients can implement the same retrieve API.
"""

import asyncio
import json
import math
from datetime import datetime, timezone

from .context import digest
from .models import DocumentChunk, Fact

ADDITIVE = {
    "ga4": {"sessions", "engagedSessions", "keyEvents", "eventCount", "screenPageViews",
            "totalRevenue", "purchaseRevenue", "transactions"},
    "search_console": {"clicks", "impressions"},
}


class PoysisEvidenceAdapter:
    def __init__(self, scope, marketing_store, *, dataset_grants=(), embedder=None,
                 vector_store=None, connection_ids=(), allow_all_documents=False):
        self.scope = scope
        self.marketing = marketing_store
        self.dataset_grants = tuple(grant.model_copy(deep=True) for grant in dataset_grants)
        self.embedder = embedder
        self.vectors = vector_store
        self.connection_ids = list(connection_ids)
        self.allow_all_documents = allow_all_documents

    def _authorize_dataset(self, dataset):
        for grant in self.dataset_grants:
            if ((grant.source, grant.property_id, grant.dataset) ==
                    (dataset.source, dataset.property_id, dataset.dataset)
                    and set(dataset.metrics) <= set(grant.metrics)
                    and all(dataset.dimensions.get(k) == v for k, v in grant.dimensions.items())):
                return
        raise PermissionError("Dataset or metric is outside the adapter's grants")

    async def retrieve(self, scope, operation):
        if scope != self.scope:
            raise PermissionError("Adapter scope mismatch")
        if operation.kind == "structured":
            return await self._facts(operation)
        if operation.kind == "semantic":
            return await self._documents(operation)
        raise ValueError("Unsupported adapter operation")

    async def _facts(self, operation):
        dataset, period = operation.dataset, operation.period
        if dataset is None or period is None:
            raise ValueError("Structured operation requires dataset and period")
        self._authorize_dataset(dataset)
        if not set(dataset.metrics) <= ADDITIVE[dataset.source]:
            raise ValueError("Only registered additive metrics can be aggregated; distinct users and rates are unsupported")
        rows = await self.marketing.fetch(self.scope.workspace_id, dataset.source, dataset.property_id,
                                          dataset.dataset, period.start, period.end)
        if len(rows) > 100000:
            raise ValueError("Structured result exceeds complete-evaluation limit")
        rows = [r for r in rows if all(r["dimensions"].get(k) == v for k, v in dataset.dimensions.items())]
        if not rows:
            return []
        locator = "poysis:marketing:" + json.dumps({"source": dataset.source, "property_id": dataset.property_id,
                                                   "dataset": dataset.dataset, "dimensions": dataset.dimensions}, sort_keys=True)
        captured = datetime.now(timezone.utc)
        snapshot_hash = digest(rows)
        result = []
        for metric in dict.fromkeys(dataset.metrics):
            values = [r["metrics"].get(metric) for r in rows]
            if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
                raise ValueError("Metric is absent or invalid in some rows; cannot establish a complete total")
            value = math.fsum(values)
            result.append(Fact(
                id=digest([self.scope.model_dump(), locator, str(period), metric, snapshot_hash]),
                scope=self.scope, source=dataset.source, locator=locator, captured_at=captured,
                period=period, metric=metric, value=value,
                unit="source_currency" if metric in {"totalRevenue", "purchaseRevenue"} else "count",
                dimensions=dataset.dimensions,
                definition=f"SUM({metric}) over selected daily rows in a fixed report definition.",
                limitations=["Completeness and tracking-definition stability are unverified.",
                             "Aggregation assumes the selected dataset has non-overlapping dimensions.",
                             f"Source row count: {len(rows)}; row snapshot SHA256: {snapshot_hash}.",
                             "Underlying daily rows are mutable; this aggregate and its row hash are snapshotted."]
            ))
        return result

    async def _documents(self, operation):
        if self.embedder is None or self.vectors is None:
            raise RuntimeError("Document retrieval is not connected")
        if not self.allow_all_documents and not self.connection_ids:
            raise PermissionError("No document connections have been granted")
        embedding = await self.embedder.get_embedding(operation.query, task_type="retrieval_query")
        rows = await asyncio.to_thread(self.vectors.query_vectors, embedding,
                                       namespace=f"consolidation_{self.scope.workspace_id}", top_k=6,
                                       connection_ids=None if self.allow_all_documents else self.connection_ids)
        result = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if not self.allow_all_documents and str(metadata.get("connection_id", "")) not in self.connection_ids:
                raise PermissionError("Vector backend returned a document outside granted connections")
            text = metadata.get("_text") or metadata.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            limitations = ["A retrieved document is an author's statement, not independently verified fact.",
                           "Semantic retrieval is selective and does not establish document coverage."]
            if len(text) > 4000:
                limitations.append("Chunk text truncated to 4000 characters; source hash covers the full text.")
            result.append(DocumentChunk(
                id=digest([self.scope.model_dump(), "chunk", str(row["id"]), text]), scope=self.scope,
                source="poysis_documents", locator=f"poysis:consolidation_{self.scope.workspace_id}:chunk:{row['id']}",
                captured_at=datetime.now(timezone.utc), limitations=limitations,
                title=str(metadata.get("title") or "Untitled source")[:200], text=text[:4000],
                source_modified_at=str(metadata["modified_time"]) if metadata.get("modified_time") else None))
        return result
