#!/usr/bin/env python3
"""Test Ouroboros with story context."""
import asyncio
import json
import os
from app.primitives.database import DatabaseService

async def main():
    db = DatabaseService()
    workspace_id = "test123"

    # Fetch topics and stories
    topics = await db.get_topics(workspace_id)
    stories = await db.get_stories(workspace_id)

    print(f"Knowledge base summary:")
    print(f"  Topics: {len(topics)}")
    print(f"  Stories: {len(stories)}\n")

    # Build topic text
    topic_text = "\n".join([
        f"- {t['label']}: {t['doc_count']} documents"
        + (f"\n  About: {t.get('semantic_summary', '')}" if t.get('semantic_summary') else "")
        for t in topics[:10]
    ])

    # Build story text
    story_text = ""
    if stories:
        story_text = "\n\nNarrative threads:\n" + "\n".join([
            f"- {s['title']}: {s['description']} (strength: {s['strength']})"
            for s in stories[:5]
        ])

    # Show what Ouroboros sees
    print("=" * 60)
    print("OUROBOROS CONTEXT (Topics + Stories)")
    print("=" * 60)
    print(topic_text[:500])
    print(story_text[:500])
    print("\n[Ouroboros now calls Gemini with both topics AND narratives]")

asyncio.run(main())
