import os
import json
import asyncio
import psycopg2
from psycopg2.extras import execute_values
from typing import List, Dict, Any, Optional

_CANDIDATE_CEILING = 50


def _strip_null_bytes(value):
    """Postgres JSONB rejects \\u0000. PDFs and Office docs sometimes embed them."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_null_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_null_bytes(v) for v in value]
    return value


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
                    json.dumps(_strip_null_bytes(v.get("metadata", {}))),
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

    def fetch_all_vectors(self, namespace: str) -> List[Dict[str, Any]]:
        """Fetch all vectors with embeddings for a namespace (used for clustering)."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '0'")
                cur.execute(
                    "SELECT id, embedding::text, metadata FROM vectors WHERE namespace = %s",
                    [namespace]
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        results = []
        for row_id, emb_text, metadata in rows:
            try:
                embedding = json.loads(emb_text)
            except Exception:
                continue
            results.append({"id": row_id, "embedding": embedding, "metadata": metadata or {}})
        return results

    def fetch_vector_source_ids(self, namespace: str) -> List[Dict[str, Any]]:
        """Lightweight fetch: vector id + source_id only, no embeddings."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, metadata->>'source_id' FROM vectors WHERE namespace = %s",
                    [namespace],
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "source_id": r[1]} for r in rows]

    def list_documents(self, namespace: str) -> List[Dict[str, Any]]:
        """Return one row per distinct source_id with title, url, and chunk count."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        metadata->>'source_id'  AS source_id,
                        metadata->>'title'      AS title,
                        metadata->>'url'        AS url,
                        metadata->>'source_type' AS source_type,
                        COUNT(*)                AS chunk_count
                    FROM vectors
                    WHERE namespace = %s
                    GROUP BY 1, 2, 3, 4
                    ORDER BY title
                    """,
                    [namespace],
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            {"source_id": r[0], "title": r[1], "url": r[2], "source_type": r[3], "chunks": r[4]}
            for r in rows
        ]

    def list_documents_with_snippets(self, namespace: str, snippet_words: int = 150) -> List[Dict[str, Any]]:
        """One row per document: title + first 3 chunks concatenated for richer context."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        metadata->>'source_id'                          AS source_id,
                        MAX(metadata->>'title')                         AS title,
                        MAX(metadata->>'url')                           AS url,
                        string_agg(metadata->>'_text', ' ' ORDER BY id) AS combined_text
                    FROM (
                        SELECT *,
                            ROW_NUMBER() OVER (
                                PARTITION BY metadata->>'source_id' ORDER BY id
                            ) AS rn
                        FROM vectors
                        WHERE namespace = %s
                    ) sub
                    WHERE rn <= 3
                    GROUP BY metadata->>'source_id'
                    """,
                    [namespace],
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        results = []
        for source_id, title, url, combined_text in rows:
            words = (combined_text or "").split()
            snippet = " ".join(words[:snippet_words])
            results.append({
                "source_id": source_id,
                "title": (title or "").strip(),
                "url": url or "",
                "snippet": snippet,
            })
        return results

    def update_vector_metadata_batch(self, updates: List[Dict[str, Any]], namespace: str) -> None:
        """Merge topic metadata into existing vector metadata records."""
        if not updates:
            return
        conn = self._get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    from psycopg2.extras import execute_values
                    execute_values(
                        cur,
                        """
                        UPDATE vectors AS v
                        SET metadata = v.metadata || u.new_meta::jsonb
                        FROM (VALUES %s) AS u(vid, ns, new_meta)
                        WHERE v.id = u.vid AND v.namespace = u.ns
                        """,
                        [(u["id"], namespace, json.dumps(u["metadata"])) for u in updates],
                        template="(%s, %s, %s)"
                    )
        finally:
            conn.close()

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
