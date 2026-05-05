# Bedrock Migration Plan

> **Branch:** All work in this plan must be done on a new branch — `feat/bedrock-migration`.
> Never merge directly to `main` until the migration is fully tested end-to-end.
> ```
> git checkout -b feat/bedrock-migration
> ```

---

## Overview

Migrate the AI layer from Google Gemini (LLM + Embeddings) to AWS Bedrock, so that
inference and embedding costs are covered by AWS Activate credits.

**What changes:** The AI primitives only. Pinecone, Supabase, Fly.io, and all API
routes stay untouched.

**Critical constraint:** Gemini Embedding-001 produces **3072-dimensional** vectors.
The current Pinecone index (`poysis-gemini`) is sized to that dimension.
The Bedrock replacement (Amazon Titan Embeddings V2) produces **1024-dimensional** vectors.
These are incompatible — the Pinecone index must be recreated and all data re-ingested
after the switch. Plan for this before going to production.

---

## Chosen AWS Bedrock Models

| Role | Current | Replacement | Bedrock Model ID |
|---|---|---|---|
| Embeddings | Gemini Embedding-001 (3072d) | Amazon Titan Embeddings V2 (1024d) | `amazon.titan-embed-text-v2:0` |
| LLM (RAG + Streaming) | Gemini 3 Flash | Claude 3.5 Haiku | `anthropic.claude-3-5-haiku-20241022-v1:0` |

Claude 3.5 Haiku is the right Gemini Flash equivalent — fast, cheap, and strong enough
for RAG synthesis. Swap to `claude-sonnet-4-5` if you want higher quality at higher cost.

---

## Step 1 — AWS Setup (Before Any Code)

1. Enable model access in the AWS Console:
   - Go to **Bedrock → Model access** in your target region (recommend `us-east-1`)
   - Request access for: `Amazon Titan Embeddings V2` and `Anthropic Claude 3.5 Haiku`
   - Access is usually granted within minutes

2. Create an IAM user (or use an existing one) with the following policy:
   ```json
   {
     "Effect": "Allow",
     "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
     "Resource": "*"
   }
   ```

3. Note down:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION` (e.g. `us-east-1`)

---

## Step 2 — Update `requirements.txt`

**Remove:**
```
google-generativeai
llama-index-embeddings-gemini
llama-index-llms-google-genai
```

**Add:**
```
boto3
llama-index-embeddings-bedrock
llama-index-llms-bedrock
```

Full updated file:
```
fastapi
uvicorn
httpx
pydantic
python-dotenv
supabase
pinecone
cohere
gunicorn
python-multipart
llama-index
llama-index-vector-stores-pinecone
llama-index-embeddings-bedrock
llama-index-llms-bedrock
llama-index-readers-file
llama-parse
pymupdf
pandas
openpyxl
boto3
```

---

## Step 3 — Update `.env.example`

Remove `GEMINI_API_KEY`. Add AWS credentials:

```env
# AWS Bedrock Configuration
AWS_ACCESS_KEY_ID=your-access-key-id
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_REGION=us-east-1
```

---

## Step 4 — Rewrite `app/primitives/knowledge/embedder.py`

This file wraps the raw embedding call used by the Classifier block.
The `task_type` parameter (retrieval_document vs retrieval_query) is a Gemini concept —
Titan Embeddings V2 does not have it. Remove it entirely.

```python
import os
import asyncio
import boto3
import json
from typing import List


class Embedder:
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.model_id = "amazon.titan-embed-text-v2:0"
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            )
        return self._client

    async def get_embedding(self, text: str, task_type: str = None) -> List[float]:
        """
        Generates vector embeddings using Amazon Titan Embeddings V2.
        task_type is accepted for interface compatibility but unused — Titan
        does not differentiate between document and query embeddings.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._invoke, text)
        return result

    def _invoke(self, text: str) -> List[float]:
        body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        response_body = json.loads(response["body"].read())
        return response_body["embedding"]
```

---

## Step 5 — Rewrite `app/primitives/knowledge/engine.py`

Three targeted changes only:

1. Replace `GeminiEmbedding` import with `BedrockEmbedding`
2. Replace `GoogleGenAI` import with `Bedrock` LLM
3. Update all four places in the file where these are instantiated

**Import block** — replace the Google imports:
```python
# Remove these:
from llama_index.embeddings.gemini import GeminiEmbedding
from llama_index.llms.google_genai import GoogleGenAI

# Add these:
from llama_index.embeddings.bedrock import BedrockEmbedding
from llama_index.llms.bedrock import Bedrock
```

**Embedding instantiation** — used in `_run_ingestion_pipeline`, `fetch_raw`, `answer_question`, `stream_answer`:
```python
# Remove:
embed_model = GeminiEmbedding(
    model_name="models/gemini-embedding-001",
    api_key=os.getenv("GEMINI_API_KEY")
)

# Replace with:
embed_model = BedrockEmbedding(
    model_name="amazon.titan-embed-text-v2:0",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
```

**LLM instantiation** — used in `answer_question` and `stream_answer`:
```python
# Remove:
llm = GoogleGenAI(
    model="gemini-3-flash-preview",
    api_key=os.getenv("GEMINI_API_KEY")
)

# Replace with:
llm = Bedrock(
    model="anthropic.claude-3-5-haiku-20241022-v1:0",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
```

No other logic in `engine.py` changes.

---

## Step 6 — Update `app/primitives/knowledge/vector_store.py`

Two changes:
1. Rename the index from `poysis-gemini` → `poysis-bedrock`
2. Update the dimension from `3072` → `1024`

```python
# Line 12 — index name:
self.index_name = "poysis-bedrock"

# Line 38 — dimension:
dimension=1024,  # Titan Embeddings V2
```

---

## Step 7 — Re-index Existing Data

Because the vector dimension changed (3072 → 1024), any existing vectors in Pinecone
are incompatible with the new model. After deploying:

1. The old `poysis-gemini` index will still exist in Pinecone — leave it until confirmed working
2. The new `poysis-bedrock` index will be created automatically on first use
3. Trigger re-ingestion of all notebooks through the existing `/ingest` or `/ingest-file`
   endpoints — the new embedding model will populate the new index
4. Once re-ingestion is verified, delete the old `poysis-gemini` index from Pinecone console

---

## Step 8 — Testing Checklist

Before merging to `main`, verify each of these manually:

- [ ] `POST /search` — returns semantically relevant results
- [ ] `POST /ingest` — JSON document ingestion succeeds
- [ ] `POST /ingest-file` — PDF, CSV, XLSX ingestion succeeds
- [ ] `POST /ask` (streaming) — SSE stream returns tokens and `__SOURCES__` footer
- [ ] `POST /ask` (non-streaming) — returns `answer` + `sources` JSON
- [ ] `POST /classify` — label scores are sensible
- [ ] `POST /cluster` — groups similar docs correctly
- [ ] `POST /recommend` — returns similar items

---

## Files Changed Summary

| File | Change |
|---|---|
| `requirements.txt` | Remove Google packages, add boto3 + Bedrock LlamaIndex adapters |
| `.env.example` | Replace `GEMINI_API_KEY` with AWS credentials |
| `app/primitives/knowledge/embedder.py` | Full rewrite — Gemini → Titan V2 |
| `app/primitives/knowledge/engine.py` | Swap 4 model instantiations — imports + usages |
| `app/primitives/knowledge/vector_store.py` | Index name + dimension update |

No router files, no API contracts, no Pinecone query logic changes.
