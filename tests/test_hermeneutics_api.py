import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import interpretations
from app.api.security import get_user_id
from hermeneutics import HermeneuticsEngine, Scope, SQLiteRepository
from test_hermeneutics_engine import FixtureAdapter, FixtureModel, request


class InterpretationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteRepository(Path(self.temp.name) / "api.sqlite")
        self.engine = HermeneuticsEngine(self.repo, lambda scope: FixtureAdapter(), FixtureModel())
        self.app = FastAPI()
        self.app.include_router(interpretations.router)
        self.app.dependency_overrides[interpretations.get_engine] = lambda: self.engine
        self.app.dependency_overrides[get_user_id] = lambda: "member"
        for method, value in [("get_workspace", {"user_id": "owner"}), ("has_workspace_access", True)]:
            patcher = patch(f"app.primitives.database.DatabaseService.{method}", new_callable=AsyncMock)
            mock = patcher.start()
            mock.return_value = value
            self.addCleanup(patcher.stop)
            if method == "has_workspace_access":
                self.membership = mock
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_submit_poll_idempotency_and_scope(self):
        url = "/interpretations?workspace_id=workspace-a"
        body = request().model_dump(mode="json")
        response = self.client.post(url, json=body, headers={"Idempotency-Key": "one"})
        self.assertEqual(response.status_code, 202, response.text)
        identity = response.json()["id"]
        self.assertEqual(self.client.post(url, json=body, headers={"Idempotency-Key": "one"}).json()["id"], identity)
        self.assertEqual(self.client.get(f"/interpretations/{identity}?workspace_id=workspace-a").status_code, 200)
        self.assertEqual(self.client.get(f"/interpretations/{identity}?workspace_id=workspace-b").status_code, 404)
        body["question"] = "Different question"
        self.assertEqual(self.client.post(url, json=body, headers={"Idempotency-Key": "one"}).status_code, 409)

    def test_membership_required_and_header_required(self):
        body = request().model_dump(mode="json")
        self.assertEqual(self.client.post("/interpretations?workspace_id=workspace-a", json=body).status_code, 422)
        self.membership.return_value = False
        self.assertEqual(self.client.post("/interpretations?workspace_id=workspace-a", json=body,
                                         headers={"Idempotency-Key": "one"}).status_code, 403)

    def test_complete_pipeline_through_client_endpoints(self):
        import asyncio
        response = self.client.post("/interpretations?workspace_id=workspace-a", json=request().model_dump(mode="json"),
                                    headers={"Idempotency-Key": "pipeline"})
        identity = response.json()["id"]
        asyncio.run(self.engine.run_next())
        result = self.client.get(f"/interpretations/{identity}?workspace_id=workspace-a")
        self.assertEqual(result.json()["status"], "completed")
        evidence = self.client.get(f"/interpretations/{identity}/evidence?workspace_id=workspace-a")
        self.assertGreater(len(evidence.json()["links"]), 0)
