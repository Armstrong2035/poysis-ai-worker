from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.primitives.knowledge.engine import KnowledgeEngine

router = APIRouter(tags=["categorization"])

class ClassifyRequest(BaseModel):
    text: str
    workspace_id: str
    labels: Dict[str, str]       # e.g. {"billing": "Queries about invoices...", "support": "Technical issues..."}
    threshold: Optional[float] = 0.65  # Min confidence to assign a label

@router.post("/classify")
async def classify_intent(request: ClassifyRequest):
    """
    Categorization Block: Maps free-form text to a user-defined label.
    Policy: Embeds each label description, finds closest match, returns label if score >= threshold.
    No if/else. No keywords. Pure semantic proximity.
    """
    try:
        engine = KnowledgeEngine()
        best_label = None
        best_score = 0.0
        scores = {}

        for label, description in request.labels.items():
            # Get raw similarity between input text and each label's description
            # We embed label descriptions on-the-fly for comparison
            label_vector = await engine.embedder.get_embedding(description, task_type="retrieval_document")
            input_vector = await engine.embedder.get_embedding(request.text, task_type="retrieval_query")

            # Cosine similarity
            dot = sum(a * b for a, b in zip(input_vector, label_vector))
            norm_a = sum(a ** 2 for a in input_vector) ** 0.5
            norm_b = sum(b ** 2 for b in label_vector) ** 0.5
            score = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

            scores[label] = round(score, 4)
            if score > best_score:
                best_score = score
                best_label = label

        met_threshold = best_score >= request.threshold

        return {
            "label": best_label if met_threshold else None,
            "confidence": best_score,
            "met_threshold": met_threshold,
            "all_scores": scores
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
