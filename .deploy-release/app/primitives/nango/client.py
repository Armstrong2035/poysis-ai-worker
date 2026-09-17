import hashlib
import hmac
import os
from typing import Optional

import httpx

_BASE_URL: Optional[str] = None
_SECRET_KEY: Optional[str] = None


def _base_url() -> str:
    global _BASE_URL
    if _BASE_URL is None:
        _BASE_URL = os.getenv("NANGO_BASE_URL", "").rstrip("/")
    return _BASE_URL


def _secret_key() -> str:
    global _SECRET_KEY
    if _SECRET_KEY is None:
        _SECRET_KEY = os.getenv("NANGO_SECRET_KEY", "")
    return _SECRET_KEY


def build_connect_url(provider: str, connection_id: str, workspace_id: str) -> str:
    """Return the Nango-hosted OAuth initiation URL for a provider."""
    sig = hmac.new(
        _secret_key().encode(),
        connection_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    worker_url = os.getenv("WORKER_BASE_URL", "").rstrip("/")
    success_url = f"{worker_url}/auth/nango/callback?provider={provider}&workspace_id={workspace_id}"
    error_url = f"{worker_url}/auth/nango/callback?provider={provider}&workspace_id={workspace_id}&error=oauth_failed"
    return (
        f"{_base_url()}/oauth/connect/{provider}"
        f"?connection_id={connection_id}"
        f"&hmac={sig}"
        f"&success_url={success_url}"
        f"&error_url={error_url}"
    )


async def get_token(connection_id: str, provider: str) -> str:
    """
    Fetch a valid access token from Nango for the given connection.
    Nango auto-refreshes the token if it is expired before returning.
    Raises RuntimeError if the connection is not found or the call fails.
    """
    url = f"{_base_url()}/connection/{connection_id}"
    headers = {"Authorization": f"Bearer {_secret_key()}"}
    params = {"provider_config_key": provider}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code == 404:
        raise RuntimeError(f"Nango connection not found: provider={provider} connection_id={connection_id}")
    resp.raise_for_status()

    data = resp.json()
    token = (data.get("credentials") or {}).get("access_token")
    if not token:
        raise RuntimeError(f"Nango returned no access_token for {provider}/{connection_id}")
    return token


async def delete_connection(connection_id: str, provider: str) -> None:
    """Remove a connection from Nango (called on source disconnect)."""
    url = f"{_base_url()}/connection/{connection_id}"
    headers = {"Authorization": f"Bearer {_secret_key()}"}
    params = {"provider_config_key": provider}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(url, headers=headers, params=params)

    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()
