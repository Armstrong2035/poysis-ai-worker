import json
import unittest
from datetime import date

from app.primitives.marketing import MarketingService, Observation, parse_ga4, parse_search_console


class MemoryStore:
    def __init__(self):
        self.rows = {}

    async def upsert(self, rows):
        for row in rows:
            self.rows[row[:6]] = {"day": row[4], "dimensions": json.loads(row[6]), "metrics": json.loads(row[7])}

    async def fetch(self, workspace, source, prop, dataset, start, end):
        return [value for key, value in self.rows.items()
                if key[:4] == (workspace, source, prop, dataset) and start <= key[4] <= end]


class MarketingTests(unittest.IsolatedAsyncioTestCase):
    def test_ga4(self):
        rows = parse_ga4({"dimensionHeaders": [{"name": "date"}, {"name": "landingPage"}],
                          "metricHeaders": [{"name": "sessions"}],
                          "rows": [{"dimensionValues": [{"value": "20260901"}, {"value": "/pricing"}],
                                    "metricValues": [{"value": "120"}]}]})
        self.assertEqual(rows[0], Observation(date(2026, 9, 1), {"landingPage": "/pricing"}, {"sessions": 120}))

    def test_reject_malformed_reports(self):
        with self.assertRaises(ValueError):
            parse_search_console({}, ["query"])
        with self.assertRaises(ValueError):
            parse_search_console({"rows": [{"keys": ["2026-09-01"]}]}, ["date", "page"])
        with self.assertRaises(ValueError):
            Observation(date.today(), {}, {"sessions": float("nan")})

    async def test_retry_isolation_weighting_and_window(self):
        store = MemoryStore()
        service = MarketingService(store)
        report = {"rows": [
            {"keys": ["2026-09-01", "buy shoes", "/shoes"], "clicks": 1, "impressions": 1000, "position": 3},
            {"keys": ["2026-09-02", "buy shoes", "/shoes"], "clicks": 1, "impressions": 10, "position": 9},
        ]}
        rows = parse_search_console(report, ["date", "query", "page"])
        for _ in range(2):
            await service.ingest("a", "search_console", "site", "v1", rows)
        await service.ingest("b", "search_console", "site", "v1", rows)
        await service.ingest("a", "search_console", "other-site", "v1", rows)
        opportunities = await service.opportunities("a", "site", "v1", date(2026, 9, 1), date(2026, 9, 2))
        self.assertEqual(len(opportunities), 1)
        evidence = opportunities[0]["evidence"]
        self.assertEqual(evidence["impressions"], 1010)
        self.assertAlmostEqual(evidence["ctr"], 2 / 1010)
        self.assertAlmostEqual(evidence["average_position"], 3090 / 1010)
        self.assertEqual(await service.opportunities("a", "site", "v1", date(2026, 8, 1), date(2026, 8, 2)), [])

    async def test_duplicate_batch_is_rejected_before_write(self):
        store = MemoryStore()
        row = Observation(date.today(), {}, {"sessions": 10})
        with self.assertRaises(ValueError):
            await MarketingService(store).ingest("a", "ga4", "site", "v1", [row, row])
        self.assertEqual(store.rows, {})


if __name__ == "__main__":
    unittest.main()
