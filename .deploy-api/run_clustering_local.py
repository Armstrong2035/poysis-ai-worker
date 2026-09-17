import asyncio
import json
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv(override=True)

from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService
from app.primitives.consolidation.clustering import ClusteringEngine
from test_clustering import verify_clustering_result, verify_hierarchy

WORKSPACE_ID = "test123"


def print_topic_tree(topics: list) -> None:
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

    def _safe(s: str) -> str:
        return s.encode("ascii", "replace").decode("ascii")

    def print_node(t: dict, indent: int) -> None:
        prefix = "  " * indent + ("\\- " if indent else "")
        kw = ", ".join(t.get("keywords", [])[:4]) or "-"
        print(f"{prefix}[{t['topic_id']}] {_safe(t['label'][:60])}  ({t['doc_count']} docs)")
        if kw != "-":
            print(f"{'  ' * indent}   keywords: {_safe(kw)}")
        for child in sorted(children.get(t["topic_id"], []), key=lambda c: c["doc_count"], reverse=True):
            print_node(child, indent + 1)

    print(f"\n{'=' * 60}")
    print(f"TOPIC TREE  ({len(roots)} roots, {len(topic_map)} total)")
    print("=" * 60)
    for root in roots:
        print_node(root, 0)
        print()


def print_indexed_documents(namespace: str) -> None:
    vs = VectorService()
    docs = vs.list_documents(namespace)
    print(f"\n{'=' * 60}")
    print(f"INDEXED DOCUMENTS  ({len(docs)} files)")
    print("=" * 60)
    for d in docs:
        label = (d['title'] or d['source_id']).encode('ascii', 'replace').decode('ascii')
        print(f"  [{d['chunks']:3d} chunks]  {label}")
        if d["url"]:
            print(f"             {d['url']}")


async def main():
    db = DatabaseService()
    engine = ClusteringEngine(db=db)

    print_indexed_documents(f"consolidation_{WORKSPACE_ID}")
    print(f"\nRunning clustering for workspace '{WORKSPACE_ID}'...\n")
    result = await engine.run_clustering(WORKSPACE_ID)

    print("\n" + "=" * 50)
    print("RESULT")
    print(json.dumps({k: v for k, v in result.items() if k != "roots"}, indent=2))
    print("=" * 50)

    ok = verify_clustering_result(result)

    print("\nFetching stored topics...")
    topics = await db.get_topics(WORKSPACE_ID)
    print(f"{len(topics)} topics in DB.")

    verify_hierarchy(topics)
    print_topic_tree(topics)

    if not ok:
        print("\nNote: clustering assertions failed — see above.")


asyncio.run(main())
