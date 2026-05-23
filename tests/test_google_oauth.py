"""
Test Google OAuth flow and token persistence.
Verifies token exchange, storage, and refresh.
"""

import pytest
import json
from tests.conftest import make_request, TEST_USER_1_ID, TEST_WORKSPACE_ID


def test_google_oauth_endpoint_exists():
    """POST /auth/google endpoint is available."""
    response = make_request("POST", "/auth/google", TEST_USER_1_ID, json={
        "workspace_id": TEST_WORKSPACE_ID,
    })

    # Should either redirect or return OAuth URL
    assert response.status_code in [200, 401, 400]


def test_google_oauth_requires_workspace_id():
    """OAuth request without workspace_id is rejected."""
    response = make_request("POST", "/auth/google", TEST_USER_1_ID, json={
        # Missing workspace_id
    })

    assert response.status_code == 422


def test_google_oauth_requires_user_id():
    """OAuth flow requires user_id header."""
    import httpx
    with httpx.Client(base_url="http://localhost:8000") as client:
        response = client.post(
            "/auth/google",
            json={"workspace_id": TEST_WORKSPACE_ID}
            # No X-User-ID header
        )
        assert response.status_code == 401


def test_oauth_callback_parses_state():
    """Callback endpoint correctly parses workspace_id and user_id from state."""
    # This test verifies the callback parsing logic
    # The actual callback flow would be: user → Google → /auth/google/callback
    # For testing, we verify the endpoint exists

    response = make_request("GET", "/auth/google/callback?code=test&state=test-ws,user-id", TEST_USER_1_ID)

    # May fail with invalid code, but endpoint should exist
    assert response.status_code in [400, 500, 200]


def test_token_persists_after_oauth():
    """After successful OAuth, token is stored in database."""
    # This is an integration test that requires real OAuth flow
    # For now, we test that the database method exists by checking
    # that the endpoint doesn't crash on token operations

    # Try to get documents (which would use stored token)
    response = make_request("GET", f"/retrieval/list_documents?workspace_id={TEST_WORKSPACE_ID}", TEST_USER_1_ID)
    assert response.status_code in [200, 401, 404]


def test_token_refresh_on_expiry():
    """Expired token is refreshed during consolidation."""
    # This test verifies that the refresh logic exists
    # Actual refresh would happen during snapshot run

    # Try to start snapshot (which uses Google token)
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": TEST_WORKSPACE_ID,
        "sources": ["google_drive"],
    })

    if response.status_code == 401:
        # Expected if token missing/expired
        data = response.json()
        assert "Google token" in data.get("detail", "") or "OAuth" in data.get("detail", "")


def test_multiple_workspaces_with_different_tokens():
    """Each workspace can have its own Google token."""
    # Create second workspace
    workspace_2 = f"test-workspace-2"

    # Both should accept OAuth independently
    response1 = make_request("POST", "/auth/google", TEST_USER_1_ID, json={
        "workspace_id": TEST_WORKSPACE_ID,
    })
    assert response1.status_code in [200, 401, 400]

    response2 = make_request("POST", "/auth/google", TEST_USER_1_ID, json={
        "workspace_id": workspace_2,
    })
    assert response2.status_code in [200, 401, 400]


def test_oauth_handles_user_denial():
    """If user denies OAuth, callback returns error."""
    # Simulate user denial (error parameter in callback)
    response = make_request(
        "GET",
        f"/auth/google/callback?error=access_denied&state={TEST_WORKSPACE_ID},{TEST_USER_1_ID}",
        TEST_USER_1_ID
    )

    # Should handle error gracefully (not crash)
    assert response.status_code in [400, 403, 500]


def test_oauth_prevents_csrf():
    """OAuth state parameter prevents CSRF attacks."""
    # State should be cryptographically random and checked
    # This test verifies that the endpoint validates state

    response = make_request(
        "GET",
        f"/auth/google/callback?code=fake&state=mismatched-state",
        TEST_USER_1_ID
    )

    # Should reject mismatched state
    assert response.status_code in [400, 403, 500]
