"""
Shared test fixtures and utilities.
"""

import pytest
import httpx
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

# Test configuration
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
TEST_USER_1_ID = str(uuid.uuid4())
TEST_USER_2_ID = str(uuid.uuid4())
TEST_WORKSPACE_ID = f"test-workspace-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client():
    """HTTP client for testing."""
    return httpx.Client(base_url=WORKER_URL)


@pytest.fixture
def async_client():
    """Async HTTP client for testing."""
    return httpx.AsyncClient(base_url=WORKER_URL)


def make_request(method: str, path: str, user_id: str, **kwargs):
    """Helper to make authenticated requests."""
    headers = kwargs.pop("headers", {})
    headers["X-User-ID"] = user_id

    with httpx.Client(base_url=WORKER_URL) as client:
        if method == "GET":
            return client.get(path, headers=headers, **kwargs)
        elif method == "POST":
            return client.post(path, headers=headers, **kwargs)
        elif method == "DELETE":
            return client.delete(path, headers=headers, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")


async def make_async_request(method: str, path: str, user_id: str, **kwargs):
    """Helper to make authenticated async requests."""
    headers = kwargs.pop("headers", {})
    headers["X-User-ID"] = user_id

    async with httpx.AsyncClient(base_url=WORKER_URL) as client:
        if method == "GET":
            return await client.get(path, headers=headers, **kwargs)
        elif method == "POST":
            return await client.post(path, headers=headers, **kwargs)
        elif method == "DELETE":
            return await client.delete(path, headers=headers, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")


@pytest.fixture
def test_user_1():
    """Test user 1 ID."""
    return TEST_USER_1_ID


@pytest.fixture
def test_user_2():
    """Test user 2 ID."""
    return TEST_USER_2_ID


@pytest.fixture
def workspace_id():
    """Test workspace ID."""
    return TEST_WORKSPACE_ID


@pytest.fixture
def health_check(client):
    """Verify backend is running."""
    try:
        response = client.get("/ping")
        assert response.status_code == 200
        return True
    except Exception as e:
        pytest.skip(f"Backend not running: {e}")
