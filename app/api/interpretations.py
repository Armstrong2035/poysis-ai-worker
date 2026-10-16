"""Authenticated Poysis gateway for the independent interpretation service."""

from typing import Annotated
from uuid import UUID
import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.security import verify_workspace_access
from hermeneutics.deepseek import DeepSeekError
from hermeneutics.models import InterpretationRequest, Scope
from hermeneutics.repository import ConflictError
from hermeneutics.runtime import build_engine

router = APIRouter(prefix="/interpretations", tags=["interpretations"])
Workspace = Annotated[str, Depends(verify_workspace_access)]


def get_engine():
    try:
        return build_engine()
    except DeepSeekError:
        raise HTTPException(503, "Interpretation model is not configured") from None


@router.get("/datasets")
async def datasets(workspace_id: Workspace):
    from app.primitives.database import _conn
    from psycopg2.extras import RealDictCursor
    from hermeneutics.adapters import ADDITIVE
    def read():
        with _conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""SELECT source, property_id, dataset,
                    array_agg(DISTINCT m.metric_key) AS metrics, min(day) AS start, max(day) AS end
                    FROM marketing_observations
                    CROSS JOIN LATERAL jsonb_object_keys(marketing_observations.metrics) AS m(metric_key)
                    WHERE workspace_id=%s AND source IN ('ga4', 'search_console')
                    GROUP BY source, property_id, dataset ORDER BY source, property_id, dataset""", (workspace_id,))
                rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["metrics"] = sorted(set(row["metrics"]) & ADDITIVE[row["source"]])
        return {"data": [row for row in rows if row["metrics"]]}
    return await asyncio.to_thread(read)


@router.post("", status_code=202)
async def submit(request: InterpretationRequest, workspace_id: Workspace,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
                 engine=Depends(get_engine)):
    try:
        return await engine.submit(Scope(client_id="poysis", workspace_id=workspace_id), request, idempotency_key)
    except ConflictError:
        raise HTTPException(409, "Idempotency key already identifies a different request") from None
    except (ValueError, OverflowError):
        raise HTTPException(422, "Invalid interpretation frame") from None


@router.get("/{interpretation_id}")
async def status(interpretation_id: UUID, workspace_id: Workspace, engine=Depends(get_engine)):
    result = await engine.get(Scope(client_id="poysis", workspace_id=workspace_id), str(interpretation_id))
    if result is None:
        raise HTTPException(404, "Interpretation not found")
    return result


@router.get("/{interpretation_id}/evidence")
async def evidence(interpretation_id: UUID, workspace_id: Workspace, engine=Depends(get_engine)):
    result = await engine.evidence(Scope(client_id="poysis", workspace_id=workspace_id), str(interpretation_id))
    if result is None:
        raise HTTPException(404, "Interpretation not found")
    return result
