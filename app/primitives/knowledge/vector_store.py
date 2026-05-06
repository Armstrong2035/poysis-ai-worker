import os
import time
from pinecone import Pinecone, ServerlessSpec
from typing import List, Dict, Any

# Maximum number of candidates fetched from Pinecone before score-gap trimming
_CANDIDATE_CEILING = 50

class VectorService:
    def __init__(self):
        self.api_key = os.getenv("PINE_CONE_API_KEY")
        self.index_name = "poysis-gemini"
        
        if not self.api_key:
            raise ValueError("PINE_CONE_API_KEY not found in environment")
            
        self.pc = Pinecone(api_key=self.api_key)
        # Lazy — don't connect to the index until first use
        self._index = None

    @property
    def index(self):
        """Connect to Pinecone index on first access, not at startup."""
        if self._index is None:
            self._ensure_index_exists()
            self._index = self.pc.Index(self.index_name)
        return self._index

    def _ensure_index_exists(self):
        """Creates the index if it doesn't already exist."""
        existing_indexes = [idx.name for idx in self.pc.list_indexes()]
        
        if self.index_name not in existing_indexes:
            print(f"[VECTOR] Creating new Pinecone index: {self.index_name}")
            self.pc.create_index(
                name=self.index_name,
                dimension=3072, # Gemini embedding-001 dimension
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",
                    region="us-east-1"
                )
            )
            while not self.pc.describe_index(self.index_name).status['ready']:
                time.sleep(1)
        else:
            print(f"[VECTOR] Connected to existing Pinecone index: {self.index_name}")

    def upsert_vectors(self, vectors: List[Dict[str, Any]], namespace: str, batch_size: int = 100):
        """Pushes embeddings to Pinecone with metadata in a specific namespace.
        Uses batching to stay within Pinecone's 2MB request size limit.
        """
        total_vectors = len(vectors)
        print(f"[VECTOR] Upserting {total_vectors} vectors to namespace '{namespace}' (Batch Size: {batch_size})...")
        
        for i in range(0, total_vectors, batch_size):
            batch = vectors[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_vectors + batch_size - 1) // batch_size
            
            print(f"[VECTOR]   -> Sending batch {batch_num}/{total_batches} ({len(batch)} vectors)...")
            try:
                self.index.upsert(vectors=batch, namespace=namespace, show_progress=False)
                print(f"[VECTOR]   -> Batch {batch_num} OK")
            except Exception as e:
                print(f"[VECTOR ERROR] Failed to upsert batch {batch_num}: {e}")
                raise e

    def query_vectors(self, query_embedding: List[float], namespace: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Searches Pinecone for similar vectors within a specific namespace."""
        results = self.index.query(
            vector=query_embedding,
            top_k=_CANDIDATE_CEILING,
            include_metadata=True,
            namespace=namespace
        )

        matches = []
        for match in results.get("matches", []):
            matches.append({
                "id": match.id,
                "score": match.score,
                "metadata": match.metadata
            })
        return matches

    @staticmethod
    def detect_score_gap(matches: List[Dict[str, Any]], min_results: int = 5) -> List[Dict[str, Any]]:
        """Trims results at the natural cluster boundary."""
        if len(matches) <= min_results:
            return matches

        scores = [m["score"] for m in matches]
        gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
        max_gap_idx = gaps.index(max(gaps))
        cut = max(max_gap_idx + 1, min_results)

        print(f"[VECTOR] Score gap cut: keeping {cut}/{len(matches)} candidates")
        return matches[:cut]

    def delete_all(self):
        """Purges the index."""
        print("[VECTOR] Purging index...")
        self.index.delete(delete_all=True)
