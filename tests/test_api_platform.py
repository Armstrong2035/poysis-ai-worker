import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import marketing, platform
from app.primitives.platform.api_keys import hash_key, issue_key


class APIPlatformTests(unittest.TestCase):
    def setUp(self):
        self.store = AsyncMock()
        self.marketing_store = AsyncMock()
        self.app = FastAPI()
        self.app.include_router(platform.router)
        self.app.include_router(platform.public_router)
        self.app.include_router(marketing.router)
        self.app.dependency_overrides[platform.get_key_store] = lambda: self.store
        self.app.dependency_overrides[platform.require_owner] = lambda: ("workspace-a", "owner-a")
        self.app.dependency_overrides[marketing.get_store] = lambda: self.marketing_store
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.identity = {"key_id": str(uuid4()), "client_id": str(uuid4()),
                         "workspace_id": "workspace-a", "client_name": "Bezalel",
                         "scopes": ["marketing:read"]}

    def test_issued_key_has_safe_prefix_and_hash(self):
        _, prefix, raw, digest = issue_key()
        self.assertTrue(raw.startswith(f"poysis_live_{prefix}."))
        self.assertEqual(hash_key(raw), digest)
        self.assertNotIn(raw, digest)

    def test_create_key_returns_secret_once(self):
        client_id = str(uuid4())
        self.store.get_client.return_value = {"client_id": client_id}
        self.store.create_key.return_value = {"key_id": str(uuid4()), "key_prefix": "abc", "api_key": "poysis_live_abc.secret"}
        response = self.client.post("/platform/api-keys?workspace_id=workspace-a", json={"name": "Bezalel", "scopes": ["marketing:read"]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["api_key"].startswith("poysis_live_"))
        self.store.create_key.assert_awaited_once()

    def test_rejects_unknown_scope_and_past_expiry(self):
        response = self.client.post("/platform/api-keys?workspace_id=workspace-a", json={"name": "Bad", "scopes": ["admin"]})
        self.assertEqual(response.status_code, 422)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        response = self.client.post("/platform/api-keys?workspace_id=workspace-a", json={"name": "Bad", "expires_at": past})
        self.assertEqual(response.status_code, 422)

    def test_connection_and_legacy_marketing_reads_derive_workspace(self):
        self.store.authenticate.return_value = self.identity
        headers = {"Authorization": "Bearer poysis_live_abc.secret"}
        response = self.client.get("/v1/marketing/connection", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["workspace_id"], "workspace-a")
        self.marketing_store.list_rules.return_value = []
        response = self.client.get("/marketing/rules", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.marketing_store.list_rules.assert_awaited_with("workspace-a", 100, 0)

    def test_key_cannot_override_workspace_or_write_owner_routes(self):
        self.store.authenticate.return_value = self.identity
        headers = {"Authorization": "Bearer poysis_live_abc.secret"}
        response = self.client.get("/marketing/rules?workspace_id=workspace-b", headers=headers)
        self.assertEqual(response.status_code, 403)
        response = self.client.put(f"/marketing/rules/{uuid4()}", headers=headers,
                                   json={"rule": {}, "expected_revision": 0})
        self.assertIn(response.status_code, (401, 403, 422))
        self.marketing_store.save_rule.assert_not_called()

    def test_invalid_key_is_unauthorized(self):
        self.store.authenticate.return_value = None
        response = self.client.get("/marketing/rules", headers={"Authorization": "Bearer poysis_live_bad.secret"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
