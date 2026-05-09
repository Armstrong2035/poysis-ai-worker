import os
import json
import asyncio
import psycopg2
from psycopg2.extras import execute_values
from typing import List, Dict, Any, Optional

_CANDIDATE_CEILING = 50


class VectorService:
    def __init__(self):
        self.conn_str = os.getenv("SUPABASE_DIRECT_CONNECTION_STRING")
        if not self.conn_str:
            raise ValueError("SUPABASE_DIRECT_CONNECTION_STRING not found in environment")

    def _get_conn(self):
        return psycopg2.connect(self.conn_str)

    def _insert_batch(self, rows: list, batch_num: int, total_batches: int):
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO vectors (id, namespace, embedding, metadata)
                        VALUES %s
                        ON CONFLICT (id, namespace) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                        """,
                        rows,
                        template="(%s, %s, %s::vector, %s::jsonb)"
                    )
            print(f"[VECTOR]   -> Batch {batch_num}/{total_batches} OK")
        finally:
            conn.close()

    async def upsert_vectors(self, vectors: List[Dict[str, Any]], namespace: str, batch_size: int = 100):
        total = len(vectors)
        print(f"[VECTOR] Upserting {total} vectors to namespace '{namespace}'...")

        batches = [vectors[i:i + batch_size] for i in range(0, total, batch_size)]
        total_batches = len(batches)

        async def insert(batch, idx):
            rows = [
                (
                    v["id"],
                    namespace,
                    "[" + ",".join(str(x) for x in v["values"]) + "]",
                    json.dumps(v.get("metadata", {})),
                )
                for v in batch
            ]
            await asyncio.to_thread(self._insert_batch, rows, idx + 1, total_batches)

        await asyncio.gather(*[insert(b, i) for i, b in enumerate(batches)])

    def query_vectors(
        self,
        query_embedding: List[float],
        namespace: str,
        top_k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(top_k, _CANDIDATE_CEILING)
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                if metadata_filter:
                    cur.execute(
                        """
                        SELECT id, metadata, 1 - (embedding <=> %s::vector) AS score
                        FROM vectors
                        WHERE namespace = %s AND metadata @> %s::jsonb
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        [vec_str, namespace, json.dumps(metadata_filter), vec_str, limit]
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, metadata, 1 - (embedding <=> %s::vector) AS score
                        FROM vectors
                        WHERE namespace = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        [vec_str, namespace, vec_str, limit]
                    )

                rows = cur.fetchall()
        finally:
            conn.close()

        return [
            {"id": row[0], "metadata": row[1] or {}, "score": float(row[2])}
            for row in rows
        ][:top_k]

    @staticmethod
    def detect_score_gap(matches: List[Dict[str, Any]], min_results: int = 5) -> List[Dict[str, Any]]:
        if len(matches) <= min_results:
            return matches

        scores = [m["score"] for m in matches]
        gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
        max_gap_idx = gaps.index(max(gaps))
        cut = max(max_gap_idx + 1, min_results)

        print(f"[VECTOR] Score gap cut: keeping {cut}/{len(matches)} candidates")
        return matches[:cut]

    def delete_all(self, namespace: Optional[str] = None):
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    if namespace:
                        cur.execute("DELETE FROM vectors WHERE namespace = %s", [namespace])
                        print(f"[VECTOR] Purged namespace '{namespace}'")
                    else:
                        cur.execute("DELETE FROM vectors")
                        print("[VECTOR] Purged all vectors")
        finally:
            conn.close()
