"""
test_youtube_query.py

Quick sanity check — semantic search over indexed YouTube transcripts.

Usage:
    python test_youtube_query.py <workspace_id> "your query here"
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()


async def main():
    if len(sys.argv) < 3:
        print('Usage: python test_youtube_query.py <workspace_id> "query"')
        sys.exit(1)

    workspace_id = sys.argv[1]
    query = sys.argv[2]
    top_k = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    from app.primitives.knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine()
    namespace = f"youtube_{workspace_id}"

    print(f"Querying namespace '{namespace}' for: {query!r}\n")
    results = await engine.fetch_raw(namespace, query, top_k=top_k)

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        print(f"[{i}] score={r['score']:.3f} | {meta.get('title', '(untitled)')}")
        print(f"     {meta.get('url', '')}")
        print(f"     {r['text'][:300].strip()}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
