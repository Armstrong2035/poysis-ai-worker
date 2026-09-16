import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import marketing
from app.api.security import get_user_id
from app.primitives.marketing.keyword_planner import (
    PlannerImport, filter_planner_keywords, parse_planner_report, planner_snapshot,
)
from app.primitives.marketing.rules import KeywordFilter


def report():
    return {"results": [{"text": "invoice automation", "closeVariants": ["automate invoices"],
                         "keywordMetrics": {"avgMonthlySearches": "1000", "competition": "LOW",
                         "competitionIndex": "20", "lowTopOfPageBidMicros": "1200000",
                         "highTopOfPageBidMicros": "3000000",
                         "monthlySearchVolumes": [{"year": "2026", "month": "JULY", "monthlySearches": "900"}]}}]}


def request():
    return PlannerImport(snapshot_id=uuid4(), captured_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                         report_kind="historical_metrics", report=report(), context={
                             "customer_id": "1234567890", "dataset": "invoice-research-v1",
                             "language": "languageConstants/1000", "geo_target_constants": [],
                             "network": "GOOGLE_SEARCH", "currency_code": "USD"})


class PlannerTests(unittest.TestCase):
    def test_metrics_variants_and_micros(self):
        keyword = parse_planner_report(report(), "historical_metrics")[0]
        self.assertEqual(keyword.avg_monthly_searches, 1000)
        self.assertEqual(keyword.close_variants, ["automate invoices"])
        self.assertEqual(keyword.low_top_of_page_bid_micros, 1200000)
        self.assertEqual(keyword.monthly_search_volumes[0].month, 7)
        self.assertEqual(keyword.monthly_search_volumes[0].searches, 900)
        self.assertIsNone(keyword.average_cpc_micros)

    def test_ideas_and_unknown_not_zero(self):
        data = {"results": [{"text": "invoice", "keywordIdeaMetrics": {"avgMonthlySearches": "0"}},
                            {"text": "unknown"}]}
        rows = [k.model_dump() for k in parse_planner_report(data, "ideas")]
        self.assertEqual(rows[0]["avg_monthly_searches"], 0)
        self.assertIsNone(rows[1]["avg_monthly_searches"])
        self.assertEqual(len(filter_planner_keywords(rows, KeywordFilter())), 2)
        self.assertEqual(len(filter_planner_keywords(rows, KeywordFilter(), min_searches=0)), 1)
        self.assertEqual(filter_planner_keywords(rows, KeywordFilter(), max_competition=100), [])

    def test_reject_partial_wrong_kind_duplicates_invalid_values(self):
        values = []
        partial = report()
        partial["nextPageToken"] = "next-page"
        values.append(partial)
        duplicate = report()
        duplicate["results"] *= 2
        values.append(duplicate)
        for field, value in [("competitionIndex", 101), ("avgMonthlySearches", -1),
                             ("avgMonthlySearches", True), ("avgMonthlySearches", 1.5),
                             ("avgMonthlySearches", "1.2"), ("highTopOfPageBidMicros", "0")]:
            invalid = report()
            invalid["results"][0]["keywordMetrics"][field] = value
            values.append(invalid)
        for data in values:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_planner_report(data, "historical_metrics")
        with self.assertRaises(ValueError):
            parse_planner_report(report(), "ideas")

    def test_variant_exclusions_and_threshold_boundaries(self):
        rows = [k.model_dump() for k in parse_planner_report(report(), "historical_metrics")]
        self.assertEqual(len(filter_planner_keywords(rows, KeywordFilter(include=["AUTOMATE INVOICES"], mode="exact"), 1000, 20)), 1)
        self.assertEqual(filter_planner_keywords(rows, KeywordFilter(include=["invoice"], exclude=["automate"])), [])
        self.assertEqual(filter_planner_keywords(rows, KeywordFilter(), 1001), [])
        self.assertEqual(filter_planner_keywords(rows, KeywordFilter(), max_competition=19), [])

    def test_snapshot_fingerprint_and_context(self):
        first = request()
        content, fingerprint = planner_snapshot(first)
        self.assertEqual(planner_snapshot(first)[1], fingerprint)
        self.assertEqual(content["metric_semantics"], "estimated_search_demand_and_paid_ad_competition")
        first.context.currency_code = "NGN"
        self.assertNotEqual(planner_snapshot(first)[1], fingerprint)
        payload = first.model_dump()
        payload["captured_at"] = datetime(2026, 9, 1)
        with self.assertRaises(ValidationError):
            PlannerImport.model_validate(payload)


class PlannerAPITests(unittest.TestCase):
    def setUp(self):
        self.store = AsyncMock()
        self.app = FastAPI()
        self.app.include_router(marketing.router)
        self.app.dependency_overrides[get_user_id] = lambda: "owner"
        self.app.dependency_overrides[marketing.get_store] = lambda: self.store
        for method, result in [("get_workspace", {"user_id": "owner"}), ("has_workspace_access", True)]:
            patcher = patch(f"app.primitives.database.DatabaseService.{method}", new_callable=AsyncMock)
            mock = patcher.start()
            mock.return_value = result
            self.addCleanup(patcher.stop)
            if method == "has_workspace_access":
                self.membership = mock
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.request = request()

    def test_import_read_filters_and_conflict(self):
        self.store.save_planner_report.return_value = True
        response = self.client.post("/marketing/keyword-planner/reports?workspace_id=workspace", json=self.request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["keyword_count"], 1)
        self.assertEqual(self.store.save_planner_report.call_args.args[0], "workspace")
        self.store.get_planner_report.return_value = planner_snapshot(self.request)[0]
        url = f"/marketing/keyword-planner/reports/{self.request.snapshot_id}"
        response = self.client.get(url, params={"workspace_id": "workspace", "include": "invoice", "min_monthly_searches": 1000})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["context"]["currency_code"], "USD")
        self.store.get_planner_report.assert_awaited_with("workspace", self.request.snapshot_id)
        self.store.save_planner_report.return_value = False
        self.assertEqual(self.client.post("/marketing/keyword-planner/reports?workspace_id=workspace", json=self.request.model_dump(mode="json")).status_code, 409)

    def test_nonmember_and_member_edit_restrictions(self):
        self.app.dependency_overrides[get_user_id] = lambda: "member"
        self.assertEqual(self.client.post("/marketing/keyword-planner/reports?workspace_id=workspace", json=self.request.model_dump(mode="json")).status_code, 403)
        self.store.save_planner_report.assert_not_called()
        self.membership.return_value = False
        response = self.client.get(f"/marketing/keyword-planner/reports/{self.request.snapshot_id}?workspace_id=workspace")
        self.assertEqual(response.status_code, 403)
        self.store.get_planner_report.assert_not_called()

    def test_missing_and_malformed_reports(self):
        self.store.get_planner_report.return_value = None
        self.assertEqual(self.client.get(f"/marketing/keyword-planner/reports/{self.request.snapshot_id}?workspace_id=workspace").status_code, 404)
        payload = self.request.model_dump(mode="json")
        payload["report"]["results"][0]["keywordMetrics"]["avgMonthlySearches"] = "bad"
        self.assertEqual(self.client.post("/marketing/keyword-planner/reports?workspace_id=workspace", json=payload).status_code, 422)
        self.store.save_planner_report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
