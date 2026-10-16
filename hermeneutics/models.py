"""Version-one contracts. Trusted scope is never supplied by a language model."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scope(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_id: Key
    workspace_id: Key


class Period(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end or (self.end - self.start).days >= 366:
            raise ValueError("Period must be ordered and at most 366 days")
        return self


class Dataset(Contract):
    source: Literal["ga4", "search_console"]
    property_id: Key
    dataset: Key
    metrics: list[Key] = Field(min_length=1, max_length=8)
    dimensions: dict[Key, Key] = Field(default_factory=dict, max_length=20)


class Frame(Contract):
    domain: Literal["marketing"] = "marketing"
    objective: Text
    period: Period
    comparison: Period | None = None
    datasets: list[Dataset] = Field(default_factory=list, max_length=4)
    definitions: dict[Key, Text] = Field(default_factory=dict, max_length=20)
    assumptions: list[Text] = Field(default_factory=list, max_length=20)
    user_assertions: list[Text] = Field(default_factory=list, max_length=20)
    include_documents: bool = True
    include_previous_interpretations: bool = True

    @model_validator(mode="after")
    def comparable(self):
        if self.comparison:
            if self.comparison.end >= self.period.start:
                raise ValueError("Comparison must precede the analysis period")
            if self.comparison.end - self.comparison.start != self.period.end - self.period.start:
                raise ValueError("Comparison must have the same duration")
        return self


class InterpretationRequest(Contract):
    question: Text
    frame: Frame


class Evidence(Contract):
    id: Key
    scope: Scope
    kind: Literal["fact", "signal", "document", "previous_interpretation", "decision"]
    source: Key
    locator: Text
    captured_at: datetime
    limitations: list[Text] = Field(default_factory=list, max_length=30)


class Fact(Evidence):
    kind: Literal["fact"] = "fact"
    period: Period
    metric: Key
    value: float
    unit: Key
    dimensions: dict[str, str] = Field(default_factory=dict)
    definition: Text


class Signal(Evidence):
    kind: Literal["signal"] = "signal"
    statement: Text
    measurements: dict[str, float | None]
    derived_from: list[Key] = Field(min_length=1, max_length=20)


class DocumentChunk(Evidence):
    kind: Literal["document"] = "document"
    title: Key
    text: Text
    source_modified_at: str | None = None


class PriorInterpretation(Evidence):
    kind: Literal["previous_interpretation"] = "previous_interpretation"
    thesis: Text


EvidenceObject = Annotated[Fact | Signal | DocumentChunk | PriorInterpretation, Field(discriminator="kind")]


class RetrievalOperation(Contract):
    kind: Literal["structured", "semantic", "previous"]
    purpose: Text
    query: Text | None = None
    dataset: Dataset | None = None
    period: Period | None = None


class RetrievalRecord(Contract):
    operation: RetrievalOperation
    status: Literal["ok", "empty", "failed"]
    evidence_ids: list[Key] = Field(default_factory=list)
    coverage: Text = "Source coverage is unverified"


class ContextPacket(Contract):
    schema_version: Literal[1] = 1
    question: Text
    scope: Scope
    frame: Frame
    facts: list[Fact] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    relevant_documents: list[DocumentChunk] = Field(default_factory=list)
    previous_interpretations: list[PriorInterpretation] = Field(default_factory=list)
    known_decisions: list[Text] = Field(default_factory=list)
    temporal_context: dict[str, str] = Field(default_factory=dict)
    source_coverage: list[RetrievalRecord] = Field(default_factory=list)
    known_gaps: list[Text] = Field(default_factory=list)

    def evidence(self):
        return [*self.facts, *self.signals, *self.relevant_documents, *self.previous_interpretations]


class EvidenceLink(Contract):
    evidence_id: Key
    relationship: Literal["supports", "contradicts", "qualifies", "explains", "correlates_with", "precedes", "depends_on"]
    rationale: Text
    weight: float = Field(ge=0, le=1)


class Claim(Contract):
    statement: Text
    evidence: list[EvidenceLink] = Field(min_length=1, max_length=15)
    limitations: list[Text] = Field(default_factory=list, max_length=10)


class Reading(Contract):
    thesis: Text
    claims: list[Claim] = Field(min_length=1, max_length=8)


class Rival(Contract):
    explanation: Text
    document_query: Text
    distinguishing_evidence: Text


class InitialInterpretation(Contract):
    primary: Reading | None
    rivals: list[Rival] = Field(min_length=1, max_length=3)
    missing_context: list[Text] = Field(default_factory=list, max_length=15)


class Confidence(Contract):
    level: Literal["low", "moderate", "high"]
    rationale: Text


class Synthesis(Contract):
    outcome: Literal["explained", "insufficient_evidence"]
    primary: Reading | None
    alternatives: list[Reading] = Field(default_factory=list, max_length=3)
    missing_context: list[Text] = Field(default_factory=list, max_length=30)
    confidence: Confidence

    @model_validator(mode="after")
    def consistent(self):
        if self.outcome == "explained" and self.primary is None:
            raise ValueError("An explanation requires a supported primary reading")
        if self.outcome == "insufficient_evidence" and (self.primary is not None or not self.missing_context):
            raise ValueError("Insufficient evidence requires gaps and no primary conclusion")
        return self


class Interpretation(Contract):
    id: Key
    scope: Scope
    question: Text
    frame: Frame
    created_at: datetime
    valid_from: date
    valid_until: date | None = None
    initial: InitialInterpretation | None
    synthesis: Synthesis
    context: ContextPacket
    model_version: Key
    prompt_version: Key
    context_hash: Key
