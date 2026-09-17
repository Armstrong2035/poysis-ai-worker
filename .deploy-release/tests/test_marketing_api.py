import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import marketing
from app.api.security import get_user_id
from app.primitives.marketing.rules import OpportunityRule


class MarketingAPITests(unittest.TestCase):
    def setUp(self):
        self.store = AsyncMock()
        self.interpreter = AsyncMock()
        self.workspace_patch = patch("app.primitives.database.DatabaseService.get_workspace", new_callable=AsyncMock)
        self.workspace = self.workspace_patch.start()
        self.workspace.return_value = {"user_id": "owner"}
        self.member_patch = patch("app.primitives.database.DatabaseService.has_workspace_access", new_callable=AsyncMock)
        self.membership = self.member_patch.start()
        self.membership.return_value = True
        self.addCleanup(self.workspace_patch.stop)
        self.addCleanup(self.member_patch.stop)
        self.app = FastAPI()
        self.app.include_router(marketing.router)
        self.app.dependency_overrides[get_user_id] = lambda: "owner"
        self.app.dependency_overrides[marketing.get_store] = lambda: self.store
        self.app.dependency_overrides[marketing.get_target_interpreter] = lambda: self.interpreter
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.rule_id = str(uuid4())
        self.rule = OpportunityRule(name="Invoices", property_id="site", dataset="v1", window_days=7)
        self.record = {"rule_id": self.rule_id, "config": self.rule.model_dump(), "revision": 1}

    def test_data_pagination_filters_and_scope(self):
        self.store.data.return_value = [{"day": "2026-09-01"}, {"day": "2026-09-02"}]
        response = self.client.get("/marketing/data", params={
            "workspace_id": "a", "source": "ga4", "property_id": "site", "dataset": "v1",
            "start": "2026-09-01", "end": "2026-09-10", "limit": 1,
            "dimensions": '{"landingPage":"/invoice"}'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["next_offset"], 1)
        self.assertEqual(len(response.json()["data"]), 1)
        args = self.store.data.call_args.args
        self.assertEqual(args[:4], ("a", "ga4", "site", "v1"))
        self.assertEqual(args[6], {"landingPage": "/invoice"})

    def test_nonmember_cannot_read_or_preview(self):
        self.membership.return_value = False
        self.app.dependency_overrides[get_user_id] = lambda: "stranger"
        self.assertEqual(self.client.get("/marketing/rules?workspace_id=a").status_code, 403)
        response = self.client.post("/marketing/rules/preview?workspace_id=a", json={
            "text": "invoice opportunities", "property_id": "site", "dataset": "v1"})
        self.assertEqual(response.status_code, 403)
        self.interpreter.assert_not_called()
        self.store.list_rules.assert_not_called()

    def test_member_can_read_but_not_edit(self):
        self.app.dependency_overrides[get_user_id] = lambda: "member"
        self.store.list_rules.return_value = [self.record]
        self.assertEqual(self.client.get("/marketing/rules?workspace_id=a").status_code, 200)
        response = self.client.put(f"/marketing/rules/{self.rule_id}?workspace_id=a", json={
            "rule": self.rule.model_dump(), "expected_revision": 0})
        self.assertEqual(response.status_code, 403)
        self.store.save_rule.assert_not_called()

    def test_save_and_revision_conflict(self):
        self.store.save_rule.return_value = self.record
        payload = {"rule": self.rule.model_dump(), "expected_revision": 0}
        url = f"/marketing/rules/{self.rule_id}?workspace_id=a"
        self.assertEqual(self.client.put(url, json=payload).status_code, 200)
        self.assertEqual(self.store.save_rule.call_args.args[0], "a")
        self.assertEqual(self.store.save_rule.call_args.args[-2:], ("owner", 0))
        self.store.save_rule.return_value = None
        self.assertEqual(self.client.put(url, json=payload).status_code, 409)
        payload["rule"]["ctr_threshold"] = 3
        self.assertEqual(self.client.put(url, json=payload).status_code, 422)

    def test_opportunity_window_disabled_and_missing(self):
        self.store.get_rule.return_value = self.record
        self.store.fetch.return_value = []
        url = f"/marketing/opportunities?workspace_id=a&rule_id={self.rule_id}&end=2026-09-10"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["start"], "2026-09-04")
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(response.json()["coverage"], "unverified")
        self.store.get_rule.assert_awaited_with("a", marketing.UUID(self.rule_id))
        self.store.fetch.reset_mock()
        self.record["config"]["enabled"] = False
        self.assertEqual(self.client.get(url).json()["data"], [])
        self.store.fetch.assert_not_called()
        self.store.get_rule.return_value = None
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_preview_never_saves_and_errors_are_generic(self):
        payload = {"text": "Find invoice opportunities", "property_id": "site", "dataset": "v1"}
        self.interpreter.return_value = {"status": "needs_clarification", "saved": False}
        url = "/marketing/rules/preview?workspace_id=a"
        self.assertEqual(self.client.post(url, json=payload).status_code, 200)
        self.store.save_rule.assert_not_called()
        self.interpreter.side_effect = ValueError("raw private model output")
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private", response.text)
        self.interpreter.side_effect = TimeoutError()
        self.assertEqual(self.client.post(url, json=payload).status_code, 503)

    def test_invalid_window_and_pagination(self):
        params = {"workspace_id": "a", "source": "ga4", "property_id": "site", "dataset": "v1",
                  "start": "2026-09-10", "end": "2026-09-01"}
        self.assertEqual(self.client.get("/marketing/data", params=params).status_code, 422)
        self.assertEqual(self.client.get("/marketing/rules?workspace_id=a&limit=1001").status_code, 422)
        self.store.data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
