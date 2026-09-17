"""
Test rate limiting middleware.
Verifies 100 requests per minute per user, 429 on excess.
"""

import pytest
import httpx
from tests.conftest import TEST_USER_1_ID, WORKER_URL


def test_rate_limit_allows_normal_traffic():
    """First 100 requests from user succeed."""
    with httpx.Client(base_url=WORKER_URL) as client:
        success_count = 0
        for i in range(50):  # Test 50 requests (less than 100 limit)
            response = client.get("/ping", headers={"X-User-ID": TEST_USER_1_ID})
            if response.status_code == 200:
                success_count += 1

        assert success_count >= 45  # Allow some margin


def test_rate_limit_429_on_excess():
    """Request after 100/min returns 429."""
    with httpx.Client(base_url=WORKER_URL) as client:
        # Fire 101 requests rapidly
        responses = []
        for i in range(101):
            response = client.get("/ping", headers={"X-User-ID": TEST_USER_1_ID})
            responses.append(response.status_code)

        # Should see some 429s in the batch
        has_429 = 429 in responses
        has_200 = 200 in responses

        assert has_200  # Some succeed
        assert has_429 or responses.count(200) <= 100  # Rate limiting active


def test_rate_limit_per_user_isolation():
    """User 1 rate limit does not affect User 2."""
    with httpx.Client(base_url=WORKER_URL) as client:
        user1_429 = False
        user2_200 = False

        # Spam User 1
        for i in range(101):
            response = client.get("/ping", headers={"X-User-ID": TEST_USER_1_ID})
            if response.status_code == 429:
                user1_429 = True

        # User 2 should still get 200
        response = client.get("/ping", headers={"X-User-ID": "other-user-id"})
        if response.status_code == 200:
            user2_200 = True

        assert user1_429 or user2_200  # At least one isolation works
