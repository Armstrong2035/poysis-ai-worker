import unittest
from datetime import date
from unittest.mock import AsyncMock, Mock

from hermeneutics.adapters import PoysisEvidenceAdapter
from hermeneutics.models import Dataset, Period, RetrievalOperation, Scope


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = Scope(client_id="poysis", workspace_id="workspace-a")
        self.dataset = Dataset(source="ga4", property_id="123", dataset="v1", metrics=["sessions"],
                               dimensions={"sessionDefaultChannelGroup": "Organic Search"})
        self.marketing = AsyncMock()
        self.embedder = AsyncMock()
        self.vectors = Mock()
        self.adapter = PoysisEvidenceAdapter(self.scope, self.marketing, dataset_grants=[self.dataset],
                                             embedder=self.embedder, vector_store=self.vectors, connection_ids=["allowed"])
        self.operation = RetrievalOperation(kind="structured", purpose="Compare", dataset=self.dataset,
                                             period=Period(start=date(2026, 8, 1), end=date(2026, 8, 2)))

    async def test_authorized_fact_aggregation_and_dimension_filter(self):
        self.marketing.fetch.return_value = [
            {"day": date(2026, 8, i), "dimensions": {"sessionDefaultChannelGroup": channel}, "metrics": {"sessions": sessions}}
            for i, channel, sessions in [(1, "Organic Search", 5), (2, "Organic Search", 7), (1, "Paid Search", 100)]]
        facts = await self.adapter.retrieve(self.scope, self.operation)
        self.assertEqual(facts[0].value, 12)
        self.marketing.fetch.assert_awaited_with("workspace-a", "ga4", "123", "v1", date(2026, 8, 1), date(2026, 8, 2))

    async def test_property_scope_and_dimension_grants_cannot_be_broadened(self):
        for changes in ({"property_id": "other"}, {"dimensions": {}}, {"metrics": ["keyEvents"]}):
            self.operation.dataset = self.dataset.model_copy(update=changes)
            with self.assertRaises(PermissionError):
                await self.adapter.retrieve(self.scope, self.operation)
        self.marketing.fetch.assert_not_called()

    async def test_nonadditive_and_missing_metrics_are_rejected(self):
        nonadditive = Dataset(source="ga4", property_id="123", dataset="v1", metrics=["activeUsers"])
        self.adapter.dataset_grants = (nonadditive,)
        self.operation.dataset = nonadditive
        with self.assertRaises(ValueError):
            await self.adapter.retrieve(self.scope, self.operation)
        self.adapter.dataset_grants = (self.dataset,)
        self.operation.dataset = self.dataset
        self.marketing.fetch.return_value = [{"dimensions": self.dataset.dimensions, "metrics": {}}]
        with self.assertRaises(ValueError):
            await self.adapter.retrieve(self.scope, self.operation)

    async def test_document_namespace_connection_grants_and_source_text(self):
        self.embedder.get_embedding.return_value = [0.1, 0.2]
        self.vectors.query_vectors.return_value = [{"id": "chunk-1", "metadata": {
            "connection_id": "allowed", "_text": "Source evidence", "title": "Interview"}}]
        operation = RetrievalOperation(kind="semantic", query="Why?", purpose="Find documents")
        docs = await self.adapter.retrieve(self.scope, operation)
        self.assertEqual(docs[0].text, "Source evidence")
        self.assertEqual(self.vectors.query_vectors.call_args.kwargs["namespace"], "consolidation_workspace-a")
        self.assertEqual(self.vectors.query_vectors.call_args.kwargs["connection_ids"], ["allowed"])
        self.vectors.query_vectors.return_value[0]["metadata"]["connection_id"] = "denied"
        with self.assertRaises(PermissionError):
            await self.adapter.retrieve(self.scope, operation)


if __name__ == "__main__":
    unittest.main()
