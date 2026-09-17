"""Dashboard key management and external API-key authentication."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.security import get_user_id, verify_workspace_access
from app.primitives.database import DatabaseService
from app.primitives.platform.api_keys import ALLOWED_SCOPES, APIKeyStore

router = APIRouter(prefix="/platform", tags=["developer-platform"])
public_router = APIRouter(prefix="/v1", tags=["public-api"])


def get_key_store():
    return APIKeyStore()


async def require_owner(workspace_id: Annotated[str, Depends(verify_workspace_access)], user_id: str = Depends(get_user_id)):
    workspace = await DatabaseService().get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(403, "Only the workspace owner can manage API access")
    return workspace_id, user_id


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)


class KeyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["marketing:read"], min_length=1, max_length=10)
    expires_at: datetime | None = None


@router.get("/client")
async def client(owner=Depends(require_owner), store=Depends(get_key_store)):
    row = await store.get_client(owner[0])
    return {"client": row}


@router.put("/client")
async def save_client(request: ClientRequest, owner=Depends(require_owner), store=Depends(get_key_store)):
    return {"client": await store.ensure_client(owner[0], request.name.strip(), owner[1])}


@router.get("/api-keys")
async def keys(owner=Depends(require_owner), store=Depends(get_key_store)):
    return {"data": await store.list_keys(owner[0])}


@router.post("/api-keys", status_code=201)
async def create_key(request: KeyRequest, owner=Depends(require_owner), store=Depends(get_key_store)):
    scopes = list(dict.fromkeys(request.scopes))
    if not set(scopes) <= ALLOWED_SCOPES:
        raise HTTPException(422, "One or more API key scopes are unsupported")
    if request.expires_at:
        if request.expires_at.tzinfo is None or request.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "API key expiry must be a timezone-aware future timestamp")
    client = await store.get_client(owner[0])
    if client is None:
        client = await store.ensure_client(owner[0], "Poysis API", owner[1])
    return await store.create_key(owner[0], client["client_id"], request.name.strip(), scopes, request.expires_at, owner[1])


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_key(key_id: UUID, owner=Depends(require_owner), store=Depends(get_key_store)):
    if not await store.revoke_key(owner[0], key_id):
        raise HTTPException(404, "Active API key not found")


@router.get("/usage")
async def usage(days: int = Query(30, ge=1, le=366), owner=Depends(require_owner), store=Depends(get_key_store)):
    return {"days": days, "data": await store.usage(owner[0], days)}


async def require_marketing_api_key(authorization: Annotated[str | None, Header()] = None,
                                    store=Depends(get_key_store)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Poysis API key")
    identity = await store.authenticate(authorization[7:].strip(), "marketing:read")
    if identity is None:
        raise HTTPException(401, "Invalid, expired, revoked, or insufficiently scoped API key")
    return identity


@public_router.get("/marketing/connection")
async def marketing_connection(identity=Depends(require_marketing_api_key)):
    return {"connected": True, "client_id": identity["client_id"],
            "client_name": identity["client_name"], "workspace_id": identity["workspace_id"],
            "scopes": identity["scopes"]}
