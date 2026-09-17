"""
Embedder — used only by the classify block for label/text similarity.

On AWS: Amazon Titan Embeddings V2 via Bedrock (1024-dim).
Local dev fallback: Gemini embedding-001 (when GEMINI_API_KEY is set and
  AWS_BEDROCK_REGION is not).

NOTE: These vectors are NOT stored in the main pgvector table and are NOT
compatible with the OpenAI text-embedding-3-small vectors that KnowledgeEngine
produces. They are used only for in-memory cosine similarity in the classify block.
"""
import asyncio
import json
import os
from typing import List


class Embedder:
    def __init__(self):
        self._use_bedrock = bool(os.getenv("AWS_BEDROCK_REGION") or os.getenv("AWS_DEFAULT_REGION"))

    async def get_embedding(self, text: str, task_type: str = "retrieval_document") -> List[float]:
        if self._use_bedrock:
            return await self._bedrock_embed(text)
        return await self._gemini_embed(text, task_type)

    async def _bedrock_embed(self, text: str) -> List[float]:
        import boto3
        region = os.getenv("AWS_BEDROCK_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

        def _call():
            client = boto3.client("bedrock-runtime", region_name=region)
            body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
            resp = client.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(resp["body"].read())["embedding"]

        return await asyncio.to_thread(_call)

    async def _gemini_embed(self, text: str, task_type: str) -> List[float]:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Neither AWS credentials nor GEMINI_API_KEY is configured for Embedder.")
        genai.configure(api_key=api_key)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: genai.embed_content(
                model="models/gemini-embedding-001",
                content=text,
                task_type=task_type,
            ),
        )
        return result["embedding"]
