#!/usr/bin/env python3
"""Manually add semantic data to test123 topics for testing."""
import asyncio
from app.primitives.database import DatabaseService

async def main():
    db = DatabaseService()

    if not db.client:
        print("[ERROR] Supabase client not initialized")
        return

    # Get current topics for test123
    topics = await db.get_topics("test123")

    print(f"[INFO] Found {len(topics)} topics in test123\n")

    # Add semantic data to first 3 topics
    updates = [
        {
            "topic_id": 17,
            "semantic_summary": "In-depth video critiques examining social media platforms, search engine design, and technological solutions with focus on societal impact.",
            "key_themes": ["social media critique", "search engine design", "tech solutions", "video analysis"],
            "suggested_use_cases": ["Q&A about social media and technology", "Research on platform design", "Tech critique resource"]
        },
        {
            "topic_id": 30,
            "semantic_summary": "Diverse reference materials covering multiple domains including general knowledge, business operations, and miscellaneous topics.",
            "key_themes": ["business operations", "reference materials", "general knowledge"],
            "suggested_use_cases": ["FAQ bot", "Quick reference lookup", "Knowledge base search"]
        },
        {
            "topic_id": 4,
            "semantic_summary": "Technical documentation and engineering content related to software development, architecture, and Poysis platform development.",
            "key_themes": ["software engineering", "platform development", "technical architecture"],
            "suggested_use_cases": ["Engineering knowledge base", "Technical Q&A", "Development resource"]
        }
    ]

    print("[INFO] Updating topics with semantic data...\n")

    for update in updates:
        topic_id = update["topic_id"]
        try:
            db.client.table("consolidation_topics").update({
                "semantic_summary": update["semantic_summary"],
                "key_themes": update["key_themes"],
                "suggested_use_cases": update["suggested_use_cases"]
            }).eq("workspace_id", "test123").eq("topic_id", topic_id).execute()

            print(f"✓ Updated topic {topic_id}")
            print(f"  Summary: {update['semantic_summary'][:60]}...")
            print()
        except Exception as e:
            print(f"✗ Error updating topic {topic_id}: {e}\n")

    print("[INFO] Semantic data added successfully!")

asyncio.run(main())
