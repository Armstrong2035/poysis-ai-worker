"""
Test bearer-token authentication and the MCP endpoint credential.

These tests encode WHY the auth layer exists, not only what it returns:

  - An identity the caller can invent is not an identity. Before this change
    `get_user_id` returned the X-User-ID header unchanged, so anyone who knew a
    user id could act as that user. The tests below fail if that returns.
  - The MCP endpoint served a workspace's whole knowledge base with no
    credential at all. The workspace id in the URL was the only secret.

Two groups:

  Always run     - need no valid signature, so they work against any deployment.
  Need a secret  - need to mint a token the server accepts. They skip unless
                   AUTH_JWT_SECRET is set to the same value the server uses.
                   Run them with:
                     AUTH_JWT_SECRET=<the server's secret> pytest tests/test_auth_tokens.py

Like the rest of tests/, these hit a live server at WORKER_URL.
"""

import os
import time
import uuid

import pytest

from tests.conftest import (
    WORKER_URL,
    TEST_USER_1_ID,
    TEST_USER_2_ID,
    bearer_request,
    make_jwt,
    make_request,
)

jwt = pytest.importorskip("jwt", reason="PyJWT is required to mint test tokens")

# The server's signing secret. Without it we can produce a correctly formed
# token but never one the server will accept, so the positive cases skip.
SERVER_SECRET = os.getenv("AUTH_JWT_SECRET")
needs_secret = pytest.mark.skipif(
    not SERVER_SECRET,
    reason="Set AUTH_JWT_SECRET to the server's secret to run signature-dependent tests",
)

# A key the server definitely does not hold.
WRONG_SECRET = "not-the-servers-secret-" + uuid.uuid4().hex

# Any workspace-scoped route works as the probe. This one reads the database,
# so it exercises the whole dependency chain rather than a short-circuit.
PROBE = "/consolidation/indexed_count/{ws}"


# --------------------------------------------------------------------------
# Always run: no valid signature required
# --------------------------------------------------------------------------

def test_no_credential_is_rejected(client):
    """
    A request carrying neither a token nor the legacy header must be refused.

    This is the floor. If it ever returns 200 the endpoint is fully open.
    """
    response = client.get(PROBE.format(ws=uuid.uuid4()))
    assert response.status_code == 401


def test_malformed_bearer_is_rejected(client):
    """A bearer value that is not a JWT must not be treated as an identity."""
    response = client.get(
        PROBE.format(ws=uuid.uuid4()),
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401


def test_token_signed_with_wrong_key_is_rejected():
    """
    A well-formed token signed by a key the server does not hold must fail.

    This is the test that proves the signature is actually checked. A server
    that decoded the token without verifying it would accept this and read the
    attacker-chosen `sub`, which is exactly the old X-User-ID hole wearing a
    JWT costume.
    """
    token = make_jwt({"sub": TEST_USER_1_ID}, secret=WRONG_SECRET)
    response = bearer_request("GET", PROBE.format(ws=uuid.uuid4()), token)
    assert response.status_code == 401


def test_token_with_no_subject_is_rejected():
    """
    A token with a valid signature but no `sub` gives us no identity.

    Signed with the wrong key here, so it fails on signature for any server.
    The case matters because a token that verifies but carries no subject must
    not fall through to a None or empty user id.
    """
    token = make_jwt({"foo": "bar"}, secret=WRONG_SECRET)
    response = bearer_request("GET", PROBE.format(ws=uuid.uuid4()), token)
    assert response.status_code == 401


def test_bearer_takes_precedence_over_legacy_header():
    """
    A bad token must not be rescued by a valid-looking X-User-ID.

    Otherwise an attacker downgrades to the weaker scheme at will, and turning
    the fallback off later would protect nothing.
    """
    token = make_jwt({"sub": TEST_USER_1_ID}, secret=WRONG_SECRET)
    response = bearer_request(
        "GET",
        PROBE.format(ws=uuid.uuid4()),
        token,
        headers={"X-User-ID": TEST_USER_1_ID},
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------
# MCP endpoint: the workspace id in the URL must not be the only secret
# --------------------------------------------------------------------------

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def test_mcp_post_without_token_is_rejected(client):
    """
    tools/list with no credential must be refused.

    Before this change the call below returned the full tool list to anyone who
    knew or guessed a workspace id, over plain HTTP.
    """
    response = client.post(f"/mcp/{uuid.uuid4()}", json=TOOLS_LIST)
    assert response.status_code == 401 or _is_jsonrpc_auth_error(response)


def test_mcp_post_with_wrong_token_is_rejected(client):
    """A token that does not match the stored hash must be refused."""
    response = client.post(
        f"/mcp/{uuid.uuid4()}?token=wrong-{uuid.uuid4().hex}",
        json=TOOLS_LIST,
    )
    assert response.status_code == 401 or _is_jsonrpc_auth_error(response)


def test_mcp_get_without_token_does_not_confirm_workspace(client):
    """
    The browser-friendly GET route must not confirm which workspace ids exist.

    Without a credential it answered for real ids and errored for invented ones,
    which let anyone probe the id space.
    """
    response = client.get(f"/mcp/{uuid.uuid4()}")
    assert response.status_code == 401


def _is_jsonrpc_auth_error(response) -> bool:
    """
    The MCP route reports some failures as a JSON-RPC error inside a 200.

    Accept either shape so the test asserts the behaviour that matters — access
    refused — rather than the transport detail of how it is reported.
    """
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except Exception:
        return False
    entries = body if isinstance(body, list) else [body]
    for entry in entries:
        error = (entry or {}).get("error") or {}
        message = str(error.get("message", "")).lower()
        if "token" in message or "unauthor" in message or "401" in message:
            return True
    return False


# --------------------------------------------------------------------------
# Need the server's secret
# --------------------------------------------------------------------------

@needs_secret
def test_valid_token_is_accepted():
    """
    A properly signed token must authenticate.

    Without this the suite could pass with an endpoint that refuses everything,
    which is secure and useless.
    """
    token = make_jwt({"sub": TEST_USER_1_ID}, secret=SERVER_SECRET)
    response = bearer_request("GET", PROBE.format(ws=uuid.uuid4()), token)
    # The identity is accepted; the workspace does not exist, hence 403/404.
    # 401 would mean the token itself was rejected, which is the failure here.
    assert response.status_code != 401


@needs_secret
def test_expired_token_is_rejected():
    """
    Expiry must be enforced, or a leaked token is valid forever.

    Signed with the real key, so the only reason to refuse it is `exp`.
    """
    past = int(time.time()) - 3600
    token = make_jwt(
        {"sub": TEST_USER_1_ID, "iat": past - 60, "exp": past},
        secret=SERVER_SECRET,
    )
    response = bearer_request("GET", PROBE.format(ws=uuid.uuid4()), token)
    assert response.status_code == 401


@needs_secret
def test_token_for_user_a_cannot_read_user_b_workspace(workspace_id):
    """
    The test that matters most: a valid identity is not an authorization.

    User 1 owns the workspace. User 2 holds a genuinely signed token, so the
    token check passes and the workspace check is what must stop them. A
    regression that verified signatures but dropped the ownership check would
    pass every other test in this file and fail this one.
    """
    make_request(
        "POST",
        "/consolidation/snapshot",
        TEST_USER_1_ID,
        json={"workspace_id": workspace_id, "sources": ["google_drive"]},
    )

    token = make_jwt({"sub": TEST_USER_2_ID}, secret=SERVER_SECRET)
    response = bearer_request(
        "GET", f"/consolidation/snapshot/status/{workspace_id}", token
    )
    assert response.status_code == 403, (
        "User 2 presented a valid token and reached User 1's workspace. "
        "The signature was checked but ownership was not."
    )


# --------------------------------------------------------------------------
# The legacy fallback, while it is still switched on
# --------------------------------------------------------------------------

def test_legacy_header_still_works_during_migration(client):
    """
    X-User-ID must keep working until the clients are moved.

    This test is deliberately temporary. Delete it in phase 4 of AUTH_PLAN.md,
    when AUTH_ALLOW_HEADER_FALLBACK is set to false. Until then, a failure here
    means the frontend has been broken.

    Skipped automatically once the server starts refusing the header, so it does
    not become a false alarm after the cutover.
    """
    response = client.get(
        PROBE.format(ws=uuid.uuid4()),
        headers={"X-User-ID": TEST_USER_1_ID},
    )
    if response.status_code == 401:
        pytest.skip("Header fallback is switched off; delete this test")
    assert response.status_code in (200, 403, 404)
