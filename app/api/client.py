"""Worker API for the frontend's app data (formerly Supabase/RLS).

The studio, developer, and marketplace apps call these routes with a Cognito
access token. Authenticated routes resolve the caller via `get_user_id` and are
scoped to that user; public routes serve only marketplace-safe fields.

Mounted at /client. Public routes are mounted at /client/public.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security import get_user_id, verify_workspace_access
from app.primitives.client_store import ClientDataStore

router = APIRouter(prefix="/client", tags=["client-data"])
public_router = APIRouter(prefix="/client/public", tags=["client-public"])

RESERVED_SLUGS = frozenset({"api"})


def get_store():
    return ClientDataStore()


def _slug_clean(slug: str) -> str:
    import re
    clean = re.sub(r"[^a-z0-9-]", "-", slug.lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean


# ── Workspace ────────────────────────────────────────────────────────────────

@router.get("/workspace")
async def get_workspace(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    ws = await store.get_workspace_id(user_id)
    return {"workspace_id": ws}


@router.post("/workspace")
async def ensure_workspace(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    return {"workspace_id": await store.ensure_workspace(user_id)}


# ── Profile ──────────────────────────────────────────────────────────────────

@router.get("/profile")
async def get_profile(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    profile = await store.get_profile(user_id)
    if profile is None:
        profile = await store.ensure_profile(user_id)
    return {"profile": profile}


class UiModeRequest(BaseModel):
    mode: Literal["creator", "enterprise"]


@router.put("/profile/ui-mode")
async def set_ui_mode(request: UiModeRequest, user_id: str = Depends(get_user_id),
                      store=Depends(get_store)):
    # Enterprise is a paid tier — gate it on the profile entitlement so a
    # non-entitled user cannot self-select it (mirrors the old server action).
    if request.mode == "enterprise":
        profile = await store.get_profile(user_id)
        if not (profile and profile.get("enterprise")) and not (profile and profile.get("is_admin")):
            raise HTTPException(403, "Enterprise isn't available on your plan.")
    await store.ensure_profile(user_id)
    await store.set_ui_mode(user_id, request.mode)
    return {"ui_mode": request.mode}


# ── Notebooks (owner-scoped) ─────────────────────────────────────────────────

class NotebookSaveRequest(BaseModel):
    config: dict = Field(default_factory=dict)


class NotebookCreateRequest(BaseModel):
    name: str = Field(default="Untitled Notebook", max_length=200)
    config: dict = Field(default_factory=dict)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SlugRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)


@router.get("/notebooks")
async def list_notebooks(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    return {"data": await store.list_notebooks(user_id)}


@router.post("/notebooks", status_code=201)
async def create_notebook(request: NotebookCreateRequest, user_id: str = Depends(get_user_id),
                          store=Depends(get_store)):
    workspace_id = await store.ensure_workspace(user_id)
    row = await store.create_notebook(user_id, workspace_id, request.name, request.config)
    if row is None:
        raise HTTPException(500, "Could not create notebook")
    return row


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: UUID, user_id: str = Depends(get_user_id),
                       store=Depends(get_store)):
    row = await store.get_notebook(user_id, notebook_id)
    if row is None:
        raise HTTPException(404, "Notebook not found")
    return row


@router.put("/notebooks/{notebook_id}")
async def save_notebook(notebook_id: UUID, request: NotebookSaveRequest,
                        user_id: str = Depends(get_user_id), store=Depends(get_store)):
    config = request.config
    name = config.pop("name", None) if isinstance(config, dict) else None
    # Data-loss backstop: never let a blockless config overwrite a notebook
    # that currently has blocks (the signature of an autosave firing from a
    # reset store). Mirrors the old server action.
    incoming_has_blocks = bool(config.get("activeBlocks")) if isinstance(config, dict) else False
    if not incoming_has_blocks:
        existing = await store.get_notebook(user_id, notebook_id)
        existing_blocks = existing and (existing.get("config") or {}).get("activeBlocks")
        if isinstance(existing_blocks, list) and existing_blocks:
            raise HTTPException(409, "Refused to overwrite existing notebook content with an empty config.")
    row = await store.save_notebook(user_id, notebook_id, name or "Untitled Notebook", config)
    if row is None:
        raise HTTPException(404, "Notebook not found")
    return row


@router.patch("/notebooks/{notebook_id}")
async def rename_notebook(notebook_id: UUID, request: RenameRequest,
                          user_id: str = Depends(get_user_id), store=Depends(get_store)):
    row = await store.rename_notebook(user_id, notebook_id, request.name)
    if row is None:
        raise HTTPException(404, "Notebook not found")
    return row


@router.put("/notebooks/{notebook_id}/slug")
async def set_slug(notebook_id: UUID, request: SlugRequest,
                   user_id: str = Depends(get_user_id), store=Depends(get_store)):
    clean = _slug_clean(request.slug)
    if not clean:
        raise HTTPException(422, "Slug cannot be empty.")
    if clean in RESERVED_SLUGS:
        raise HTTPException(422, "That link is reserved. Try another.")
    result = await store.set_slug(user_id, notebook_id, clean)
    if result == "taken":
        raise HTTPException(409, "That slug is already taken. Try another.")
    if result is not True:
        raise HTTPException(404, "Notebook not found")
    return {"slug": clean}


@router.delete("/notebooks/{notebook_id}/slug")
async def clear_slug(notebook_id: UUID, user_id: str = Depends(get_user_id),
                     store=Depends(get_store)):
    await store.clear_slug(user_id, notebook_id)
    return {"slug": None}


@router.delete("/notebooks/{notebook_id}")
async def delete_notebook(notebook_id: UUID, user_id: str = Depends(get_user_id),
                          store=Depends(get_store)):
    count = await store.delete_notebook(user_id, notebook_id)
    if count == 0:
        raise HTTPException(404, "Notebook not found")
    return {"deleted": count}


# ── Cluster ceilings (owner-scoped) ──────────────────────────────────────────
class CeilingRequest(BaseModel):
    ceiling: Literal["private", "team", "public"]


@router.get("/ceilings")
async def list_ceilings(user_id: str = Depends(get_user_id), store=Depends(get_store)):
    return {"data": await store.list_ceilings(user_id)}


@router.put("/ceilings/{topic_id}")
async def upsert_ceiling(topic_id: str, request: CeilingRequest,
                         user_id: str = Depends(get_user_id), store=Depends(get_store)):
    await store.upsert_ceiling(topic_id, user_id, request.ceiling)
    return {"topic_id": topic_id, "ceiling": request.ceiling}


# ── Consolidation jobs (live status bars) ────────────────────────────────────

@router.get("/jobs")
async def list_jobs(workspace_id: str, user_id: str = Depends(get_user_id),
                    store=Depends(get_store)):
    await verify_workspace_access(workspace_id, user_id)
    return {"data": await store.list_jobs(workspace_id)}


# ── Topic overrides (locked topics) ──────────────────────────────────────────

class TopicOverrideRequest(BaseModel):
    locked: bool


@router.get("/topic-overrides")
async def list_topic_overrides(workspace_id: str, user_id: str = Depends(get_user_id),
                               store=Depends(get_store)):
    await verify_workspace_access(workspace_id, user_id)
    return {"data": await store.list_topic_overrides(workspace_id)}


@router.put("/topic-overrides/{topic_id}")
async def upsert_topic_override(topic_id: str, workspace_id: str,
                                request: TopicOverrideRequest,
                                user_id: str = Depends(get_user_id),
                                store=Depends(get_store)):
    await verify_workspace_access(workspace_id, user_id)
    await store.upsert_topic_override(workspace_id, topic_id, user_id, request.locked)
    return {"ok": True}


# ── Public reads (no auth — marketplace-safe fields only) ────────────────────

@public_router.get("/notebooks/{notebook_id}")
async def public_notebook(notebook_id: UUID, store=Depends(get_store)):
    row = await store.get_public_notebook(notebook_id)
    if row is None:
        raise HTTPException(404, "Notebook not found")
    return row


@public_router.get("/notebooks/by-slug/{slug}")
async def public_notebook_by_slug(slug: str, store=Depends(get_store)):
    row = await store.get_public_notebook_by_slug(slug)
    if row is None:
        raise HTTPException(404, "Notebook not found")
    return row


@public_router.get("/directory")
async def public_directory(store=Depends(get_store)):
    return {"data": await store.list_public_directory()}


@public_router.get("/ceilings")
async def public_ceilings(owner_user_id: str,
                          topic_id: list[str] | None = Query(default=None),
                          store=Depends(get_store)):
    """Public topics an owner has marked public. topic_id narrows the result."""
    return {"data": await store.list_public_ceilings(owner_user_id, topic_id)}
