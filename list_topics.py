from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(override=True)

from app.primitives.database import DatabaseService

WORKSPACE_ID = "test123"


async def main():
    db = DatabaseService()
    topics = await db.get_topics(WORKSPACE_ID)

    if not topics:
        print("No topics found. Run clustering first.")
        return

    topic_map = {t["topic_id"]: t for t in topics if t["topic_id"] >= 0}
    children = defaultdict(list)
    for t in topic_map.values():
        pid = t.get("parent_topic_id")
        if pid is not None:
            children[pid].append(t)

    roots = sorted(
        [t for t in topic_map.values() if t.get("parent_topic_id") is None],
        key=lambda t: t["doc_count"],
        reverse=True,
    )

    def print_node(t: dict, indent: int) -> None:
        prefix = "  " * indent + ("└─ " if indent else "")
        kw = ", ".join(t.get("keywords", [])[:5]) or "—"
        print(f"{prefix}[{t['topic_id']}] {t['label'][:70]}")
        print(f"{'  ' * indent}{'   ' if indent else ''}  docs: {t['doc_count']}  |  keywords: {kw}")
        for child in sorted(children.get(t["topic_id"], []), key=lambda c: c["doc_count"], reverse=True):
            print_node(child, indent + 1)

    print(f"{len(roots)} root topics, {len(topic_map)} total\n")
    for root in roots:
        print_node(root, 0)
        print()


import asyncio
asyncio.run(main())
