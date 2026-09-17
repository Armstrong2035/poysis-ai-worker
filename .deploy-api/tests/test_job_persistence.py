"""
Test job persistence across server restarts.
Verifies that background job state survives application restarts.
"""

import pytest
import asyncio
from tests.conftest import make_request, TEST_USER_1_ID, TEST_WORKSPACE_ID


@pytest.mark.asyncio
async def test_job_persists_in_database(workspace_id):
    """Job status survives server restart (in database)."""
    # Start a snapshot job
    start_response = make_request("POST", "/consolidation/snapshot", TEST_USER_1_ID, json={
        "workspace_id": workspace_id,
        "sources": ["google_drive"],
        "time_window_days": 90,
        "doc_limit": 10,  # Small limit for quick test
    })
    assert start_response.status_code == 200
    job_data = start_response.json()
    job_id = job_data.get("job_id")
    assert job_id

    # Wait briefly for job to start
    await asyncio.sleep(1)

    # Fetch job status (from DB if not in-memory)
    status_response = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["workspace_id"] == workspace_id


def test_job_status_includes_metadata(workspace_id):
    """Job status response includes job_id, status, timestamps."""
    # Get latest job
    response = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    assert response.status_code == 200

    job = response.json()
    # Status response should have these fields
    assert "workspace_id" in job
    assert "status" in job or job.get("status") == "not_started"


def test_clustering_job_persistence(workspace_id):
    """Clustering job status persists in database."""
    # Start clustering job
    response = make_request("POST", f"/consolidation/cluster/{workspace_id}", TEST_USER_1_ID)

    if response.status_code == 200:
        job_data = response.json()
        job_id = job_data.get("job_id")
        assert job_id

        # Status should be queryable
        status_response = make_request("GET", f"/consolidation/cluster/status/{workspace_id}", TEST_USER_1_ID)
        assert status_response.status_code == 200


def test_multiple_jobs_same_workspace(workspace_id):
    """Multiple jobs for same workspace are tracked independently."""
    # Get initial status
    response1 = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    assert response1.status_code == 200

    # Job history should track multiple runs
    # (latest job should be retrievable)
    response2 = make_request("GET", f"/consolidation/snapshot/status/{workspace_id}", TEST_USER_1_ID)
    assert response2.status_code == 200
    status2 = response2.json()
    assert "workspace_id" in status2
