from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from app.primitives.database import DatabaseService
import uuid

router = APIRouter(tags=["tracking"])

class ClickEvent(BaseModel):
    search_id: str
    workspace_id: str
    product_id: str
    position_clicked: Optional[int] = None

class CartEvent(BaseModel):
    search_id: str
    workspace_id: str
    product_id: str

class PurchaseEvent(BaseModel):
    search_id: str
    workspace_id: str
    product_id: str
    revenue_value: float

@router.post("/track/click")
async def track_click(event: ClickEvent, background_tasks: BackgroundTasks):
    """Called by the client overlay when a user clicks a product."""
    db = DatabaseService()
    event_data = {
        "id": str(uuid.uuid4()),
        "search_id": event.search_id,
        "workspace_id": event.workspace_id,
        "product_id": event.product_id,
        "event_type": "click",
        "position_clicked": event.position_clicked
    }
    background_tasks.add_task(db.log_attribution_event, event_data)
    return {"status": "ok"}

@router.post("/track/cart")
async def track_cart(event: CartEvent, background_tasks: BackgroundTasks):
    """Called by the embed script when a Shopify Ajax cart event fires."""
    db = DatabaseService()
    event_data = {
        "id": str(uuid.uuid4()),
        "search_id": event.search_id,
        "workspace_id": event.workspace_id,
        "product_id": event.product_id,
        "event_type": "add_to_cart"
    }
    background_tasks.add_task(db.log_attribution_event, event_data)
    return {"status": "ok"}

@router.post("/track/purchase")
async def track_purchase(event: PurchaseEvent, background_tasks: BackgroundTasks):
    """Called by a webhook or server-to-server integration after checkout."""
    db = DatabaseService()
    event_data = {
        "id": str(uuid.uuid4()),
        "search_id": event.search_id,
        "workspace_id": event.workspace_id,
        "product_id": event.product_id,
        "event_type": "purchase",
        "revenue_value": event.revenue_value
    }
    background_tasks.add_task(db.log_attribution_event, event_data)
    return {"status": "ok"}
