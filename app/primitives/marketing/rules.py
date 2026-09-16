"""Validated, versioned configuration shared by manual and language-based rules."""

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def normalize_keyword(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class KeywordFilter(StrictModel):
    mode: Literal["exact", "contains"] = "contains"
    include: list[Label] = Field(default_factory=list, max_length=100)
    exclude: list[Label] = Field(default_factory=list, max_length=100)

    @field_validator("include", "exclude")
    @classmethod
    def unique_terms(cls, terms):
        return list(dict.fromkeys(normalize_keyword(term) for term in terms))

    def matches(self, query: str) -> bool:
        query = normalize_keyword(query)
        def match(term):
            return query == term if self.mode == "exact" else term in query
        return (not self.include or any(map(match, self.include))) and not any(map(match, self.exclude))


class OpportunityRule(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["search_snippet_review"] = "search_snippet_review"
    name: Label
    property_id: Label
    dataset: Label
    enabled: bool = True
    min_impressions: int = Field(default=100, ge=1, le=1_000_000_000, strict=True)
    ctr_threshold: float = Field(default=0.02, gt=0, lt=1)
    position_min: float = Field(default=1, ge=1, le=1000)
    position_max: float = Field(default=10, ge=1, le=1000)
    window_days: int = Field(default=28, ge=1, le=366, strict=True)
    keywords: KeywordFilter = Field(default_factory=KeywordFilter)
    target_text: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def check_position(self):
        if self.position_min > self.position_max:
            raise ValueError("position_min must not exceed position_max")
        return self


class TargetRequest(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    property_id: Label
    dataset: Label

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Target cannot be blank")
        return value.strip()


class TargetInterpretation(StrictModel):
    rule: OpportunityRule | None
    questions: list[Label] = Field(default_factory=list, max_length=10)
    assumptions: list[Label] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent(self):
        if self.rule is None and not self.questions:
            raise ValueError("Unsupported or ambiguous targets require a clarification")
        if self.rule is not None and self.questions:
            raise ValueError("Unresolved questions cannot produce a ready rule")
        return self


def describe_rule(rule: OpportunityRule) -> str:
    includes = ", ".join(repr(term) for term in rule.keywords.include) or "all queries"
    excludes = ", ".join(repr(term) for term in rule.keywords.exclude) or "none"
    return (
        f"For each query/page segment over {rule.window_days} days: impressions >= "
        f"{rule.min_impressions}, CTR < {rule.ctr_threshold * 100:g}%, average position "
        f"between {rule.position_min:g} and {rule.position_max:g} inclusive. "
        f"Keyword matching: {rule.keywords.mode}, case-insensitive; include any of "
        f"{includes}; exclude any of {excludes}. Exclusions take precedence."
    )
