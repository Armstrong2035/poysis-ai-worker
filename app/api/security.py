"""Security utilities for user validation and authorization."""

import logging
import os
from fastapi import Depends, HTTPException, Header
from typing import Optional
from app.primitives.database import DatabaseService

db = DatabaseService()
logger = logging.getLogger(__name__)

# --- Token verification -----------------------------------------------------
# The caller sends "Authorization: Bearer <jwt>". We verify the signature and
# read the subject claim, so the identity can no longer be invented by the
# caller. Two signing schemes are supported because Supabase uses both:
#
#   AUTH_JWT_SECRET  - symmetric HS256. Supabase's classic project JWT secret.
#   AUTH_JWKS_URL    - asymmetric RS256/ES256 against a JWKS endpoint. Supabase's
#                      newer signing keys, and also Cognito. For Cognito the URL
#                      is https://cognito-idp.<region>.amazonaws.com/<pool>/.well-known/jwks.json
#
# Set AUTH_ISSUER and AUTH_AUDIENCE when the provider issues them; each is
# checked only when set. For Cognito, AUTH_AUDIENCE is the app client id and
# AUTH_ISSUER is https://cognito-idp.<region>.amazonaws.com/<pool-id>.
_JWT_SECRET = os.getenv("AUTH_JWT_SECRET")
_JWKS_URL = os.getenv("AUTH_JWKS_URL")
_ISSUER = os.getenv("AUTH_ISSUER")
_AUDIENCE = os.getenv("AUTH_AUDIENCE")

# While true, a request with no bearer token falls back to the old unverified
# X-User-ID header, and the fallback is logged. Set AUTH_ALLOW_HEADER_FALLBACK
# to "false" once the clients all send tokens. See AUTH_PLAN.md phase 4.
_ALLOW_HEADER_FALLBACK = os.getenv("AUTH_ALLOW_HEADER_FALLBACK", "true").lower() != "false"

_jwk_client = None


def _get_jwk_client():
    """Build the JWKS client once. It caches the keys and refetches on rotation."""
    global _jwk_client
    if _jwk_client is None and _JWKS_URL:
        from jwt import PyJWKClient
        _jwk_client = PyJWKClient(_JWKS_URL, cache_keys=True)
    return _jwk_client


def _verify_bearer(token: str) -> str:
    """Verify the token and return its subject. Raises 401 on any failure.

    Supports legacy Supabase HS256 JWTs and Cognito RS256/ES256 JWTs. Cognito
    access tokens carry `client_id` (not `aud`), so the audience is checked
    manually below against either claim.
    """
    import jwt

    options = {"verify_aud": False}
    try:
        if _JWT_SECRET:
            claims = jwt.decode(
                token,
                _JWT_SECRET,
                algorithms=["HS256"],
                issuer=_ISSUER,
                options=options,
            )
        elif _JWKS_URL:
            signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                issuer=_ISSUER,
                options=options,
            )
        else:
            # No verification material configured. Refuse rather than trust the
            # token, otherwise a bearer token would be weaker than the header.
            logger.error("[AUTH] bearer token sent but neither AUTH_JWT_SECRET nor AUTH_JWKS_URL is set")
            raise HTTPException(status_code=401, detail="Token authentication is not configured")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[AUTH] token rejected: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Audience check. Cognito access tokens use `client_id`; legacy Supabase and
    # Cognito ID tokens use `aud`. Accept either when AUTH_AUDIENCE is set.
    if _AUDIENCE:
        aud = claims.get("aud")
        aud_match = (
            aud == _AUDIENCE
            if isinstance(aud, str)
            else (isinstance(aud, list) and _AUDIENCE in aud)
        )
        if not aud_match and claims.get("client_id") != _AUDIENCE:
            raise HTTPException(status_code=401, detail="Token audience does not match")

    # A Cognito access token must be used as an access token, never an ID token.
    token_use = claims.get("token_use")
    if token_use and token_use != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Token has no subject claim")
    return str(subject)


async def get_user_id(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
) -> str:
    """
    Resolve the caller's user id.

    A verified bearer token wins. The unverified X-User-ID header is accepted
    only while AUTH_ALLOW_HEADER_FALLBACK is on, and every such request is
    logged so you can tell when it is safe to turn the fallback off.
    """
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        # External API keys are fully verified by the scoped route dependency.
        # Returning an inert principal here lets FastAPI reach that dependency;
        # ordinary workspace checks grant this principal no access.
        if token.startswith("poysis_live_"):
            return "__poysis_api_key__"
        return _verify_bearer(token)

    if x_user_id:
        if not _ALLOW_HEADER_FALLBACK:
            raise HTTPException(
                status_code=401,
                detail="X-User-ID is no longer accepted. Send Authorization: Bearer <token>.",
            )
        logger.warning("[AUTH] unverified X-User-ID fallback used")
        return x_user_id

    raise HTTPException(
        status_code=401,
        detail="Missing credentials. Send Authorization: Bearer <token>.",
    )


async def verify_workspace_access(
    workspace_id: str,
    user_id: str = Depends(get_user_id)
) -> str:
    """
    Verify that the user has access to the specified workspace.
    Checks workspace_members table; falls back to workspace owner check for backward compatibility.
    Also enforces the tenant boundary when the workspace belongs to a tenant.
    Returns the workspace_id if valid, raises 403 if not.
    """
    if not db.client:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        workspace = await db.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Check workspace_members table (new multi-user architecture)
        has_access = await db.has_workspace_access(workspace_id, user_id)
        if not has_access:
            # Fallback: Check if user owns the workspace (legacy single-user architecture)
            workspace_user = workspace.get("user_id")
            if workspace_user and workspace_user == user_id:
                # Auto-add owner to members table for future consistency
                await db.add_workspace_member(workspace_id, user_id, role="owner")
            else:
                raise HTTPException(status_code=403, detail="You do not have access to this workspace")

        # Tenant boundary: when the workspace belongs to a tenant, the caller
        # must also be a member of that tenant. Skipped while tenant_id is NULL
        # (tenants are provisioned lazily during the migration).
        tenant_id = workspace.get("tenant_id")
        if tenant_id:
            await verify_tenant_access(str(tenant_id), user_id)

        return workspace_id

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SECURITY] Error verifying workspace access: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify workspace")


async def verify_tenant_access(tenant_id: str, user_id: str) -> str:
    """Verify the user is a member of the tenant. Returns tenant_id or raises 403."""
    from app.primitives.tenant_store import TenantStore

    try:
        role = await TenantStore().is_member(tenant_id, user_id)
        if not role:
            raise HTTPException(status_code=403, detail="You do not have access to this organization")
        return tenant_id
    except HTTPException:
        raise
    except Exception as e:
        print(f"[SECURITY] Error verifying tenant access: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify organization")


# Keep the old name for backward compatibility
verify_workspace_ownership = verify_workspace_access
