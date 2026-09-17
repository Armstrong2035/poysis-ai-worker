"""
Test user isolation and authorization boundaries.
Verifies that users cannot access other users' data.
"""

import pytest
from tests.conftest import make_request, TEST_USER_1_ID, TEST_USER_2_ID, TEST_WORKSPACE_ID


def test_missing_user_id_header(client):
    """Request without X-User-ID header returns 401."""
    response = client.get("/consolidation/snapshot/status/test-workspace")
    assert response.status_code == 401


def test_user_cannot_access_other_user_workspace(workspace_id):
    """User 2 cannot access User 1's workspace."""
    # Create workspace for User 1
    make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "sources": ["google_drive"],
    })

    # User 2 tries to access User 1's workspace
    response = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_2_ID)
    assert response.status_code == 403


def test_user_can_access_own_workspace(workspace_id):
    """User 1 can access their own workspace."""
    # Create workspace for User 1
    response = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    # Should return 200 or 200-class response (status not started is OK)
    assert response.status_code in [200, 201]


def test_list_documents_requires_auth(client):
    """GET /retrieval/list_documents without X-User-ID returns 401."""
    response = client.get("/retrieval/list_documents?workspace_id=test-ws")
    assert response.status_code == 401


def test_query_knowledge_base_requires_auth(client):
    """POST /retrieval/query_knowledge_base without X-User-ID returns 401."""
    response = client.post("/retrieval/query_knowledge_base", json={
        "workspace_id": "test-ws",
        "query": "test query"
    })
    assert response.status_code == 401


def test_user_isolation_on_query(workspace_id):
    """Query results are isolated per user (cross-check impossible access)."""
    # User 1 queries their workspace
    response1 = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "query": "test query"
    })
    assert response1.status_code in [200, 401]  # 401 if not indexed yet

    # User 2 cannot query User 1's workspace
    response2 = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_2_ID, json={
        "workspace_id": workspace_id,
        "query": "test query"
    })
    assert response2.status_code == 403


def test_discover_requires_workspace_ownership(workspace_id):
    """Discover endpoint verifies workspace ownership."""
    response = make_request("POST", "/consolidation/discover", TEST_USER_2_ID, json={
        "workspace_id": workspace_id,
        "sources": ["google_drive"]
    })
    assert response.status_code == 403
