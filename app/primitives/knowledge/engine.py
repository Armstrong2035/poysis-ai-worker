import os
from typing import List, Dict, Any, Optional
from app.primitives.knowledge.embedder import Embedder
from app.primitives.knowledge.vector_store import VectorService
from app.primitives.knowledge.bertopic_handler import BertopicHandler

# LlamaIndex Imports
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.ingestion import IngestionPipeline
from llama_index.vector_stores.pinecone import PineconeVectorStore
from llama_index.embeddings.gemini import GeminiEmbedding
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core.readers import SimpleDirectoryReader
from llama_index.core import VectorStoreIndex, StorageContext
from llama_parse import LlamaParse

class KnowledgeEngine:
    """
    The Unified Knowledge Engine (Core Memory).
    Consolidates embedding generation, BERTopic clustering, and vector storage
    into a single ingestion capability.
    """
    def __init__(self):
        self.embedder = Embedder()
        self.vector_service = VectorService()
        self.topic_handler = BertopicHandler(
            model_path=os.getenv("BERTOPIC_MODEL_PATH", "models/bertopic_model"),
            min_topic_size=int(os.getenv("BERTOPIC_MIN_TOPIC_SIZE", "15")),
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

    async def _run_ingestion_pipeline(self, notebook_id: str, documents: List[Document], chunk: bool = True) -> int:
        """
        Core ingestion pipeline using LlamaIndex for embedding, enriches chunks
        with BERTopic topic metadata, then upserts via VectorService directly to
        avoid llama_index's PineconeVectorStore hanging indefinitely on async_add.
        Set chunk=False for pre-segmented documents (e.g. spreadsheet rows).
        """
        import asyncio

        # 1. Setup Embedding Model (Gemini)
        embed_model = GeminiEmbedding(
            model_name="models/gemini-embedding-001",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        # 2. Create Pipeline — embed only, no vector store (we upsert manually below)
        transformations = []
        if chunk:
            transformations.append(SentenceSplitter(chunk_size=512, chunk_overlap=50))
        transformations.append(embed_model)

        pipeline = IngestionPipeline(transformations=transformations)

        # 3. Embed (Async)
        nodes = await pipeline.arun(documents=documents, show_progress=True)
        embedded_nodes = [node for node in nodes if node.embedding]

        # 4. Enrich metadata with BERTopic topics. Clustering is best-effort so
        # ingestion remains available even if the local model artifacts are cold.
        metadata_by_node_id = {
            node.node_id: {k: v for k, v in node.metadata.items() if v is not None}
            for node in embedded_nodes
        }
        if embedded_nodes:
            try:
                texts = [node.get_content() for node in embedded_nodes]
                embeddings = [node.embedding for node in embedded_nodes]

                self.topic_handler.load_or_initialize()
                if self.topic_handler.has_model:
                    topics, probabilities = self.topic_handler.transform_new_documents(texts, embeddings)
                else:
                    topics, probabilities = self.topic_handler.fit_transform(texts, embeddings)

                chunks = [
                    {"id": node.node_id, "metadata": metadata_by_node_id[node.node_id]}
                    for node in embedded_nodes
                ]
                enriched_chunks = self.topic_handler.enrich_chunks(chunks, topics, probabilities)
                metadata_by_node_id = {
                    chunk["id"]: chunk["metadata"]
                    for chunk in enriched_chunks
                }
                self.topic_handler.save_model()
            except Exception as e:
                print(f"[KnowledgeEngine Warning] BERTopic enrichment skipped: {e}")

        # 5. Upsert via VectorService — strip None values Pinecone rejects
        vectors = [
            {
                "id": node.node_id,
                "values": node.embedding,
                "metadata": metadata_by_node_id.get(node.node_id, {}),
            }
            for node in embedded_nodes
        ]
        if vectors:
            await asyncio.to_thread(self.vector_service.upsert_vectors, vectors, notebook_id)

        return len(vectors)

    async def fetch_raw(
        self,
        notebook_id: str,
        text: str,
        top_k: int = 10,
        topic_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        FIXED: Now uses LlamaIndex's internal retriever logic to ensure 
        correct metadata mapping and scoring.
        """
        # Topic filtering is handled by the raw Pinecone path because LlamaIndex
        # metadata filter support varies by vector store adapter version.
        if topic_id is not None:
            query_embedding = await self.embedder.get_embedding(text, task_type="retrieval_query")
            matches = self.vector_service.query_vectors(
                query_embedding=query_embedding,
                namespace=notebook_id,
                top_k=top_k,
                metadata_filter={"topic_id": int(topic_id)},
            )
            return matches[:top_k]

        # 1. Setup Vector Store
        vector_store = PineconeVectorStore(
            pinecone_index=self.vector_service.index,
            namespace=notebook_id
        )
        
        # 2. Setup Embedder (Gemini)
        embed_model = GeminiEmbedding(
            model_name="models/gemini-embedding-001",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        # 3. Create Index and Retriever
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=embed_model
        )
        
        # We fetch extra candidates to perform the same 'Score Gap' trimming Poysis likes
        retriever = index.as_retriever(similarity_top_k=top_k * 2)
        nodes = await retriever.aretrieve(text)
        
        # 4. Format for Poysis Blocks
        results = []
        for node in nodes:
            results.append({
                "id": node.node.node_id,
                "score": node.score,
                "metadata": node.node.metadata,
                "text": node.node.get_content() # Always pulls full text
            })
        
        return results

    async def answer_question(self, notebook_id: str, query: str) -> Dict[str, Any]:
        """
        THE INTELLIGENCE LAYER: Answers a direct question by synthesizing 
        information from the relevant document chunks.
        """
        # 1. Setup Retrieval Components
        vector_store = PineconeVectorStore(
            pinecone_index=self.vector_service.index,
            namespace=notebook_id
        )
        
        embed_model = GeminiEmbedding(
            model_name="models/gemini-embedding-001",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        # 2. Setup Reasoning Engine (Gemini 3 Flash via google-genai)
        llm = GoogleGenAI(
            model="gemini-3-flash-preview",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        # 3. Create Query Engine (top_k=3 for speed without significant quality loss)
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=embed_model
        )
        
        query_engine = index.as_query_engine(
            llm=llm,
            similarity_top_k=3,
            streaming=False
        )

        # 4. Execute RAG
        print(f"[KnowledgeEngine] Reasoning across notebook '{notebook_id}' to answer: '{query}'")
        response = await query_engine.aquery(query)
        
        # 5. Extract Citations
        sources = []
        for node in response.source_nodes:
            sources.append({
                "file": node.node.metadata.get("source_file"),
                "score": node.score,
                "snippet": node.node.get_content()[:200] + "..."
            })

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

        # 1. Setup Components (same as answer_question)
        vector_store = PineconeVectorStore(
            pinecone_index=self.vector_service.index,
            namespace=notebook_id
        )
        embed_model = GeminiEmbedding(
            model_name="models/gemini-embedding-001",
            api_key=os.getenv("GEMINI_API_KEY")
        )
        llm = GoogleGenAI(
            model="gemini-3-flash-preview",
            api_key=os.getenv("GEMINI_API_KEY")
        )

        # 2. Create Streaming Query Engine
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=embed_model
        )
        query_engine = index.as_query_engine(
            llm=llm,
            similarity_top_k=3,
            streaming=True  # KEY: enables token streaming
        )

        print(f"[KnowledgeEngine] Streaming answer for notebook '{notebook_id}'...")
        
        # 3. Stream tokens as they arrive
        streaming_response = await query_engine.aquery(query)
        
        async for token in streaming_response.async_response_gen():
            yield token

        # 4. Yield sources as a final structured chunk
        sources = []
        for node in streaming_response.source_nodes:
            sources.append({
                "file": node.node.metadata.get("source_file"),
                "score": node.score,
                "snippet": node.node.get_content()[:200] + "..."
            })
        
        # Signal end of stream with sources metadata
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
                return await self._run_ingestion_pipeline(notebook_id, documents, chunk=False)

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
