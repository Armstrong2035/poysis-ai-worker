"""Frame -> retrieve -> context -> interpret -> challenge -> synthesize -> persist."""

import asyncio
import contextlib
from datetime import datetime, timezone

from .context import assemble, digest
from .model import PROMPT_VERSION, prompt_for
from .models import (Confidence, InitialInterpretation, Interpretation, InterpretationRequest,
                     PriorInterpretation, RetrievalOperation, RetrievalRecord, Scope, Synthesis)
from .planner import plan
from .repository import LeaseLostError


class InvalidInterpretation(ValueError):
    pass


def validate_readings(readings, packet):
    evidence = {item.id: item for item in packet.evidence()}
    for reading in readings:
        if reading is None:
            continue
        for claim in reading.claims:
            for link in claim.evidence:
                if link.evidence_id not in evidence:
                    raise InvalidInterpretation("Model cited evidence outside its context")
            if all(evidence[link.evidence_id].kind == "previous_interpretation" for link in claim.evidence):
                raise InvalidInterpretation("A prior interpretation cannot be the only evidence for a claim")


class HermeneuticsEngine:
    def __init__(self, repository, adapter_factory, model, *, model_timeout=90, retrieval_timeout=30):
        self.repository = repository
        # Trusted host resolves scope to an adapter with dataset/document grants.
        self.adapter_factory = adapter_factory
        self.model = model
        self.model_timeout = model_timeout
        self.retrieval_timeout = retrieval_timeout

    async def submit(self, scope: Scope, request: InterpretationRequest, idempotency_key: str):
        # Revalidate mutable caller objects and the plan before persisting work.
        scope = Scope.model_validate(scope.model_dump())
        request = InterpretationRequest.model_validate(request.model_dump())
        plan(request)
        return await asyncio.to_thread(self.repository.submit, scope, request, idempotency_key)

    async def get(self, scope, interpretation_id):
        return await asyncio.to_thread(self.repository.get, scope, interpretation_id)

    async def evidence(self, scope, interpretation_id):
        return await asyncio.to_thread(self.repository.evidence, scope, interpretation_id)

    async def _retrieve(self, scope, operation, adapter):
        try:
            if operation.kind == "previous":
                previous = await asyncio.to_thread(self.repository.previous, scope)
                items = []
                for identity, result in previous:
                    reading = result["synthesis"]["primary"]
                    if reading:
                        items.append(PriorInterpretation(
                            id=digest([scope.model_dump(), "interpretation", identity]), scope=scope,
                            source="hermeneutics", locator=f"interpretations/{identity}",
                            captured_at=datetime.fromisoformat(result["created_at"]),
                            thesis=reading["thesis"], limitations=["Prior hypothesis, not independent evidence.",
                                "Selected by recency; may concern a different frame or postdate the analysis period."]))
            else:
                items = await asyncio.wait_for(adapter.retrieve(scope, operation), self.retrieval_timeout)
        except PermissionError:
            raise
        except Exception:
            return [], RetrievalRecord(operation=operation, status="failed", coverage="Retrieval unavailable; not evidence of absence")
        if len(items) > 40:
            raise ValueError("Adapter exceeded per-operation evidence limit")
        for item in items:
            if item.scope != scope:
                raise PermissionError("Adapter returned evidence outside authorized scope")
        return items, RetrievalRecord(operation=operation, status="ok" if items else "empty",
                                      evidence_ids=[item.id for item in items])

    async def _call(self, stage, contract, packet, initial=None):
        raw = await asyncio.wait_for(self.model.complete(prompt_for(stage, contract, packet, initial)), self.model_timeout)
        if not isinstance(raw, str) or len(raw) > 60000:
            raise InvalidInterpretation("Invalid model output size")
        try:
            return contract.model_validate_json(raw)
        except ValueError as exc:
            raise InvalidInterpretation("Model output failed schema validation") from exc

    async def _interpret(self, job):
        scope = Scope(client_id=job["client_id"], workspace_id=job["workspace_id"])
        request = InterpretationRequest.model_validate_json(job["request_json"])
        adapter = self.adapter_factory(scope)
        evidence, records = [], []
        # Bounded concurrent retrieval, no speculative free-form SQL.
        fetched = await asyncio.gather(*(self._retrieve(scope, op, adapter) for op in plan(request)))
        for items, record in fetched:
            evidence.extend(items)
            records.append(record)
        packet = assemble(scope, request, evidence, records)
        initial = None
        if not (packet.facts or packet.signals or packet.relevant_documents):
            # Empty successful retrieval is different from all sources failing.
            if records and all(r.status == "failed" for r in records if r.operation.kind != "previous") and any(r.operation.kind != "previous" for r in records):
                raise RuntimeError("All evidence sources unavailable")
            synthesis = Synthesis(outcome="insufficient_evidence", primary=None,
                                  missing_context=packet.known_gaps + ["No primary evidence was retrieved."],
                                  confidence=Confidence(level="low", rationale="There is no primary evidence to interpret."))
        else:
            initial = await self._call("interpret", InitialInterpretation, packet)
            validate_readings([initial.primary], packet)
            if request.frame.include_documents:
                queries = list(dict.fromkeys(r.document_query for r in initial.rivals))
                challenged = await asyncio.gather(*(self._retrieve(scope, RetrievalOperation(
                    kind="semantic", query=query, purpose="Seek evidence that distinguishes a rival explanation"), adapter)
                    for query in queries))
                for items, record in challenged:
                    evidence.extend(items)
                    records.append(record)
            packet = assemble(scope, request, evidence, records)
            # The expanded packet must retain any initial citations for lineage.
            validate_readings([initial.primary], packet)
            synthesis = await self._call("challenge", Synthesis, packet, initial)
            validate_readings([synthesis.primary, *synthesis.alternatives], packet)
            synthesis.missing_context = list(dict.fromkeys([*synthesis.missing_context, *packet.known_gaps]))[:30]
            if synthesis.outcome == "insufficient_evidence":
                synthesis.confidence = Confidence(level="low", rationale="Available evidence does not resolve an explanation.")
            elif synthesis.confidence.level == "high" and packet.known_gaps:
                synthesis.confidence = Confidence(level="moderate", rationale="Confidence capped because source coverage or context is incomplete.")
        created_at = datetime.now(timezone.utc)
        return Interpretation(id=job["id"], scope=scope, question=request.question, frame=request.frame,
                              created_at=created_at, valid_from=created_at.date(),
                              initial=initial, synthesis=synthesis, context=packet,
                              model_version=self.model.model_version, prompt_version=PROMPT_VERSION,
                              context_hash=digest(packet.model_dump(mode="json")))

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(20)
            await asyncio.to_thread(self.repository.heartbeat, job["id"], job["lease_owner"])

    async def run_next(self):
        """One durable job. A host worker can call repeatedly; no HTTP dependency."""
        job = await asyncio.to_thread(self.repository.claim)
        if job is None:
            return None
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            result = await self._interpret(job)
            await asyncio.to_thread(self.repository.finish, job["id"], job["lease_owner"], result)
        except LeaseLostError:
            raise
        except Exception as exc:
            code = ("evidence_access_denied" if isinstance(exc, PermissionError) else
                    "invalid_model_output" if isinstance(exc, InvalidInterpretation) else
                    "model_timeout" if isinstance(exc, TimeoutError) else "interpretation_failed")
            await asyncio.to_thread(self.repository.finish, job["id"], job["lease_owner"], error_code=code)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, LeaseLostError):
                await heartbeat
        scope = Scope(client_id=job["client_id"], workspace_id=job["workspace_id"])
        return await self.get(scope, job["id"])
