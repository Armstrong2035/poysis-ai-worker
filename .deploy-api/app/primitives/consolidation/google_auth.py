import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode
import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def build_auth_url(state: str) -> str:
    """Build the Google OAuth consent screen URL."""
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI"),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # required to get refresh token
        "prompt": "consent",        # force refresh token on every auth
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI"),
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        data = resp.json()

    expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expiry": expiry.isoformat(),
    }


async def fetch_google_email(access_token: str) -> str:
    """Look up the email of the Google account that authorized the token."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("email", "unknown@google.com")


async def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()

    expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
    return {
        "access_token": data["access_token"],
        "expiry": expiry.isoformat(),
    }


async def get_valid_token(workspace_id: str, db, user_id: str = None) -> Optional[str]:
    """
    Returns a valid access token for a workspace and user.
    Automatically refreshes if expired.
    """
    tokens = await db.get_google_tokens(workspace_id, user_id)
    if not tokens:
        return None

    expiry = datetime.fromisoformat(tokens["google_token_expiry"])
    is_expired = datetime.now(timezone.utc) >= expiry - timedelta(minutes=5)

    if is_expired and tokens.get("google_refresh_token"):
        refreshed = await refresh_access_token(tokens["google_refresh_token"])
        await db.save_google_tokens(
            workspace_id=workspace_id,
            user_id=user_id,
            access_token=refreshed["access_token"],
            refresh_token=tokens["google_refresh_token"],
            expiry=refreshed["expiry"],
        )
        return refreshed["access_token"]

    return tokens.get("google_access_token")
