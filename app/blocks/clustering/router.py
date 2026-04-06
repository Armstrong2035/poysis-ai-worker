from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.primitives.knowledge.engine import KnowledgeEngine

router = APIRouter(tags=["clustering"])

class ClusterRequest(BaseModel):
    documents: List[Dict[str, Any]]  # Raw docs: [{"id": "...", "text": "..."}]
    workspace_id: str
    similarity_threshold: Optional[float] = 0.80  # How similar docs must be to be "the same"

@router.post("/cluster")
async def cluster_documents(request: ClusterRequest):
    """
    Clustering Block: Groups semantically similar documents into "Concept Buckets."
    Policy: For each doc, fetch its nearest neighbours. If score >= threshold, group together.
    Use case: De-duplicate support tickets, group product reviews into themes.
    """
    try:
        engine = KnowledgeEngine()

        # First, upsert all documents so they are in the knowledge base
        await engine.upsert_documents(request.workspace_id, request.documents)

        # Then, find clusters by cross-comparing each document
        clusters = []
        assigned = set()

        for doc in request.documents:
            doc_id = str(doc.get("id") or doc.get("source_id"))
            text = doc.get("text") or doc.get("content")

            if doc_id in assigned or not text:
                continue

            # This doc becomes the "seed" of a new cluster
            raw_results = await engine.fetch_raw(
                workspace_id=request.workspace_id,
                text=text,
                top_k=len(request.documents)
            )

            # Gather all docs above the threshold into this cluster
            cluster_members = []
            for r in raw_results:
                member_id = r["id"]
                if r.get("score", 0) >= request.similarity_threshold and member_id not in assigned:
                    cluster_members.append({
                        "id": member_id,
                        "score": round(r["score"], 4),
                        "text": r.get("metadata", {}).get("text")
                    })
                    assigned.add(member_id)

            if cluster_members:
                clusters.append({
                    "seed_id": doc_id,
                    "seed_text": text[:100],
                    "size": len(cluster_members),
                    "members": cluster_members
                })

        return {"clusters": clusters, "total_clusters": len(clusters), "total_docs": len(request.documents)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
