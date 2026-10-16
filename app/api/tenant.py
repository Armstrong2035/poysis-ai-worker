"""Tenant (organization) API for the frontend.

Self-serve: the user's personal tenant is provisioned on first access, and its
branding/workspaces/members are read and updated here. All routes resolve the
caller from the Cognito token and are scoped to that caller's tenant.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.security import get_user_id
from app.primitives.database import DatabaseService
from app.primitives.platform.api_keys import APIKeyStore
from app.primitives.tenant_store import TenantStore

router = APIRouter(prefix="/client/tenant", tags=["tenant"])


def get_store():
    return TenantStore()


def get_key_store():
    return APIKeyStore()


class BrandingRequest(BaseModel):
    branding: dict = Field(default_factory=dict)


async def require_tenant_manager(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    """Resolve the caller's tenant and require an owner/admin role for writes."""
    tenant = await store.get_tenant_for_user(user_id)
    if tenant is None:
        raise HTTPException(404, "No organization found for this account")
    role = await store.is_member(tenant["id"], user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(403, "Only an organization owner or admin can do that")
    return user_id, tenant


@router.get("")
async def get_tenant(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    tenant = await store.get_tenant_for_user(user_id)
    if tenant is None:
        tenant = await store.ensure_personal_tenant(user_id)
    role = await store.is_member(tenant["id"], user_id)
    return {"tenant": tenant, "role": role}


@router.put("/branding")
async def update_branding(request: BrandingRequest,
                          manager=Depends(require_tenant_manager),
                          store=Depends(get_store)):
    user_id, tenant = manager
    updated = await store.update_branding(tenant["id"], request.branding)
    if updated is None:
        raise HTTPException(404, "Organization not found")
    return {"tenant": updated}


@router.get("/workspaces")
async def list_workspaces(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    tenant = await store.get_tenant_for_user(user_id)
    if tenant is None:
        return {"data": []}
    return {"data": await store.list_workspaces(tenant["id"])}


@router.get("/members")
async def list_members(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    tenant = await store.get_tenant_for_user(user_id)
    if tenant is None:
        return {"data": []}
    return {"data": await store.list_members(tenant["id"])}


@router.get("/connectors")
async def list_connectors(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    """MCP connectors across the tenant's workspaces (for a future registry)."""
    tenant = await store.get_tenant_for_user(user_id)
    if tenant is None:
        return {"data": []}
    return {"data": await DatabaseService().list_mcp_connectors(tenant["id"])}


async def require_tenant_key(authorization: Annotated[str | None, Header()] = None,
                             store=Depends(get_key_store)):
    """Authenticate a server-side tenant key with member-write scope."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing tenant key")
    identity = await store.authenticate(authorization[7:].strip(), "tenant:member:write")
    if identity is None:
        raise HTTPException(401, "Invalid, expired, revoked, or insufficiently scoped tenant key")
    return identity


class JoinRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    tenant_id: str | None = None


@router.post("/join")
async def join_tenant(request: JoinRequest, identity=Depends(require_tenant_key),
                      store=Depends(get_store)):
    """Add a user to the key's tenant (auto-join for a client app)."""
    tenant_id = request.tenant_id or identity["tenant_id"]
    if request.tenant_id and str(request.tenant_id) != str(identity["tenant_id"]):
        raise HTTPException(403, "Tenant key does not match the requested tenant")
    await store.add_member(tenant_id, request.user_id, "member")
    return {"ok": True, "tenant_id": str(tenant_id)}
