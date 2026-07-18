import os
from typing import List, Dict, Any, Optional
from app.primitives.knowledge.embedder import Embedder
from app.primitives.knowledge.vector_store import VectorService

# LlamaIndex Imports
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.ingestion import IngestionPipeline
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core.readers import SimpleDirectoryReader
from llama_parse import LlamaParse

class KnowledgeEngine:
    """
    The Unified Knowledge Engine (Core Memory).
    Consolidates embedding generation and vector storage into a single capability.
    BERTopic clustering runs as a separate post-snapshot step via ConsolidationEngine.
    """
    def __init__(self):
        self.embedder = Embedder()
        self.vector_service = VectorService()
        self.embed_model = OpenAIEmbedding(
            model="text-embedding-3-small",
            api_key=os.getenv("OPENAI_API_KEY"),
            embed_batch_size=512,
        )

    async def upsert_documents(self, notebook_id: str, documents: List[Dict[str, Any]]) -> int:
        """
        [Legacy/JSON Path] Takes a list of standard JSON documents and indexes them.
        Each doc should have 'text' and 'source_id'.
        """
        if not notebook_id:
            raise ValueError("notebook_id is required for indexing.")

        # Convert simple JSON docs to LlamaIndex Documents for unified processing
        llama_docs = [
            Document(
                text=doc.get("text") or doc.get("content"),
                id_=str(doc.get("source_id") or doc.get("id")),
                metadata=doc.get("metadata") or {}
            )
            for doc in documents if doc.get("text") or doc.get("content")
        ]

        if not llama_docs:
            return 0

        return await self._run_ingestion_pipeline(notebook_id, llama_docs)

    async def _run_ingestion_pipeline(self, notebook_id: str, documents: List[Document]) -> int:
        """
        Core ingestion pipeline using LlamaIndex for embedding, then upserts via
        VectorService directly to Supabase pgvector.
        """
        import asyncio
        import time

        # Step 1: chunk with SentenceSplitter (CPU-only, fast)
        splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)
        nodes = splitter.get_nodes_from_documents(documents)

        # Step 2: batch embed directly — bypasses IngestionPipeline's one-at-a-time behaviour
        texts = [node.get_content() for node in nodes]
        doc_count = len(texts)
        print(f"[STEP 3 EMBED ] {doc_count} chunk(s) → text-embedding-3-small | namespace='{notebook_id}'")
        t0 = time.perf_counter()
        embeddings = await self._embed_batch(texts)
        embed_secs = time.perf_counter() - t0
        for node, embedding in zip(nodes, embeddings):
            node.embedding = embedding
        embedded_nodes = [n for n in nodes if n.embedding]
        chunks = len(embedded_nodes)
        rate = chunks / embed_secs if embed_secs > 0 else 0
        print(f"[STEP 3 EMBED ] done — {chunks} vectors | {embed_secs:.1f}s | {rate:.1f} chunks/s")

        # 4. Upsert via VectorService — include _text so retrieval doesn't need a separate store
        vectors = [
            {
                "id": node.node_id,
                "values": node.embedding,
                "metadata": {
                    **{k: v for k, v in node.metadata.items() if v is not None},
                    "_text": node.get_content(),
                },
            }
            for node in embedded_nodes
        ]
        if vectors:
            print(f"[STEP 4 UPSERT] {len(vectors)} vectors → Supabase pgvector | namespace='{notebook_id}'")
            t1 = time.perf_counter()
            await self.vector_service.upsert_vectors(vectors, notebook_id)
            upsert_secs = time.perf_counter() - t1
            print(f"[STEP 4 UPSERT] done — {upsert_secs:.1f}s")

        return len(vectors)

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed texts with sub-batching and rate-limit retry."""
        import asyncio
        SUB_BATCH = 200
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), SUB_BATCH):
            sub = texts[i:i + SUB_BATCH]
            for attempt in range(5):
                try:
                    result = await self.embed_model.aget_text_embedding_batch(sub, show_progress=False)
                    embeddings.extend(result)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 4:
                        wait = 30 * (2 ** attempt)
                        print(f"[EMBED] Rate limited — waiting {wait}s (attempt {attempt + 1}/5)")
                        await asyncio.sleep(wait)
                    else:
                        raise
        return embeddings

    async def embed_and_store(
        self,
        namespace: str,
        chunks: list,           # List[ProcessedChunk] — 60s pre-chunks from TranscriptProcessor
        topic_threshold: float = 0.75,
    ) -> int:
        """
        Two-pass transcript ingestion:
          Pass 1 — embed 60s pre-chunks, run cosine-similarity segmentation to find topic
                   boundaries, merge adjacent same-topic pre-chunks into topic chunks.
          Pass 2 — embed topic chunks (the ones actually stored), upsert to pgvector.

        Skips SentenceSplitter entirely — timestamp boundaries are preserved.
        """
        import time, uuid

        if not chunks:
            return 0

        # --- Pass 1: embed pre-chunks to detect topic boundaries ---
        print(f"[STEP 3a EMBED] {len(chunks)} pre-chunks → topic segmentation | namespace='{namespace}'")
        t0 = time.perf_counter()
        block_embeddings = await self._embed_batch([c.text for c in chunks])
        print(f"[STEP 3a EMBED] done — {time.perf_counter() - t0:.1f}s")

        topic_groups = _find_topic_groups(chunks, block_embeddings, threshold=topic_threshold)
        merged = [_merge_transcript_chunks(g) for g in topic_groups]
        print(f"[TOPIC SEG   ] {len(chunks)} pre-chunks → {len(merged)} topic chunks")

        # --- Pass 2: embed merged topic chunks for storage ---
        print(f"[STEP 3b EMBED] {len(merged)} topic chunks → storage embeddings | namespace='{namespace}'")
        t1 = time.perf_counter()
        topic_embeddings = await self._embed_batch([c.text for c in merged])
        print(f"[STEP 3b EMBED] done — {time.perf_counter() - t1:.1f}s")

        # --- Upsert ---
        vectors = []
        for chunk, embedding in zip(merged, topic_embeddings):
            vec_id = f"{chunk.source_id}_{chunk.timestamp_start_ms if chunk.timestamp_start_ms is not None else uuid.uuid4().hex}"
            metadata = {
                "source_id": chunk.source_id,
                "source_type": chunk.source_type,
                "title": chunk.title,
                "url": chunk.url,
                "_text": chunk.text,
                **chunk.extra_metadata,
            }
            if chunk.timestamp_start_ms is not None:
                metadata["timestamp_start_ms"] = chunk.timestamp_start_ms
            if chunk.timestamp_end_ms is not None:
                metadata["timestamp_end_ms"] = chunk.timestamp_end_ms
            vectors.append({"id": vec_id, "values": embedding, "metadata": metadata})

        if vectors:
            print(f"[STEP 4 UPSERT] {len(vectors)} vectors → namespace='{namespace}'")
            t2 = time.perf_counter()
            await self.vector_service.upsert_vectors(vectors, namespace)
            print(f"[STEP 4 UPSERT] done — {time.perf_counter() - t2:.1f}s")

        return len(vectors)

    async def fetch_raw(
        self,
        notebook_id: str,
        text: str,
        top_k: int = 10,
        topic_id: Optional[int] = None,
        source_types: Optional[List[str]] = None,
        topic_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        query_embedding = await self.embed_model.aget_query_embedding(text)

        # topic_id (singular) maps to the legacy metadata_filter path; topic_ids (plural)
        # maps to category_id written by the clustering step and is used for playground scoping.
        metadata_filter = {"topic_id": topic_id} if topic_id is not None else None

        matches = self.vector_service.query_vectors(
            query_embedding=query_embedding,
            namespace=notebook_id,
            top_k=top_k,
            metadata_filter=metadata_filter,
            source_types=source_types,
            topic_ids=topic_ids,
        )

        results = []
        for match in matches:
            metadata = match["metadata"] or {}
            results.append({
                "id": match["id"],
                "score": match["score"],
                "metadata": {k: v for k, v in metadata.items() if k != "_text"},
                "text": metadata.get("_text", ""),
            })

        return results

    async def ingest_file(self, notebook_id: str, file_path: str) -> int:
        """
        Ingests a file into the knowledge index.
        - CSV/Excel: custom row-by-row parser (each row = one vector, columns preserved as metadata)
        - PDF: LlamaParse if API key available, else SimpleDirectoryReader
        - TXT/DOCX: SimpleDirectoryReader
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        print(f"[KnowledgeEngine] Ingesting '{os.path.basename(file_path)}' ({ext}) for notebook '{notebook_id}'")

        try:
            # Spreadsheets: use custom row-by-row parser
            if ext in {".csv", ".xlsx", ".xls"}:
                from app.primitives.knowledge.parsers.csv import parse_spreadsheet
                print(f"[KnowledgeEngine] Using row-by-row spreadsheet parser...")
                rows = parse_spreadsheet(file_path)
                documents = [
                    Document(
                        text=row["text"],
                        metadata={
                            **row["metadata"],
                            "notebook_id": notebook_id,
                            "source_file": os.path.basename(file_path),
                        }
                    )
                    for row in rows if row.get("text")
                ]
                # Each row is already a discrete unit — skip chunking
                return await self._run_ingestion_pipeline(notebook_id, documents)

            # All other formats: LlamaIndex readers
            else:
                file_extractor = {}
                llama_parse_key = os.getenv("LLAMA_CLOUD_API_KEY")
                if llama_parse_key and ext == ".pdf":
                    print(f"[KnowledgeEngine] Using LlamaParse for high-fidelity PDF extraction...")
                    parser = LlamaParse(
                        api_key=llama_parse_key,
                        result_type="markdown",
                        num_workers=4
                    )
                    file_extractor = {".pdf": parser}

                reader = SimpleDirectoryReader(input_files=[file_path], file_extractor=file_extractor)
                documents = reader.load_data()
                for doc in documents:
                    doc.metadata["notebook_id"] = notebook_id
                    doc.metadata["source_file"] = os.path.basename(file_path)

                return await self._run_ingestion_pipeline(notebook_id, documents)

        except Exception as e:
            print(f"[KnowledgeEngine Error] Ingestion failed for {file_path}: {e}")
            import traceback
            traceback.print_exc()
            return 0


# ---------------------------------------------------------------------------
# Topic segmentation helpers (used by KnowledgeEngine.embed_and_store)
# ---------------------------------------------------------------------------

def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Dot product — valid cosine similarity when embeddings are L2-normalised (OpenAI)."""
    return sum(x * y for x, y in zip(a, b))


def _smooth(values: List[float], window: int = 3) -> List[float]:
    """Simple moving average to reduce noise in the similarity curve."""
    half = window // 2
    result = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        result.append(sum(values[lo:hi]) / (hi - lo))
    return result


def _find_topic_groups(chunks: list, embeddings: List[List[float]], threshold: float, smooth_window: int = 3) -> list:
    """
    Split chunks into topic groups by finding valleys in the cosine-similarity curve.
    A valley below `threshold` between two adjacent chunks signals a topic shift.
    """
    if len(chunks) <= 1:
        return [chunks]

    sims = [_cosine_sim(embeddings[i], embeddings[i + 1]) for i in range(len(embeddings) - 1)]
    smoothed = _smooth(sims, smooth_window)

    groups: list = []
    current = [chunks[0]]
    for i, sim in enumerate(smoothed):
        next_chunk = chunks[i + 1]
        if sim < threshold:
            groups.append(current)
            current = [next_chunk]
        else:
            current.append(next_chunk)
    if current:
        groups.append(current)
    return groups


def _merge_transcript_chunks(group: list) -> Any:
    """Merge a list of ProcessedChunks into one, spanning the full time range of the group."""
    from app.primitives.consolidation.processors.base import ProcessedChunk
    first, last = group[0], group[-1]
    return ProcessedChunk(
        text="\n".join(c.text for c in group),
        source_id=first.source_id,
        source_type=first.source_type,
        title=first.title,
        url=first.url,  # deep-links to the start of this topic
        timestamp_start_ms=first.timestamp_start_ms,
        timestamp_end_ms=last.timestamp_end_ms,
        extra_metadata={
            **first.extra_metadata,
            "end_time": last.extra_metadata.get("end_time", ""),
            "end_seconds": last.extra_metadata.get("start_seconds", 0),
        },
    )
