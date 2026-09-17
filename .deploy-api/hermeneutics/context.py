"""Budgeted evidence assembly and deterministic period-change signals."""

import hashlib
import json
from datetime import datetime, timezone

from .models import ContextPacket, Signal


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def assemble(scope, request, evidence, records, max_chars=60000):
    packet = ContextPacket(question=request.question, scope=scope, frame=request.frame,
                           source_coverage=records, temporal_context={
                               "analysis_start": str(request.frame.period.start),
                               "analysis_end": str(request.frame.period.end),
                               "assembled_at": datetime.now(timezone.utc).isoformat()},
                           known_gaps=["Source completeness is unverified; absence of a row does not establish zero.",
                                       "Graph traversal and authoritative decision records are not connected."])
    if not request.frame.datasets:
        packet.known_gaps.append("No structured datasets were selected in the frame.")
    if request.frame.datasets and request.frame.comparison is None:
        packet.known_gaps.append("Comparison assumes the immediately preceding equal-length period; seasonality is not controlled.")
    if request.frame.user_assertions:
        packet.known_gaps.append("User assertions in the frame are unverified claims, not observed facts.")
    for record in records:
        if record.status != "ok":
            packet.known_gaps.append(f"{record.operation.kind}: {record.operation.purpose} returned {record.status}.")
    buckets = {"fact": packet.facts, "signal": packet.signals, "document": packet.relevant_documents,
               "previous_interpretation": packet.previous_interpretations}
    seen = {}
    for item in evidence:
        if item.scope != scope:
            raise ValueError("Evidence scope mismatch")
        if item.id in seen:
            if item.model_dump(exclude={"captured_at"}) != seen[item.id].model_dump(exclude={"captured_at"}):
                raise ValueError("Conflicting evidence versions share an ID")
            continue
        seen[item.id] = item
        buckets[item.kind].append(item)
        if len(packet.model_dump_json()) > max_chars:
            buckets[item.kind].pop()
            packet.known_gaps.append(f"Evidence {item.id} omitted due to context budget.")
    # Signals derive only from facts actually retained in the packet.
    current = [f for f in packet.facts if f.period == request.frame.period]
    for fact in current:
        previous = next((f for f in packet.facts if f.metric == fact.metric and f.source == fact.source
                         and f.locator == fact.locator and f.dimensions == fact.dimensions
                         and f.period.end < fact.period.start), None)
        if previous is None:
            continue
        delta = fact.value - previous.value
        percent = delta / previous.value * 100 if previous.value else None
        signal = Signal(id=digest(["change", fact.id, previous.id]), scope=scope,
                        source=fact.source, locator=fact.locator, captured_at=fact.captured_at,
                        statement=f"{fact.metric}: current {fact.value:g}, comparison {previous.value:g}.",
                        measurements={"current": fact.value, "previous": previous.value,
                                      "absolute_change": delta, "percent_change": percent},
                        derived_from=[fact.id, previous.id],
                        limitations=["Period comparison is descriptive, not proof of causation."])
        if signal.id not in seen:
            packet.signals.append(signal)
            seen[signal.id] = signal
    # Keep derived lineage intact and disclose all omissions. If even the bounded
    # frame/coverage exceeds budget, fail instead of silently losing provenance.
    if len(packet.model_dump_json()) > max_chars:
        raise ValueError("Context exceeds budget; narrow frame or retrieval scope")
    retained = {e.id for e in packet.evidence()}
    for signal in packet.signals:
        if not set(signal.derived_from) <= retained:
            raise ValueError("Signal references missing source facts")
    return packet
