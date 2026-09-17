#!/usr/bin/env python3
"""Test clustering with semantic analysis and story detection."""
import asyncio
import json
from app.primitives.consolidation.clustering import ClusteringEngine
from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService

async def main():
    db = DatabaseService()
    engine = ClusteringEngine(db)

    workspace_id = "test123"

    print("[TEST] Running clustering with semantic analysis + story detection...\n")

    result = await engine.run_clustering(workspace_id)

    print(f"\n[RESULT] Clustering complete:")
    print(json.dumps(result, indent=2))

    # Fetch what was created
    print("\n[CHECKING DATABASE]")

    topics = await db.get_topics(workspace_id)
    stories = await db.get_stories(workspace_id)

    print(f"Topics created: {len(topics)}")
    if topics:
        print(f"  First topic: {topics[0]['label']} ({topics[0]['doc_count']} docs)")
        if topics[0].get('semantic_summary'):
            print(f"  Semantic: {topics[0]['semantic_summary'][:80]}...")

    print(f"\nStories detected: {len(stories)}")
    if stories:
        for story in stories[:3]:
            print(f"  - {story['title']}")
            print(f"    Strength: {story['strength']}, Docs: {story['doc_count']}")
            print(f"    Topics: {story['topic_sequence']}")

asyncio.run(main())
