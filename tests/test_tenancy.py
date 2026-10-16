"""Tenant layer integration tests.

Run against a live backend (set WORKER_URL, default http://localhost:8000):

    pytest tests/test_tenancy.py -v

These use the X-User-ID fallback (still enabled in the worker) so they need no
Cognito token, matching the rest of the HTTP integration suite.
"""

import pytest

from tests.conftest import make_request, TEST_USER_1_ID, TEST_USER_2_ID


def _provision_workspace(user_id: str) -> str:
    r = make_request("POST", "/client/workspace", user_id)
    assert r.status_code == 200, r.text
    return r.json()["workspace_id"]


def test_self_serve_provisions_tenant(health_check):
    _provision_workspace(TEST_USER_1_ID)

    r = make_request("GET", "/client/tenant", TEST_USER_1_ID)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "owner"
    assert body["tenant"]["id"]
    assert body["tenant"]["slug"]
    assert body["tenant"]["name"]


def test_branding_roundtrip(health_check):
    _provision_workspace(TEST_USER_1_ID)

    r = make_request(
        "PUT",
        "/client/tenant/branding",
        TEST_USER_1_ID,
        json={"branding": {"name": "Acme Org", "primary_color": "#123456"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["branding"]["name"] == "Acme Org"

    r = make_request("GET", "/client/tenant", TEST_USER_1_ID)
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["branding"]["primary_color"] == "#123456"


def test_tenant_lists_workspaces_and_members(health_check):
    workspace_id = _provision_workspace(TEST_USER_1_ID)

    r = make_request("GET", "/client/tenant/workspaces", TEST_USER_1_ID)
    assert r.status_code == 200, r.text
    assert any(w["workspace_id"] == workspace_id for w in r.json()["data"])

    r = make_request("GET", "/client/tenant/members", TEST_USER_1_ID)
    assert r.status_code == 200, r.text
    assert any(m["role"] == "owner" for m in r.json()["data"])


def test_cross_tenant_isolation(health_check):
    workspace_id = _provision_workspace(TEST_USER_1_ID)

    # A different user must not reach user 1's workspace, even with a valid id.
    r = make_request(
        "GET", f"/client/jobs?workspace_id={workspace_id}", TEST_USER_2_ID
    )
    assert r.status_code == 403, r.text
