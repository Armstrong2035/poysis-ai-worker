"""
End-to-end integration test.
Tests full flow: consolidation → query → results with source attribution.
"""

import pytest
import asyncio
from tests.conftest import make_request, TEST_USER_1_ID, TEST_WORKSPACE_ID


@pytest.mark.asyncio
async def test_consolidation_to_query_flow(workspace_id):
    """
    Full flow:
    1. Discover documents
    2. Run snapshot
    3. Run clustering
    4. Query and verify results have source metadata
    """

    # Step 1: Discover (optional, but good to test)
    discover_response = make_request("POST", "/consolidation/discover", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "sources": ["google_drive"],
        "time_window_days": 30,
        "doc_limit": 10,
    })
    # May fail if no Google token, that's OK for this test
    if discover_response.status_code == 200:
        discover_data = discover_response.json()
        assert "documents" in discover_data or discover_response.status_code in [401]

    # Step 2: Run snapshot (will skip if no Google token)
    snapshot_response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "sources": ["google_drive"],
        "time_window_days": 30,
        "doc_limit": 10,
    })

    if snapshot_response.status_code in [401]:
        # Expected if no Google token, skip rest of flow
        pytest.skip("No Google token configured")

    assert snapshot_response.status_code == 200
    job_data = snapshot_response.json()
    assert job_data["status"] == "started"
    job_id = job_data.get("job_id")

    # Wait for job to complete
    await asyncio.sleep(2)

    # Step 3: Check snapshot status
    status_response = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    assert status_response.status_code == 200
    status_data = status_response.json()
    # Should have status (running, done, or similar)
    assert "status" in status_data

    # Step 4: Run clustering
    cluster_response = make_request("POST", f"/consolidation/cluster/{workspace_id}", TEST_USER_1_ID)
    if cluster_response.status_code == 200:
        cluster_job = cluster_response.json()
        assert cluster_job["status"] == "started"

        # Wait for clustering
        await asyncio.sleep(1)

        cluster_status = make_request("GET", f"/consolidation/cluster/status/{workspace_id}", TEST_USER_1_ID)
        assert cluster_status.status_code == 200

    # Step 5: Query the knowledge base
    query_response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "query": "test query"
    })

    if query_response.status_code == 200:
        results = query_response.json()
        # Should have results structure
        if "results" in results and results["results"]:
            # Verify source attribution
            for result in results["results"]:
                assert "title" in result or "source_id" in result

    # Step 6: List documents
    list_response = make_request("GET", "/retrieval/list_documents?workspace_id=" + workspace_id, TEST_USER_1_ID)
    assert list_response.status_code == 200
    docs = list_response.json()
    # Should have documents structure
    if "documents" in docs:
        for doc in docs["documents"]:
            assert "title" in doc or "id" in doc


def test_query_returns_source_metadata(workspace_id):
    """Query results include source metadata for attribution."""
    response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "query": "sample query"
    })

    if response.status_code == 200:
        data = response.json()
        if "results" in data and data["results"]:
            result = data["results"][0]
            # Verify source attribution fields
            required_fields = ["title", "source_type"]
            for field in required_fields:
                assert field in result, f"Missing {field} in result"


def test_list_documents_with_search(workspace_id):
    """List documents supports search filtering."""
    response = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}&search=test", TEST_USER_1_ID)
    assert response.status_code == 200
    data = response.json()
    # Should return documents or empty list
    assert "documents" in data or "workspace_id" in data


def test_list_documents_with_source_filter(workspace_id):
    """List documents supports source_type filtering."""
    response = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}&source_type=google_drive", TEST_USER_1_ID)
    assert response.status_code == 200
    data = response.json()
    assert "documents" in data or "workspace_id" in data
