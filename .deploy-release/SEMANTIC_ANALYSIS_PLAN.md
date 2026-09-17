# Semantic Analysis During Clustering — Implementation Plan

## Problem
Topic labels alone don't capture semantic essence. "Video Script Series" doesn't convey it's critiques on social media/search. Ouroboros needs richer understanding.

## Solution
Run parallel semantic analysis during clustering. Sample documents from each topic, have Gemini analyze what they're actually about.

## Architecture

### Phase 1: Add Semantic Analysis to Clustering Engine
**File:** `app/primitives/consolidation/clustering.py`

Add to `ClusteringEngine.run_clustering()`:

```python
async def _analyze_topic_semantics(self, workspace_id: str, topics: list) -> dict:
    """Parallel task: analyze semantic content of each topic."""
    semantic_data = {}
    
    for topic in topics:
        topic_id = topic["topic_id"]
        doc_count = topic["doc_count"]
        
        # Sample ~3-5 docs from this topic (use vector search)
        sample_docs = await self.db.get_topic_samples(
            workspace_id, 
            topic_id, 
            limit=min(5, doc_count)
        )
        
        if not sample_docs:
            continue
        
        # Concatenate samples
        doc_text = "\n---\n".join([
            f"[{doc['title']}]\n{doc['preview'][:500]}"
            for doc in sample_docs
        ])
        
        # Ask Gemini what this topic is actually about
        prompt = f"""Analyze these {len(sample_docs)} document samples from a knowledge cluster.
What is this cluster actually about? What are the core themes?

Samples:
{doc_text}

Respond with JSON:
{{
  "semantic_summary": "1-2 sentence summary of actual content",
  "key_themes": ["theme1", "theme2", "theme3"],
  "suggested_use_cases": ["use case 1", "use case 2"]
}}"""
        
        try:
            model = genai.GenerativeModel("gemini-3.5-flash")
            response = model.generate_content(prompt)
            analysis = json.loads(response.text)
            semantic_data[topic_id] = analysis
        except Exception as e:
            print(f"[SEMANTIC] Error analyzing topic {topic_id}: {e}")
            semantic_data[topic_id] = None
    
    return semantic_data
```

### Phase 2: Update Database Schema
**Migration needed:**
```sql
ALTER TABLE consolidation_topics 
ADD COLUMN semantic_summary TEXT,
ADD COLUMN key_themes TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN suggested_use_cases TEXT[] DEFAULT ARRAY[]::TEXT[];
```

### Phase 3: Save Semantic Data
In `clustering.py`, after topic clustering completes:

```python
# In run_clustering() after creating topics
semantic_data = await self._analyze_topic_semantics(workspace_id, topics)

# Enrich topics with semantic analysis
for topic in topics:
    topic_id = topic["topic_id"]
    if topic_id in semantic_data and semantic_data[topic_id]:
        topic["semantic_summary"] = semantic_data[topic_id]["semantic_summary"]
        topic["key_themes"] = semantic_data[topic_id]["key_themes"]
        topic["suggested_use_cases"] = semantic_data[topic_id]["suggested_use_cases"]

# Save to DB (update_topics or save_topics method)
await self.db.save_topics_with_semantics(workspace_id, topics)
```

### Phase 4: Update DatabaseService
**File:** `app/primitives/database.py`

```python
async def save_topics_with_semantics(self, workspace_id: str, topics: list) -> None:
    """Save topics including semantic analysis."""
    if not self.client or not topics:
        return
    try:
        rows = [
            {
                "workspace_id": workspace_id,
                "topic_id": t["topic_id"],
                "label": t.get("label"),
                "keywords": t.get("keywords", []),
                "doc_count": t.get("doc_count", 0),
                "parent_topic_id": t.get("parent_topic_id"),
                "semantic_summary": t.get("semantic_summary"),
                "key_themes": t.get("key_themes", []),
                "suggested_use_cases": t.get("suggested_use_cases", []),
                "updated_at": "now()",
            }
            for t in topics
        ]
        self.client.table("consolidation_topics").upsert(
            rows, on_conflict="workspace_id,topic_id"
        ).execute()
    except Exception as e:
        print(f"[DATABASE ERROR] Failed to save topics with semantics: {e}")
```

### Phase 5: Update Topics Endpoint
**File:** `app/api/consolidation.py`

Modify `/topics/{workspace_id}` to return semantic data:

```python
@router.get("/topics/{workspace_id}")
async def get_topics(workspace_id: str):
    topics = await db.get_topics(workspace_id)
    # get_topics() now includes semantic_summary, key_themes, suggested_use_cases
    return {
        "workspace_id": workspace_id,
        "topics": topics
    }
```

### Phase 6: Update Ouroboros to Use Semantic Data
**File:** `app/ouroboros/promise_detector.py`

Enhance the prompt to include semantic analysis:

```python
# Build richer topic description
topic_text = "\n".join([
    f"- {t['label']}: {t['doc_count']} documents"
    f"\n  About: {t.get('semantic_summary', 'N/A')}"
    f"\n  Themes: {', '.join(t.get('key_themes', []))}"
    for t in topics
])

prompt = f"""Analyze this user's knowledge base and suggest what AI agents/bots they could build.

Their consolidated topics:
{topic_text}
...
"""
```

### Phase 7: Frontend Display
**Where:** Dashboard topics section / SourcesModal

Show for each topic:
```
Video Script Series (44 docs)
└─ About: In-depth video critiques examining social media platforms, 
   search engine design, and technological solutions.
   Themes: social media critique, search engines, tech solutions
   Suggested uses: Q&A bot, research synthesis, tech strategy
```

## Parallel Execution Strategy

The semantic analysis must run **in parallel with clustering**, not after:

```python
async def run_clustering(self, workspace_id: str):
    # Start clustering
    clustering_task = asyncio.create_task(self._cluster_documents(...))
    
    # Topics are returned gradually, analyze them as they come
    topics = await clustering_task
    
    # Run semantic analysis in parallel with clustering completion
    semantic_task = asyncio.create_task(
        self._analyze_topic_semantics(workspace_id, topics)
    )
    
    semantic_data = await semantic_task
    
    # Save both together
    await self.db.save_topics_with_semantics(workspace_id, topics + semantic_data)
```

## Cost Estimate
- ~10-15 Gemini API calls per consolidation (one per topic)
- ~1000 tokens per call (doc samples + analysis)
- Adds ~5-10 minutes to clustering time (parallel, not sequential)

## Testing
```python
# After clustering test123:
topics = await db.get_topics("test123")
print(topics[0]["semantic_summary"])  # Should show actual content understanding
```

## Files to Modify
1. `app/primitives/consolidation/clustering.py` — Add semantic analysis method
2. `app/primitives/database.py` — Add save_topics_with_semantics()
3. `app/api/consolidation.py` — Update topics endpoint (already returns all fields)
4. `app/ouroboros/promise_detector.py` — Enhance prompt with semantic data
5. Supabase migration — Add semantic columns
6. Frontend — Display semantic summaries in topics UI
