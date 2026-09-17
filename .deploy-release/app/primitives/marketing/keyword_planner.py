"""Google Ads Keyword Planner report normalization, independent of credentials.

Research estimates are snapshots, not daily measurements of a website. Close
variants share metrics and must never be expanded into separately additive rows.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

from .rules import KeywordFilter, Label, StrictModel, normalize_keyword


def proto_integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("Expected an integer or protobuf integer string")


Count = Annotated[int, BeforeValidator(proto_integer), Field(ge=0, le=9223372036854775807)]
MONTHS = {name: index for index, name in enumerate(
    ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
     "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"), 1)}


class PlannerContext(StrictModel):
    customer_id: str = Field(pattern=r"^[0-9]{10}$")
    dataset: Label
    language: str = Field(pattern=r"^languageConstants/[0-9]+$")
    geo_target_constants: list[str] = Field(max_length=10)
    network: Literal["GOOGLE_SEARCH", "GOOGLE_SEARCH_AND_PARTNERS"]
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("geo_target_constants")
    @classmethod
    def validate_geographies(cls, values):
        if any(not re.fullmatch(r"geoTargetConstants/[0-9]+", v) for v in values):
            raise ValueError("Expected geoTargetConstants resource names")
        return sorted(set(values))


class PlannerImport(StrictModel):
    snapshot_id: UUID
    context: PlannerContext
    captured_at: datetime
    report_kind: Literal["ideas", "historical_metrics"]
    report: dict

    @field_validator("captured_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value

    @field_validator("report")
    @classmethod
    def bounded_report(cls, value):
        if len(json.dumps(value, allow_nan=False)) > 8_000_000:
            raise ValueError("Report exceeds 8 MB")
        return value


class MonthlyVolume(BaseModel):
    year: int = Field(ge=1900, le=9999)
    month: int = Field(ge=1, le=12)
    searches: Count | None = None


class PlannerKeyword(StrictModel):
    keyword: Label
    close_variants: list[Label] = Field(default_factory=list, max_length=10000)
    avg_monthly_searches: Count | None = None
    competition: Literal["UNSPECIFIED", "UNKNOWN", "LOW", "MEDIUM", "HIGH"] = "UNSPECIFIED"
    competition_index: Count | None = Field(default=None, le=100)
    low_top_of_page_bid_micros: Count | None = None
    high_top_of_page_bid_micros: Count | None = None
    average_cpc_micros: Count | None = None
    monthly_search_volumes: list[MonthlyVolume] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def consistent_metrics(self):
        if (self.low_top_of_page_bid_micros is not None and self.high_top_of_page_bid_micros is not None
                and self.low_top_of_page_bid_micros > self.high_top_of_page_bid_micros):
            raise ValueError("Low bid must not exceed high bid")
        periods = [(v.year, v.month) for v in self.monthly_search_volumes]
        if len(set(periods)) != len(periods):
            raise ValueError("Duplicate monthly volume period")
        return self


def parse_planner_report(report: dict, report_kind: str) -> list[PlannerKeyword]:
    if report_kind not in ("ideas", "historical_metrics"):
        raise ValueError("Unsupported Keyword Planner report kind")
    # Only complete reports are accepted, so listing a snapshot never implies
    # coverage over an unimported next page. Assemble idea pages before import.
    if report.get("nextPageToken"):
        raise ValueError("Report has more pages; assemble all results before importing")
    rows = report.get("results", [])
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Report must contain at most 10000 results")
    field = "keywordIdeaMetrics" if report_kind == "ideas" else "keywordMetrics"
    wrong_field = "keywordMetrics" if report_kind == "ideas" else "keywordIdeaMetrics"
    keywords = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or wrong_field in row:
            raise ValueError("Result does not match report kind")
        metrics = row.get(field)
        if metrics is None:
            metrics = {}
        if not isinstance(metrics, dict):
            raise ValueError("Expected a metrics object")
        volumes = []
        for volume in metrics.get("monthlySearchVolumes", []):
            if volume.get("month") not in MONTHS:
                raise ValueError("Expected a named Google Ads month")
            volumes.append(MonthlyVolume(year=proto_integer(volume["year"]),
                                         month=MONTHS[volume["month"]], searches=volume.get("monthlySearches")))
        keyword = PlannerKeyword(
            keyword=row["text"], close_variants=row.get("closeVariants", []),
            avg_monthly_searches=metrics.get("avgMonthlySearches"),
            competition=metrics.get("competition", "UNSPECIFIED"),
            competition_index=metrics.get("competitionIndex"),
            low_top_of_page_bid_micros=metrics.get("lowTopOfPageBidMicros"),
            high_top_of_page_bid_micros=metrics.get("highTopOfPageBidMicros"),
            average_cpc_micros=metrics.get("averageCpcMicros"),
            monthly_search_volumes=sorted(volumes, key=lambda v: (v.year, v.month)),
        )
        identity = normalize_keyword(keyword.keyword)
        if identity in seen:
            raise ValueError("Duplicate canonical keyword in report")
        seen.add(identity)
        keywords.append(keyword)
    return keywords


def planner_snapshot(request: PlannerImport):
    keywords = parse_planner_report(request.report, request.report_kind)
    content = {
        "source": "google_ads_keyword_planner", "origin": "imported_report",
        "context": request.context.model_dump(), "captured_at": request.captured_at.isoformat(),
        "report_kind": request.report_kind, "keywords": [k.model_dump() for k in keywords],
        "metric_semantics": "estimated_search_demand_and_paid_ad_competition",
        "coverage": "importer_supplied; no provider verification",
    }
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return content, fingerprint


def filter_planner_keywords(rows, keywords: KeywordFilter, min_searches=None, max_competition=None):
    results = []
    for row in rows:
        texts = [row["keyword"], *row["close_variants"]]
        include = KeywordFilter(mode=keywords.mode, include=keywords.include)
        exclude = KeywordFilter(mode=keywords.mode, include=keywords.exclude)
        if not any(include.matches(text) for text in texts):
            continue
        if keywords.exclude and any(exclude.matches(text) for text in texts):
            continue
        volume, competition = row["avg_monthly_searches"], row["competition_index"]
        if min_searches is not None and (volume is None or volume < min_searches):
            continue
        if max_competition is not None and (competition is None or competition > max_competition):
            continue
        results.append(row)
    # Unknown search volume stays distinct from measured zero and sorts last.
    return sorted(results, key=lambda r: (-(r["avg_monthly_searches"] if r["avg_monthly_searches"] is not None else -1),
                                           normalize_keyword(r["keyword"])))
