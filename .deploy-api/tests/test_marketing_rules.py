import json
import unittest
from datetime import date
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.primitives.marketing.rules import KeywordFilter, OpportunityRule, TargetRequest
from app.primitives.marketing.service import MarketingService
from app.primitives.marketing.targets import preview_target


def rule(**kwargs):
    return OpportunityRule(name="Invoice opportunities", property_id="site", dataset="daily-v1", **kwargs)


class RuleTests(unittest.IsolatedAsyncioTestCase):
    def test_keyword_modes_exclusions_and_unicode(self):
        keywords = KeywordFilter(include=[" Invoice ", "INVOICE"], exclude=["Acme"])
        self.assertEqual(keywords.include, ["invoice"])
        self.assertTrue(keywords.matches("INVOICE reminders"))
        self.assertFalse(keywords.matches("Acme invoice"))
        self.assertFalse(keywords.matches("payments"))
        self.assertTrue(KeywordFilter(mode="exact", include=["invoice"]).matches("ＩＮＶＯＩＣＥ"))
        self.assertFalse(KeywordFilter(mode="exact", include=["invoice"]).matches("invoice reminders"))
        self.assertTrue(KeywordFilter(exclude=["acme"]).matches("invoice"))
        self.assertFalse(KeywordFilter(include=["invoice"], exclude=["invoice"]).matches("invoice"))

    def test_validation(self):
        for values in ({"position_min": 11, "position_max": 10}, {"ctr_threshold": 3},
                       {"window_days": 0}, {"window_days": 367}, {"ctr_threshold": float("nan")},
                       {"min_impressions": True}, {"unknown": 1},
                       {"keywords": {"mode": "semantic"}}, {"keywords": {"include": [" "]}}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                rule(**values)

    async def test_rule_controls_detection_and_disabled_skips_fetch(self):
        rows = [{"dimensions": {"query": query, "page": "/invoice"},
                 "metrics": {"clicks": 2, "impressions": 500, "position": position}}
                for query, position in [("invoice reminders", 12), ("Acme invoice", 12), ("payments", 12)]]
        store = AsyncMock()
        store.fetch.return_value = rows
        config = rule(position_max=15, keywords={"include": ["invoice"], "exclude": ["acme"]})
        results = await MarketingService(store).opportunities(
            "workspace", "site", "daily-v1", date(2026, 9, 1), date(2026, 9, 28), rule=config)
        self.assertEqual([r["dimensions"]["query"] for r in results], ["invoice reminders"])
        self.assertEqual(results[0]["rule"]["position_max"], 15)
        store.fetch.reset_mock()
        config.enabled = False
        self.assertEqual(await MarketingService(store).opportunities(
            "workspace", "site", "daily-v1", date(2026, 9, 1), date(2026, 9, 28), rule=config), [])
        store.fetch.assert_not_called()

    async def test_boundaries_and_segments(self):
        store = AsyncMock()
        store.fetch.return_value = [
            {"dimensions": {"query": "invoice", "page": "/", "device": device},
             "metrics": {"clicks": clicks, "impressions": 100, "position": position}}
            for device, clicks, position in [("MOBILE", 1, 10), ("DESKTOP", 2, 1), ("TABLET", 0, 11)]
        ]
        found = await MarketingService(store).opportunities(
            "workspace", "site", "daily-v1", date(2026, 9, 1), date(2026, 9, 28), rule=rule())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["dimensions"]["device"], "MOBILE")

    async def test_language_preview_does_not_trust_model_scope(self):
        generated = rule(min_impressions=500, ctr_threshold=0.03,
                         keywords={"include": ["invoice"]}).model_dump()
        generated["property_id"] = "other-tenant-property"
        complete = AsyncMock(return_value=json.dumps({"rule": generated, "questions": [], "assumptions": []}))
        request = TargetRequest(text="Find invoice queries with 500 impressions and CTR below 3%",
                                property_id="site", dataset="daily-v1")
        result = await preview_target(request, complete)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["saved"])
        self.assertEqual(result["rule"]["property_id"], "site")
        self.assertEqual(result["rule"]["target_text"], request.text)
        self.assertIn("CTR < 3%", result["summary"])

    async def test_language_clarification_and_invalid_outputs(self):
        request = TargetRequest(text="Grow revenue by 20%", property_id="site", dataset="daily-v1")
        result = await preview_target(request, AsyncMock(return_value=json.dumps({
            "rule": None, "questions": ["Revenue targets are unsupported. Would you like a Search Console rule?"],
            "assumptions": []})))
        self.assertEqual(result["status"], "needs_clarification")
        self.assertIsNone(result["rule"])
        for output in ("not json", '{"rule":null,"questions":[]}',
                       json.dumps({"rule": rule().model_dump(), "questions": ["Which keywords?"]}),
                       json.dumps({"rule": rule().model_dump(), "sql": "DROP TABLE anything"})):
            with self.subTest(output=output), self.assertRaises(ValueError):
                await preview_target(request, AsyncMock(return_value=output))


if __name__ == "__main__":
    unittest.main()
