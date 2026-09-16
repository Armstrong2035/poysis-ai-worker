"""Internal service. Callers must authorize workspace access before invoking it.

Reports must use a fixed daily grain. Keep different report definitions under
different dataset names: metrics at different grains are not safely additive.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

from .rules import OpportunityRule


@dataclass(frozen=True)
class Observation:
    day: date
    dimensions: Mapping[str, str]
    metrics: Mapping[str, float]

    def __post_init__(self):
        if type(self.day) is not date:
            raise ValueError("day must be a date")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.dimensions.items()):
            raise ValueError("dimensions must contain strings")
        if not self.metrics or not all(
            isinstance(k, str) and type(v) in (int, float) and math.isfinite(v)
            for k, v in self.metrics.items()
        ):
            raise ValueError("metrics must contain finite numbers")


def parse_ga4(report):
    """Parse a runReport response whose dimensions include date (YYYYMMDD)."""
    dimensions = [h["name"] for h in report["dimensionHeaders"]]
    metrics = [h["name"] for h in report["metricHeaders"]]
    if "date" not in dimensions or len(set(dimensions)) != len(dimensions) or len(set(metrics)) != len(metrics):
        raise ValueError("GA4 requires unique headers and a date dimension")
    result = []
    for row in report.get("rows", []):
        if len(row["dimensionValues"]) != len(dimensions) or len(row["metricValues"]) != len(metrics):
            raise ValueError("GA4 row does not match report headers")
        dims = dict(zip(dimensions, (v["value"] for v in row["dimensionValues"])))
        day = datetime.strptime(dims.pop("date"), "%Y%m%d").date()
        result.append(Observation(day, dims, dict(zip(metrics, (float(v["value"]) for v in row["metricValues"])))))
    return result


def parse_search_console(report, dimensions):
    """Pass the exact ordered dimensions from the Search Analytics request."""
    if "date" not in dimensions or len(set(dimensions)) != len(dimensions):
        raise ValueError("Search Console requires unique dimensions including date")
    result = []
    for row in report.get("rows", []):
        if len(row["keys"]) != len(dimensions):
            raise ValueError("Search Console row does not match request dimensions")
        dims = dict(zip(dimensions, row["keys"]))
        day = date.fromisoformat(dims.pop("date"))
        metrics = {k: float(row[k]) for k in ("clicks", "impressions", "position")}
        if not all(math.isfinite(v) and v >= 0 for v in metrics.values()) or metrics["clicks"] > metrics["impressions"]:
            raise ValueError("Invalid Search Console metrics")
        # Recompute rates from counts; do not average reported daily CTRs.
        result.append(Observation(day, dims, metrics))
    return result


class MarketingService:
    def __init__(self, store):
        self.store = store

    async def ingest(self, workspace_id, source, property_id, dataset, observations):
        """Upsert complete observations; retries replace counts rather than add them.

        Generic structured sources use Observation directly. Source metadata must
        include property/account identity and a versioned report definition.
        """
        if not all(isinstance(v, str) and v.strip() for v in (workspace_id, source, property_id, dataset)):
            raise ValueError("workspace, source, property and dataset are required")
        rows = []
        seen = set()
        for item in observations:
            # Validate again in case a caller mutated the mappings.
            item = Observation(item.day, dict(item.dimensions), dict(item.metrics))
            if source == "search_console":
                required = ("clicks", "impressions", "position")
                if (not all(k in item.metrics and item.metrics[k] >= 0 for k in required)
                        or item.metrics["clicks"] > item.metrics["impressions"]):
                    raise ValueError("Invalid Search Console metrics")
            canonical = json.dumps(item.dimensions, sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256(canonical.encode()).hexdigest()
            identity = (item.day, key)
            if identity in seen:
                raise ValueError("Duplicate grain in batch; aggregate or separate datasets")
            seen.add(identity)
            rows.append((workspace_id, source, property_id, dataset, item.day, key,
                         canonical, json.dumps(item.metrics, allow_nan=False)))
        await self.store.upsert(rows)
        return {"ingested": len(rows)}

    async def opportunities(self, workspace_id, property_id, dataset, start, end,
                            min_impressions=100, ctr_threshold=0.02, *, rule=None):
        """Heuristic review candidates, not causal claims or revenue forecasts."""
        if start > end:
            raise ValueError("Invalid opportunity window or thresholds")
        rule = rule or OpportunityRule(name="Search snippet review", property_id=property_id,
                                       dataset=dataset, min_impressions=min_impressions,
                                       ctr_threshold=ctr_threshold)
        if rule.property_id != property_id or rule.dataset != dataset:
            raise ValueError("Rule scope does not match requested dataset")
        if not rule.enabled:
            return []
        rows = await self.store.fetch(workspace_id, "search_console", property_id, dataset, start, end)
        if len(rows) > 100000:
            raise ValueError("Evaluation exceeds 100000 rows; narrow the date window or dataset")
        grouped = {}
        for row in rows:
            dims, metrics = row["dimensions"], row["metrics"]
            if not dims.get("query") or not dims.get("page"):
                continue
            if not rule.keywords.matches(dims["query"]):
                continue
            # Preserve device/country and all other segments rather than blending them.
            key = json.dumps(dims, sort_keys=True)
            bucket = grouped.setdefault(key, {"dimensions": dims, "clicks": 0, "impressions": 0, "weighted_position": 0})
            impressions = metrics["impressions"]
            bucket["clicks"] += metrics["clicks"]
            bucket["impressions"] += impressions
            bucket["weighted_position"] += metrics["position"] * impressions
        result = []
        for bucket in grouped.values():
            impressions = bucket["impressions"]
            if impressions < rule.min_impressions:
                continue
            ctr = bucket["clicks"] / impressions
            position = bucket["weighted_position"] / impressions
            if ctr < rule.ctr_threshold and rule.position_min <= position <= rule.position_max:
                result.append({
                    "kind": "search_snippet_review", "dimensions": bucket["dimensions"],
                    "evidence": {"clicks": bucket["clicks"], "impressions": impressions,
                                 "ctr": ctr, "average_position": position},
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "source": "search_console", "property_id": property_id, "dataset": dataset,
                    "rule": rule.model_dump(mode="json"),
                    "recommendation": "Review search intent, title and description for this page and query.",
                    "confidence": "heuristic; compare against position, brand and device baselines",
                })
        return sorted(result, key=lambda item: item["evidence"]["impressions"], reverse=True)
