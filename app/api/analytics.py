import os
import asyncio
from fastapi import APIRouter, HTTPException, Query, Header, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.primitives.database import DatabaseService

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/dashboard")
async def get_analytics_dashboard(
    workspace_id: str = Query(..., description="The Workspace ID"),
    days: int = Query(30, description="Number of days to look back"),
    # In a real production app, we would verify a session token or JWT here
    # to ensure the requester actually owns `workspace_id`.
):
    """
    Returns aggregated analytics for the workspace dashboard.
    Powered by a highly performant Supabase RPC that crunches all metrics in one pass.
    """
    try:
        db = DatabaseService()
        
        # Verify workspace exists
        workspace = await db.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found or unauthorized.")
            
        data = await db.get_dashboard_analytics(workspace_id=workspace_id, days=days)
        
        if not data:
            # Prevent nulls on fresh accounts
            data = {
                "overview": {
                    "total_searches": 0,
                    "cart_rate_percent": 0,
                    "checkout_rate_percent": 0
                },
                "trending": [],
                "missed_opportunities": [],
                "top_products": []
            }
            
        return data

    except Exception as e:
        print(f"[ANALYTICS ERROR] {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch analytics dashboard data")

@router.get("/recent-searches")
async def get_recent_searches_feed(
    workspace_id: str = Query(..., description="The Workspace ID"),
    limit: int = Query(50, description="Max number of searches to return")
):
    """
    Returns a live feed of the most recent searches for the given shop.
    Returns: list of dicts with id, query, result_count, created_at, latency_ms.
    """
    try:
        db = DatabaseService()
        
        # Verify workspace
        workspace = await db.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found or unauthorized.")
            
        recent = await db.get_recent_searches(workspace_id=workspace_id, limit=limit)
        return {"recent_searches": recent}
        
    except Exception as e:
        print(f"[ANALYTICS ERROR] {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch recent searches feed")

@router.get("/logs")
async def get_raw_logs(
    workspace_id: str = Query(..., description="The Workspace ID"),
    start_date: Optional[str] = Query(None, description="ISO8601 start date (e.g. 2026-03-01T00:00:00Z)"),
    end_date: Optional[str] = Query(None, description="ISO8601 end date (e.g. 2026-03-31T23:59:59Z)"),
    limit: int = Query(100, description="Max records to return (max 1000)", le=1000),
    offset: int = Query(0, description="Pagination offset")
):
    """
    Returns the raw search logs and joined attribution events for the requested timeframe.
    Provides BI teams and developers the deep data needed to build custom reports.
    """
    try:
        db = DatabaseService()
        
        # Verify workspace
        workspace = await db.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found or unauthorized.")
            
        logs = await db.get_raw_logs(
            workspace_id=workspace_id, 
            start_date=start_date, 
            end_date=end_date, 
            limit=limit, 
            offset=offset
        )
        
        return {
            "meta": {
                "workspace_id": workspace_id,
                "limit": limit,
                "offset": offset,
                "count": len(logs)
            },
            "data": logs
        }
        
    except Exception as e:
        print(f"[ANALYTICS ERROR] {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch raw analytics logs")
