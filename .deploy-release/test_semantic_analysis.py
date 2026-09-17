#!/usr/bin/env python3
"""Test semantic analysis integration."""
import asyncio
import json
from app.primitives.consolidation.categorizer import CategorizerEngine
from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService

async def test_semantic_analysis():
    db = DatabaseService()
    vector_service = VectorService()

    # Test with existing test123 workspace
    namespace = "consolidation_test123"

    print("[TEST] Fetching documents from test123...")
    docs = await asyncio.to_thread(
        vector_service.list_documents_with_snippets,
        namespace
    )

    if not docs:
        print("[ERROR] No documents found in test123")
        return

    print(f"[TEST] Found {len(docs)} documents")

    # Create mock topics (just for testing semantic analysis)
    topics_data = [
        {
            "topic_id": 17,
            "label": "Video Script Series",
            "keywords": [],
            "doc_count": 5,
            "parent_topic_id": None,
        },
        {
            "topic_id": 29,
            "label": "AI Tech & Research",
            "keywords": [],
            "doc_count": 3,
            "parent_topic_id": None,
        }
    ]

    # Build source_to_cat mapping (simplified)
    source_to_cat = {}
    video_docs = [d for d in docs if "video" in d.get("title", "").lower()][:5]
    ai_docs = [d for d in docs if "ai" in d.get("title", "").lower()][:3]

    for d in video_docs:
        source_to_cat[d["source_id"]] = {"id": 17, "label": "Video Script Series"}
    for d in ai_docs:
        source_to_cat[d["source_id"]] = {"id": 29, "label": "AI Tech & Research"}

    print(f"[TEST] Mapped {len(source_to_cat)} documents to topics")

    # Get model and run semantic analysis
    engine = CategorizerEngine(db, vector_service)
    model = engine._get_model()

    print("[TEST] Running semantic analysis...")
    semantic_data = await engine._analyze_semantic_content(
        docs,
        topics_data,
        source_to_cat,
        model
    )

    print("\n[RESULTS] Semantic Analysis Output:")
    for topic_id, analysis in semantic_data.items():
        print(f"\nTopic {topic_id}:")
        print(f"  Summary: {analysis.get('semantic_summary', 'N/A')}")
        print(f"  Themes: {analysis.get('key_themes', [])}")
        print(f"  Use cases: {analysis.get('suggested_use_cases', [])}")

if __name__ == "__main__":
    asyncio.run(test_semantic_analysis())
