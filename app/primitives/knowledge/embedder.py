import os
import asyncio
from typing import List
import google.generativeai as genai

class Embedder:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
        
    async def get_embedding(self, text: str, task_type: str = "retrieval_document") -> List[float]:
        """
        Generates vector embeddings using Gemini API.
        task_type: 'retrieval_document' (for indexing) or 'retrieval_query' (for searching)
        """
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")
            
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: genai.embed_content(
                model="models/gemini-embedding-001",
                content=text,
                task_type=task_type
            )
        )
        return result["embedding"]
