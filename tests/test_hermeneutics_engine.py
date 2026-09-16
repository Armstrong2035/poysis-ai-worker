import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from hermeneutics import HermeneuticsEngine, InterpretationRequest, Scope, SQLiteRepository
from hermeneutics.context import assemble, digest
from hermeneutics.models import DocumentChunk, Fact, Period, RetrievalOperation, RetrievalRecord
from hermeneutics.repository import ConflictError, LeaseLostError


SCOPE = Scope(client_id="client-a", workspace_id="workspace-a")


def request():
    return InterpretationRequest.model_validate({"question": "Why did signups fall?", "frame": {
        "objective": "Explain acquisition changes", "period": {"start": "2026-08-01", "end": "2026-08-28"},
        "datasets": [{"source": "ga4", "property_id": "123", "dataset": "signups-v1", "metrics": ["eventCount"]}]}})


class FixtureAdapter:
    def __init__(self):
        self.queries = []

    async def retrieve(self, scope, op):
        self.queries.append(op)
        if op.kind == "structured":
            value = 70 if op.period.start.month == 8 else 100
            return [Fact(id=f"fact-{op.period.start}", scope=scope, source="ga4", locator="report/signups",
                         captured_at=datetime(2026, 9, 1, tzinfo=timezone.utc), period=op.period,
                         metric="eventCount", value=value, unit="count", definition="Recorded signup events")]
        challenged = "tracking" in op.query.lower()
        return [DocumentChunk(id="tracking-doc" if challenged else "positioning-doc", scope=scope,
                              source="documents", locator="documents/tracking" if challenged else "documents/positioning",
                              captured_at=datetime.now(timezone.utc), title="Change log",
                              text="Signup tracking changed August 2." if challenged else "Positioning changed August 1.")]


def reading(thesis, evidence_id):
    return {"thesis": thesis, "claims": [{"statement": thesis,
            "evidence": [{"evidence_id": evidence_id, "relationship": "qualifies",
                          "rationale": "Recorded evidence bears on the explanation", "weight": 0.7}],
            "limitations": ["Timing alone does not establish causation"]}]}


class FixtureModel:
    model_version = "deterministic-test-model"

    def __init__(self, invented=False):
        self.calls = []
        self.invented = invented

    async def complete(self, prompt):
        data = json.loads(prompt.split("\nINPUT:\n", 1)[1])
        self.calls.append(data)
        packet = data["context"]
        if data["initial"] is None:
            return json.dumps({"primary": reading("Recorded signups fell", packet["facts"][0]["id"]),
                               "rivals": [{"explanation": "Tracking changed", "document_query": "signup tracking changes",
                                           "distinguishing_evidence": "Event instrumentation change logs"}], "missing_context": []})
        return json.dumps({"outcome": "explained",
                           "primary": reading("Tracking changes qualify the apparent decline", "invented" if self.invented else "tracking-doc"),
                           "alternatives": [reading("Positioning could also matter", "positioning-doc")],
                           "missing_context": ["Independent verification of signup counts"],
                           "confidence": {"level": "high", "rationale": "Multiple pieces of evidence"}})


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteRepository(Path(self.temp.name) / "engine.sqlite")
        self.adapter = FixtureAdapter()
        self.model = FixtureModel()
        self.engine = HermeneuticsEngine(self.repo, lambda scope: self.adapter, self.model)

    async def test_full_pipeline_challenge_and_persisted_lineage(self):
        job = await self.engine.submit(SCOPE, request(), "one")
        self.assertEqual(job["status"], "queued")
        result = await self.engine.run_next()
        self.assertEqual(result["status"], "completed", result)
        interpretation = result["interpretation"]
        self.assertEqual(len(self.model.calls), 2)
        self.assertNotIn("tracking-doc", [d["id"] for d in self.model.calls[0]["context"]["relevant_documents"]])
        self.assertIn("tracking-doc", [d["id"] for d in self.model.calls[1]["context"]["relevant_documents"]])
        self.assertEqual(interpretation["synthesis"]["confidence"]["level"], "moderate")
        self.assertEqual(interpretation["context"]["signals"][0]["measurements"]["percent_change"], -30)
        self.assertEqual(interpretation["context_hash"], digest(interpretation["context"]))
        evidence = await self.engine.evidence(SCOPE, job["id"])
        self.assertEqual({x["object_id"] for x in evidence["links"]}, {"fact-2026-08-01", "tracking-doc", "positioning-doc"})
        reopened = SQLiteRepository(self.repo.path)
        self.assertEqual(reopened.get(SCOPE, job["id"])["interpretation"], interpretation)
        self.assertIsNone(await self.engine.run_next())

    async def test_idempotency_and_client_workspace_isolation(self):
        original = await self.engine.submit(SCOPE, request(), "retry")
        retry = await self.engine.submit(SCOPE, request(), "retry")
        self.assertEqual(original["id"], retry["id"])
        changed = request()
        changed.question = "A different question"
        with self.assertRaises(ConflictError):
            await self.engine.submit(SCOPE, changed, "retry")
        for scope in (Scope(client_id="client-b", workspace_id=SCOPE.workspace_id),
                      Scope(client_id=SCOPE.client_id, workspace_id="workspace-b")):
            self.assertIsNone(await self.engine.get(scope, original["id"]))
            self.assertIsNone(await self.engine.evidence(scope, original["id"]))
            distinct = await self.engine.submit(scope, request(), "retry")
            self.assertNotEqual(original["id"], distinct["id"])

    async def test_fabricated_citation_fails_without_persisting_result(self):
        self.model.invented = True
        job = await self.engine.submit(SCOPE, request(), "bad-citation")
        result = await self.engine.run_next()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_model_output")
        self.assertEqual((await self.engine.evidence(SCOPE, job["id"]))["objects"], [])

    async def test_empty_evidence_completed_without_model(self):
        self.adapter.retrieve = AsyncMock(return_value=[])
        await self.engine.submit(SCOPE, request(), "empty")
        result = await self.engine.run_next()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["interpretation"]["synthesis"]["outcome"], "insufficient_evidence")
        self.assertEqual(self.model.calls, [])

    async def test_failed_sources_are_not_empty_evidence(self):
        self.adapter.retrieve = AsyncMock(side_effect=RuntimeError("private backend details"))
        await self.engine.submit(SCOPE, request(), "failed")
        result = await self.engine.run_next()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private", json.dumps(result))

    async def test_cross_scope_evidence_fails_closed(self):
        evil = DocumentChunk(id="evil", scope=Scope(client_id="other", workspace_id="other"),
                             source="documents", locator="x", captured_at=datetime.now(timezone.utc), title="x", text="x")
        self.adapter.retrieve = AsyncMock(return_value=[evil])
        await self.engine.submit(SCOPE, request(), "scope")
        result = await self.engine.run_next()
        self.assertEqual(result["error_code"], "evidence_access_denied")
        self.assertEqual(self.model.calls, [])

    async def test_invalid_json_and_timeout(self):
        self.model.complete = AsyncMock(return_value="not JSON")
        await self.engine.submit(SCOPE, request(), "json")
        self.assertEqual((await self.engine.run_next())["error_code"], "invalid_model_output")
        self.model.complete.side_effect = TimeoutError()
        await self.engine.submit(SCOPE, request(), "timeout")
        self.assertEqual((await self.engine.run_next())["error_code"], "model_timeout")

    async def test_lease_reclaim_and_stale_worker_cannot_commit(self):
        job = await self.engine.submit(SCOPE, request(), "lease")
        first = self.repo.claim(lease_seconds=-1)
        second = self.repo.claim()
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["lease_owner"], second["lease_owner"])
        with self.assertRaises(LeaseLostError):
            self.repo.finish(job["id"], first["lease_owner"], error_code="late")
        self.repo.finish(job["id"], second["lease_owner"], error_code="current")
        self.assertEqual(self.repo.get(SCOPE, job["id"])["error_code"], "current")

    async def test_retrieval_failure_disclosed_with_other_evidence(self):
        original = self.adapter.retrieve
        async def retrieve(scope, operation):
            if operation.kind == "structured" and operation.period.start.month == 7:
                raise RuntimeError("unavailable")
            return await original(scope, operation)
        self.adapter.retrieve = retrieve
        await self.engine.submit(SCOPE, request(), "partial")
        result = await self.engine.run_next()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any("failed" in gap for gap in result["interpretation"]["context"]["known_gaps"]))


class ContextTests(unittest.TestCase):
    def test_duplicate_document_retrieval_keeps_first_snapshot(self):
        doc = DocumentChunk(id="doc", scope=SCOPE, source="docs", locator="doc", title="Doc", text="A claim",
                            captured_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        repeated = doc.model_copy(update={"captured_at": datetime(2026, 8, 2, tzinfo=timezone.utc)})
        packet = assemble(SCOPE, request(), [doc, repeated], [])
        self.assertEqual(len(packet.relevant_documents), 1)
        with self.assertRaises(ValueError):
            assemble(SCOPE, request(), [doc, doc.model_copy(update={"text": "Different"})], [])

    def test_percentage_change_from_zero_is_unknown(self):
        facts = [Fact(id=str(i), scope=SCOPE, source="ga4", locator="same", captured_at=datetime.now(timezone.utc),
                      period=Period(start=start, end=end), metric="eventCount", value=value, unit="count", definition="sum")
                 for i, start, end, value in [(1, date(2026, 8, 1), date(2026, 8, 28), 4),
                                              (2, date(2026, 7, 4), date(2026, 7, 31), 0)]]
        packet = assemble(SCOPE, request(), facts, [])
        self.assertIsNone(packet.signals[0].measurements["percent_change"])


if __name__ == "__main__":
    unittest.main()
