import asyncio
from typing import Dict, Any, Optional

from app.primitives.knowledge.vector_store import VectorService
from app.primitives.knowledge.bertopic_handler import BertopicHandler
from app.primitives.database import DatabaseService

MIN_VECTORS_TO_CLUSTER = 30


class ClusteringEngine:
    def __init__(self, db: Optional[DatabaseService] = None):
        self.vector_service = VectorService()
        self.db = db

    def _namespace(self, workspace_id: str) -> str:
        return f"consolidation_{workspace_id}"

    async def run_clustering(self, workspace_id: str) -> Dict[str, Any]:
        namespace = self._namespace(workspace_id)

        print(f"[Clustering] Fetching vectors for namespace '{namespace}'...")
        vectors = await asyncio.to_thread(self.vector_service.fetch_all_vectors, namespace)

        if len(vectors) < MIN_VECTORS_TO_CLUSTER:
            print(f"[Clustering] Only {len(vectors)} vectors — skipping (need {MIN_VECTORS_TO_CLUSTER}+)")
            return {"status": "skipped", "reason": f"Too few vectors ({len(vectors)})", "workspace_id": workspace_id}

        texts = [v["metadata"].get("_text", "") for v in vectors]
        embeddings = [v["embedding"] for v in vectors]

        print(f"[Clustering] Running BERTopic on {len(vectors)} vectors...")
        handler = BertopicHandler(min_topic_size=10)
        topics, probabilities = await asyncio.to_thread(handler.fit_transform, texts, embeddings)

        # Bulk-update vector metadata with topic assignments
        updates = [
            {
                "id": v["id"],
                "metadata": {
                    "topic_id": int(topic_id),
                    "topic_label": handler.get_topic_label(int(topic_id)),
                    "topic_keywords": handler.get_topic_keywords(int(topic_id)),
                    "topic_probability": float(prob),
                },
            }
            for v, topic_id, prob in zip(vectors, topics, probabilities)
        ]
        print(f"[Clustering] Updating metadata for {len(updates)} vectors...")
        await asyncio.to_thread(self.vector_service.update_vector_metadata_batch, updates, namespace)

        # Build topic summary rows
        topic_info = handler.get_topic_info()
        topics_data = []
        for _, row in topic_info.iterrows():
            t_id = int(row["Topic"])
            topics_data.append({
                "topic_id": t_id,
                "label": handler.get_topic_label(t_id),
                "keywords": handler.get_topic_keywords(t_id),
                "doc_count": int(row["Count"]),
            })

        if self.db:
            await self.db.save_topics(workspace_id, topics_data)

        topic_count = len([t for t in set(topics) if t != -1])
        outlier_count = topics.count(-1)

        print(f"[Clustering] Done — {topic_count} topics, {outlier_count} outliers")
        return {
            "status": "done",
            "workspace_id": workspace_id,
            "vectors_clustered": len(vectors),
            "topics_found": topic_count,
            "outliers": outlier_count,
            "topics": topics_data,
        }
