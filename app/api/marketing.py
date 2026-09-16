"""Workspace-scoped marketing reads, rule settings and natural-language previews."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import Field, ValidationError

from app.api.security import get_user_id, verify_workspace_access
from app.api.platform import get_key_store
from app.primitives.database import DatabaseService
from app.primitives.marketing.rules import OpportunityRule, StrictModel, TargetRequest, describe_rule
from app.primitives.marketing.service import MarketingService
from app.primitives.marketing.store import PostgresMarketingStore
from app.primitives.marketing.targets import preview_target
from app.primitives.marketing.keyword_planner import PlannerImport, planner_snapshot, filter_planner_keywords
from app.primitives.marketing.rules import KeywordFilter
from typing import Literal

router = APIRouter(prefix="/marketing", tags=["marketing"])
logger = logging.getLogger(__name__)
TextQuery = Annotated[str, Query(min_length=1, max_length=200)]


async def resolve_marketing_scope(workspace_id: str | None = None,
                                  authorization: Annotated[str | None, Header()] = None,
                                  user_id: str = Depends(get_user_id),
                                  key_store=Depends(get_key_store)):
    """User sessions use explicit workspace IDs; API keys derive their workspace."""
    if authorization and authorization.startswith("Bearer poysis_live_"):
        identity = await key_store.authenticate(authorization[7:].strip(), "marketing:read")
        if identity is None:
            raise HTTPException(401, "Invalid, expired, revoked, or insufficiently scoped API key")
        if workspace_id and workspace_id != identity["workspace_id"]:
            raise HTTPException(403, "API key is not authorized for that workspace")
        return identity["workspace_id"]
    if not workspace_id:
        raise HTTPException(422, "workspace_id is required for user authentication")
    return await verify_workspace_access(workspace_id, user_id)


Scope = Annotated[str, Depends(resolve_marketing_scope)]


def get_store():
    return PostgresMarketingStore()


def get_target_interpreter():
    return preview_target


async def require_rule_editor(workspace_id: Scope, user_id: str = Depends(get_user_id)):
    workspace = await DatabaseService().get_workspace(workspace_id)
    if not workspace or workspace.get("user_id") != user_id:
        raise HTTPException(403, "Only the workspace owner can change marketing rules")
    return user_id


def page_response(rows, limit, offset):
    return {"data": rows[:limit], "limit": limit, "offset": offset,
            "next_offset": offset + limit if len(rows) > limit else None}


def check_window(start, end):
    if start > end or (end - start).days >= 366:
        raise HTTPException(422, "Date window must be ordered and contain at most 366 days")


@router.post("/keyword-planner/reports")
async def import_planner_report(request: PlannerImport, workspace_id: Scope,
                                user_id: str = Depends(require_rule_editor), store=Depends(get_store)):
    try:
        content, fingerprint = planner_snapshot(request)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid or incomplete Keyword Planner report; check report kind and metrics")
    saved = await store.save_planner_report(workspace_id, request, content, fingerprint, user_id)
    if not saved:
        raise HTTPException(409, "Snapshot ID already contains a different report; use a new snapshot ID")
    return {"snapshot_id": request.snapshot_id, "source": content["source"],
            "keyword_count": len(content["keywords"]), "captured_at": request.captured_at}


@router.get("/keyword-planner/reports")
async def list_planner_reports(workspace_id: Scope,
                               customer_id: str = Query(..., pattern=r"^[0-9]{10}$"),
                               dataset: str = Query(..., min_length=1, max_length=200),
                               limit: int = Query(100, ge=1, le=1000),
                               offset: int = Query(0, ge=0, le=1000000), store=Depends(get_store)):
    return page_response(await store.list_planner_reports(workspace_id, customer_id, dataset, limit, offset), limit, offset)


@router.get("/keyword-planner/reports/{snapshot_id}")
async def get_planner_keywords(snapshot_id: UUID, workspace_id: Scope,
                               include: list[str] = Query(default=[], max_length=100),
                               exclude: list[str] = Query(default=[], max_length=100),
                               match: Literal["exact", "contains"] = "contains",
                               min_monthly_searches: int | None = Query(None, ge=0, le=1000000000),
                               max_competition_index: int | None = Query(None, ge=0, le=100),
                               limit: int = Query(100, ge=1, le=1000),
                               offset: int = Query(0, ge=0, le=1000000), store=Depends(get_store)):
    try:
        keywords = KeywordFilter(mode=match, include=include, exclude=exclude)
    except ValueError:
        raise HTTPException(422, "Keyword filters must contain nonblank terms of at most 200 characters")
    report = await store.get_planner_report(workspace_id, snapshot_id)
    if report is None:
        raise HTTPException(404, "Keyword Planner report not found")
    rows = filter_planner_keywords(report["keywords"], keywords, min_monthly_searches, max_competition_index)
    return {**{k: v for k, v in report.items() if k != "keywords"},
            **page_response(rows[offset:offset + limit + 1], limit, offset),
            "snapshot_id": snapshot_id, "total": len(rows)}


@router.get("/data")
async def get_data(workspace_id: Scope, source: TextQuery, property_id: TextQuery,
                   dataset: TextQuery, start: date, end: date,
                   dimensions: str = Query("{}", max_length=4000),
                   limit: int = Query(100, ge=1, le=1000),
                   offset: int = Query(0, ge=0, le=1000000), store=Depends(get_store)):
    check_window(start, end)
    try:
        filters = json.loads(dimensions)
        if not isinstance(filters, dict) or not all(isinstance(v, str) for v in filters.values()):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "dimensions must be a JSON object of exact string filters")
    rows = await store.data(workspace_id, source, property_id, dataset, start, end, filters, limit, offset)
    return {**page_response(rows, limit, offset), "workspace_id": workspace_id,
            "source": source, "property_id": property_id, "dataset": dataset,
            "start": start, "end": end}


@router.get("/rules")
async def list_rules(workspace_id: Scope, limit: int = Query(100, ge=1, le=1000),
                     offset: int = Query(0, ge=0, le=1000000), store=Depends(get_store)):
    return page_response(await store.list_rules(workspace_id, limit, offset), limit, offset)


@router.post("/rules/preview")
async def interpret_target(request: TargetRequest, workspace_id: Scope,
                           interpret=Depends(get_target_interpreter)):
    try:
        return await interpret(request)
    except (ValueError, ValidationError):
        raise HTTPException(502, "Could not interpret target reliably; rephrase or use manual rule settings")
    except Exception:
        # Avoid including provider responses or target text in errors/logs.
        logger.warning("Marketing target interpreter unavailable")
        raise HTTPException(503, "Target interpreter unavailable; manual rule settings remain available")


class SaveRuleRequest(StrictModel):
    rule: OpportunityRule
    expected_revision: int = Field(ge=0, strict=True)


@router.put("/rules/{rule_id}")
async def save_rule(rule_id: UUID, request: SaveRuleRequest, workspace_id: Scope,
                    user_id: str = Depends(require_rule_editor), store=Depends(get_store)):
    row = await store.save_rule(workspace_id, rule_id, request.rule, user_id, request.expected_revision)
    if row is None:
        raise HTTPException(409, "Rule changed or no longer exists; reload before saving")
    return {**row, "summary": describe_rule(request.rule)}


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: UUID, workspace_id: Scope, store=Depends(get_store)):
    row = await store.get_rule(workspace_id, rule_id)
    if row is None:
        raise HTTPException(404, "Rule not found")
    return {**row, "summary": describe_rule(OpportunityRule.model_validate(row["config"]))}


@router.get("/opportunities")
async def opportunities(workspace_id: Scope, rule_id: UUID, end: date | None = None,
                        limit: int = Query(100, ge=1, le=1000),
                        offset: int = Query(0, ge=0, le=1000000), store=Depends(get_store)):
    record = await store.get_rule(workspace_id, rule_id)
    if record is None:
        raise HTTPException(404, "Rule not found")
    rule = OpportunityRule.model_validate(record["config"])
    # Exclude the current UTC day by default; report completeness is not inferred.
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    if end.toordinal() < rule.window_days:
        raise HTTPException(422, "Evaluation window falls outside supported dates")
    start = end - timedelta(days=rule.window_days - 1)
    try:
        results = await MarketingService(store).opportunities(
            workspace_id, rule.property_id, rule.dataset, start, end, rule=rule)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {
        **page_response(results[offset:offset + limit + 1], limit, offset),
        "total": len(results), "rule_id": rule_id, "revision": record["revision"],
        "enabled": rule.enabled, "start": start, "end": end,
        "coverage": "unverified", "summary": describe_rule(rule),
    }
