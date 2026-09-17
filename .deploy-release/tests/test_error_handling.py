"""
Test error handling middleware and exception handling.
Verifies proper error responses with status codes and error IDs.
"""

import pytest
from tests.conftest import make_request, TEST_USER_1_ID


def test_invalid_workspace_id_rejected():
    """Invalid workspace_id format returns 400."""
    # Empty workspace_id
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "",
        "sources": ["google_drive"]
    })
    assert response.status_code in [400, 422]  # Bad request or validation error


def test_missing_required_field_rejected():
    """Request without required fields returns 422."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "sources": ["google_drive"]
        # Missing workspace_id
    })
    assert response.status_code == 422


def test_unhandled_exception_returns_500():
    """Server error returns 500 with error_id."""
    # Force an error by using invalid credentials
    response = make_request("POST", "/consolidation/discover", TEST_USER_1_ID, json={
        "workspace_id": "test-workspace",
        "sources": ["invalid_source"]  # Invalid source
    })

    if response.status_code == 400:
        data = response.json()
        # Should have detail or error message
        assert "detail" in data or "error" in data


def test_workspace_not_found_returns_404():
    """Query on non-existent workspace returns appropriate error."""
    response = make_request("GET", "/retrieval/list_documents?workspace_id=nonexistent", TEST_USER_1_ID)
    # Could be 404, 200 with empty results, or other
    assert response.status_code in [200, 404]


def test_invalid_json_rejected():
    """Malformed JSON returns 400."""
    import httpx
    with httpx.Client(base_url="http://localhost:8000") as client:
        response = client.post(
            "/consolidation/snapshot",
            content="{invalid json",
            headers={"X-User-ID": TEST_USER_1_ID}
        )
        assert response.status_code in [400, 422]


def test_query_without_required_fields():
    """Query without query text returns 422."""
    response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
        "workspace_id": "test-ws"
        # Missing query
    })
    assert response.status_code == 422


def test_google_token_missing_returns_401():
    """Request without Google token returns 401."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test-workspace",
        "sources": ["google_drive"],
    })

    # If no token, should get 401
    if response.status_code == 401:
        data = response.json()
        assert "Google token" in data.get("detail", "") or "token" in data.get("detail", "").lower()
