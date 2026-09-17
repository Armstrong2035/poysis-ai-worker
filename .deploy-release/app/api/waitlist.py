from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.primitives.database import DatabaseService

router = APIRouter(tags=["waitlist"])


class WaitlistSignup(BaseModel):
    email: EmailStr
    source: Optional[str] = None


@router.post("/waitlist")
async def join_waitlist(signup: WaitlistSignup):
    db = DatabaseService()
    email = signup.email.lower().strip()
    ok = await db.add_waitlist_email(email, signup.source)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save waitlist signup")
    return {"status": "ok"}
