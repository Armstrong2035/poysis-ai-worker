import os
from typing import List, Dict, Any, Optional
from app.primitives.knowledge.embedder import Embedder
from app.primitives.knowledge.vector_store import VectorService

# LlamaIndex Imports
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.ingestion import IngestionPipeline
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI
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
        max_retries = 5
        for attempt in range(max_retries):
            try:
                embeddings = await self.embed_model.aget_text_embedding_batch(texts, show_progress=False)
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait = 30 * (2 ** attempt)
                    print(f"[STEP 3 EMBED ] Rate limited — waiting {wait}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                else:
                    raise
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
            await asyncio.to_thread(self.vector_service.upsert_vectors, vectors, notebook_id)
            upsert_secs = time.perf_counter() - t1
            print(f"[STEP 4 UPSERT] done — {upsert_secs:.1f}s")

        return len(vectors)

    async def fetch_raw(
        self,
        notebook_id: str,
        text: str,
        top_k: int = 10,
        topic_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query_embedding = await self.embed_model.aget_query_embedding(text)

        metadata_filter = {"topic_id": topic_id} if topic_id is not None else None

        matches = self.vector_service.query_vectors(
            query_embedding=query_embedding,
            namespace=notebook_id,
            top_k=top_k,
            metadata_filter=metadata_filter,
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

    async def answer_question(self, notebook_id: str, query: str) -> Dict[str, Any]:
        """
        THE INTELLIGENCE LAYER: Answers a direct question by synthesizing
        information from the relevant document chunks.
        """
        # 1. Retrieve relevant chunks
        chunks = await self.fetch_raw(notebook_id, query, top_k=3)

        if not chunks:
            return {"answer": "No relevant information found in this notebook.", "sources": []}

        # 2. Build context
        context_parts = [
            f"[Source: {c['metadata'].get('source_file', 'unknown')}]\n{c['text']}"
            for c in chunks
        ]
        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            "Answer the following question based on the provided context. "
            "If the context does not contain enough information, say so.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Answer:"
        )

        # 3. Call Gemini
        llm = GoogleGenAI(
            model="gemini-2.0-flash",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        print(f"[KnowledgeEngine] Reasoning across notebook '{notebook_id}' to answer: '{query}'")
        response = await llm.acomplete(prompt)

        sources = [
            {
                "file": c["metadata"].get("source_file"),
                "score": c["score"],
                "snippet": c["text"][:200] + "..."
            }
            for c in chunks
        ]

        return {
            "answer": str(response),
            "sources": sources
        }

    async def stream_answer(self, notebook_id: str, query: str):
        """
        STREAMING INTELLIGENCE LAYER: Yields answer tokens as they arrive from Gemini.
        First token appears in ~1s. Sources are yielded last as a JSON object.
        Use with FastAPI StreamingResponse.
        """
        import json

        # 1. Retrieve relevant chunks
        chunks = await self.fetch_raw(notebook_id, query, top_k=3)

        # 2. Build context
        context_parts = [
            f"[Source: {c['metadata'].get('source_file', 'unknown')}]\n{c['text']}"
            for c in chunks
        ]
        context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."

        prompt = (
            "Answer the following question based on the provided context.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Answer:"
        )

        # 3. Setup streaming LLM
        llm = GoogleGenAI(
            model="gemini-2.0-flash",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        print(f"[KnowledgeEngine] Streaming answer for notebook '{notebook_id}'...")

        # 4. Stream tokens as they arrive
        streaming_response = await llm.astream_complete(prompt)
        async for delta in streaming_response:
            yield delta.delta

        # 5. Yield sources as a final structured chunk
        sources = [
            {
                "file": c["metadata"].get("source_file"),
                "score": c["score"],
                "snippet": c["text"][:200] + "..."
            }
            for c in chunks
        ]
        yield f"\n\n__SOURCES__{json.dumps(sources)}"

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
