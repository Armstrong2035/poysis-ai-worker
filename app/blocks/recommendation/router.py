from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.primitives.knowledge.engine import KnowledgeEngine

router = APIRouter(tags=["recommendation"])

class RecommendRequest(BaseModel):
    reference_text: str         # The item/content we want to find similar things for
    workspace_id: str
    limit: Optional[int] = 5
    min_score: Optional[float] = 0.55
    exclude_self: Optional[bool] = True  # Exclude results too similar to the reference itself

@router.post("/recommend")
async def recommend(request: RecommendRequest):
    """
    Recommendation Block: Finds semantically similar items to a reference text.
    Policy: Fetch extra candidates, exclude the reference itself, return top N.
    Use case: "Users who read X also liked..." or "Similar products."
    """
    try:
        engine = KnowledgeEngine()

        # Fetch more than needed so we can filter
        raw_results = await engine.fetch_raw(
            workspace_id=request.workspace_id,
            text=request.reference_text,
            top_k=(request.limit * 3) + 1
        )

        # Apply recommendation policy
        recommendations = []
        for r in raw_results:
            score = r.get("score", 0)
            if score < request.min_score:
                continue

            # Exclude results that are essentially the same content (score too close to 1.0)
            if request.exclude_self and score > 0.97:
                continue

            recommendations.append({
                "id": r["id"],
                "score": round(score, 4),
                "text": r.get("metadata", {}).get("text"),
                "metadata": r.get("metadata", {})
            })

            if len(recommendations) >= request.limit:
                break

        return {"recommendations": recommendations, "count": len(recommendations)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
