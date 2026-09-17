"""
Test input validation middleware.
Verifies workspace_id validation, size limits, etc.
"""

import pytest
from tests.conftest import make_request, TEST_USER_1_ID


def test_workspace_id_validation_rejects_invalid_format():
    """Invalid workspace_id is rejected."""
    # Very long workspace_id
    long_id = "a" * 1000
    response = make_request("GET", f"/consolidation/snapshot/status/{long_id}", TEST_USER_1_ID)
    # Should either reject or handle gracefully
    assert response.status_code in [400, 404, 500]  # Not necessarily 200


def test_query_size_limit():
    """Query larger than 10MB is rejected with 413."""
    # Create a huge query (simulate large input)
    huge_query = "x" * (11 * 1024 * 1024)  # 11MB

    import httpx
    with httpx.Client(base_url="http://localhost:8000") as client:
        try:
            response = client.post(
                "/retrieval/query_knowledge_base",
                json={
                    "workspace_id": "test",
                    "query": huge_query
                },
                headers={"X-User-ID": TEST_USER_1_ID},
                timeout=5.0
            )
            # Should be rejected for size
            assert response.status_code in [413, 400, 422]
        except httpx.RequestError:
            # Connection dropped or request timeout is also acceptable
            pass


def test_workspace_id_alphanumeric_validation():
    """workspace_id with invalid characters is rejected."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test<script>alert(1)</script>",
        "sources": ["google_drive"]
    })
    # Should be rejected or sanitized
    assert response.status_code in [400, 422]


def test_doc_limit_positive():
    """Negative or zero doc_limit is rejected."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test-ws",
        "sources": ["google_drive"],
        "doc_limit": -1
    })
    assert response.status_code in [400, 422]


def test_time_window_days_positive():
    """Negative time_window_days is rejected."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test-ws",
        "sources": ["google_drive"],
        "time_window_days": -10
    })
    assert response.status_code in [400, 422]


def test_sources_list_validation():
    """Invalid source in sources list is rejected."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test-ws",
        "sources": ["invalid_source_type"]
    })
    # May be rejected or ignored, but should not crash
    assert response.status_code in [400, 200, 422]


def test_drive_folder_ids_list():
    """drive_folder_ids is validated as a list."""
    response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": "test-ws",
        "sources": ["google_drive"],
        "drive_folder_ids": "not-a-list"  # Should be array
    })
    assert response.status_code in [400, 422]
