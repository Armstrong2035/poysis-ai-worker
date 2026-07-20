import os
import json
import asyncio
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool
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

        # Reuse a small pool instead of opening a fresh connection per batch.
        # The connection string points at Supavisor's transaction-mode pooler
        # (:6543), which recycles backend connections aggressively — repeated
        # connect/close churn against it caused intermittent
        # "connection already closed" failures during long ingestion runs.
        self.pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=self.conn_str)
        # getconn() raises immediately (doesn't block) once maxconn is checked
        # out, so cap concurrent batch inserts at the pool size to avoid
        # "connection pool exhausted" when a document yields many batches.
        self._pool_sem = asyncio.Semaphore(10)

    def _get_conn(self):
        return self.pool.getconn()

    def _put_conn(self, conn):
        # Discard broken connections instead of returning them to the pool.
        self.pool.putconn(conn, close=conn.closed)

    def _insert_batch(self, rows: list, batch_num: int, total_batches: int):
        conn = self._get_conn()
        try:
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
            conn.commit()
            print(f"[VECTOR]   -> Batch {batch_num}/{total_batches} OK")
        except Exception:
            try:
                if not conn.closed:
                    conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._put_conn(conn)

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
            async with self._pool_sem:
                await asyncio.to_thread(self._insert_batch, rows, idx + 1, total_batches)

        await asyncio.gather(*[insert(b, i) for i, b in enumerate(batches)])

    def query_vectors(
        self,
        query_embedding: List[float],
        namespace: str,
        top_k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
        source_types: Optional[List[str]] = None,
        topic_ids: Optional[List[int]] = None,
        connection_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(top_k, _CANDIDATE_CEILING)
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        # Build WHERE clauses incrementally
        conditions = ["namespace = %s"]
        params: List[Any] = [vec_str, namespace]

        if metadata_filter:
            conditions.append("metadata @> %s::jsonb")
            params.append(json.dumps(metadata_filter))

        if source_types is not None:
            # Empty list means "caller is allowed zero connection types" — must
            # still filter, not fall through to unscoped (see topic_ids below).
            conditions.append("metadata->>'source_type' = ANY(%s)")
            params.append(source_types)

        if topic_ids is not None:
            # category_id is written as an integer by the clustering step.
            # `is not None` (not truthiness) matters: an empty list is a real
            # allowlist meaning "no topics permitted" and must still filter —
            # treating it as "no restriction" would search the whole workspace.
            conditions.append("(metadata->>'category_id')::int = ANY(%s)")
            params.append(topic_ids)

        if connection_ids is not None:
            # connection_id is the youtube_channels.id (etc.) written at ingest,
            # matching the id the playground sends after stripping its "conn:" prefix.
            # Same empty-list-is-a-real-allowlist rule as topic_ids above.
            conditions.append("metadata->>'connection_id' = ANY(%s)")
            params.append(connection_ids)

        where = " AND ".join(conditions)
        params += [vec_str, limit]

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # The HNSW index covers `embedding` alone, so Postgres satisfies the
                # ORDER BY from the index and applies `namespace` as a POST-filter.
                # At the default hnsw.ef_search=40 that means ~40 globally-nearest rows
                # are considered and only those happening to sit in this namespace
                # survive — a workspace holding 16% of a shared table returned 5 rows
                # for *any* top_k, and the shortfall worsens as more namespaces are
                # added. Iterative scan keeps walking the graph until `limit` rows pass
                # the filter. Measured: 5 -> 48 rows, and slightly faster, since the
                # old query spent its time discarding results.
                cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                cur.execute(
                    f"""
                    SELECT id, metadata, 1 - (embedding <=> %s::vector) AS score
                    FROM vectors
                    WHERE {where}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
            # End the transaction before the connection returns to the pool, so the
            # SET LOCAL above can't leak into whoever borrows it next.
            conn.rollback()
        finally:
            self._put_conn(conn)

        results = [
            {"id": row[0], "metadata": row[1] or {}, "score": float(row[2])}
            for row in rows
        ]
        # relaxed_order trades exact ordering for speed, so re-sort here: callers
        # (detect_score_gap's adjacent-gap math, _diversify's per-bucket ordering)
        # all assume descending score.
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

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

    def count_vectors_by_namespace(self, namespaces: List[str]) -> Dict[str, int]:
        """{namespace: vector count} for the given namespaces, in one round trip.

        Namespaces with no rows are absent from the result — callers should treat a
        missing key as 0 (an empty bot).
        """
        if not namespaces:
            return {}

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT namespace, count(*) FROM vectors WHERE namespace = ANY(%s) GROUP BY namespace",
                    [namespaces],
                )
                rows = cur.fetchall()
            conn.rollback()
        finally:
            self._put_conn(conn)

        return {row[0]: int(row[1]) for row in rows}

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
            self._put_conn(conn)

        results = []
        for row_id, emb_text, metadata in rows:
            try:
                embedding = json.loads(emb_text)
            except Exception:
                continue
            results.append({"id": row_id, "embedding": embedding, "metadata": metadata or {}})
        return results

    def fetch_document_centroids(self, namespace: str) -> List[Dict[str, Any]]:
        """One row per document: source_id, title, and a centroid embedding
        (the average of that document's chunk embeddings, computed server-side
        via pgvector's AVG() aggregate — pulling every raw chunk embedding over
        the wire for a large namespace is heavy enough to drop the pooler
        connection; this keeps the transfer to one row per document).
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metadata->>'source_id' AS source_id,
                           MAX(metadata->>'title') AS title,
                           AVG(embedding)::text AS centroid
                    FROM vectors
                    WHERE namespace = %s
                    GROUP BY metadata->>'source_id'
                    """,
                    [namespace],
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)
        return [
            {"source_id": r[0], "title": r[1], "centroid": json.loads(r[2])}
            for r in rows if r[2]
        ]

    def fetch_category_assignments(self, namespace: str) -> Dict[str, int]:
        """source_id -> category_id for every document currently assigned one.

        Used before reclustering to know which documents belong to a locked
        topic (chunks of the same source_id always share one category_id, so
        any one chunk's value is representative).
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metadata->>'source_id' AS source_id,
                           MAX((metadata->>'category_id')::int) AS category_id
                    FROM vectors
                    WHERE namespace = %s AND metadata->>'category_id' IS NOT NULL
                    GROUP BY metadata->>'source_id'
                    """,
                    [namespace],
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)
        return {row[0]: row[1] for row in rows}

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
            self._put_conn(conn)
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
            self._put_conn(conn)
        return [
            {"source_id": r[0], "title": r[1], "url": r[2], "source_type": r[3], "chunks": r[4]}
            for r in rows
        ]

    def list_documents_with_snippets(
        self, namespace: str, snippet_words: int = 150, topic_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """One row per document: title + first 3 chunks concatenated for richer context.

        topic_id optionally narrows to documents whose chunks were tagged with
        that category during clustering. The clustering step (categorizer.py)
        writes the assignment as metadata.category_id (an int), not
        metadata.topic_id — mirrors the cast used in query_vectors.
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # Full-namespace calls (no topic_id filter) scan every chunk with a
                # window function + string_agg — heavy enough on a large workspace
                # to exceed the pooler's default statement_timeout. Bounded rather
                # than unlimited: this should still fail loudly within a few
                # minutes instead of hanging indefinitely if something is
                # genuinely wrong (e.g. disk I/O throttling on the DB side).
                cur.execute("SET statement_timeout = '180000'")
                topic_filter = "AND (metadata->>'category_id')::int = %s::int" if topic_id is not None else ""
                params = [namespace] + ([topic_id] if topic_id is not None else [])
                cur.execute(
                    f"""
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
                        {topic_filter}
                    ) sub
                    WHERE rn <= 3
                    GROUP BY metadata->>'source_id'
                    """,
                    params,
                )
                rows = cur.fetchall()
        finally:
            self._put_conn(conn)

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
            with conn.cursor() as cur:
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
            conn.commit()
        except Exception:
            try:
                if not conn.closed:
                    conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._put_conn(conn)

    def delete_all(self, namespace: Optional[str] = None):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                if namespace:
                    cur.execute("DELETE FROM vectors WHERE namespace = %s", [namespace])
                    print(f"[VECTOR] Purged namespace '{namespace}'")
                else:
                    cur.execute("DELETE FROM vectors")
                    print("[VECTOR] Purged all vectors")
            conn.commit()
        except Exception:
            try:
                if not conn.closed:
                    conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._put_conn(conn)
