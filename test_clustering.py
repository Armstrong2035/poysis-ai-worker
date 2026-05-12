import httpx
import json
import time

BASE_URL = "https://poysis-ai-worker-production.up.railway.app"
WORKSPACE_ID = "test123"

POLL_INTERVAL = 15
POLL_MAX = 40  # 40 × 15s = 10 minutes max


def print_response(label: str, response: httpx.Response):
    print(f"\n{'='*50}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)
    print("=" * 50)


def verify_clustering_result(data: dict) -> bool:
    status = data.get("status")
    documents_categorized = data.get("documents_categorized", 0)
    categories = data.get("categories", 0)
    total_topics = data.get("total_topics", 0)

    checks = [
        ("status == done", status == "done"),
        ("documents_categorized > 0", documents_categorized > 0),
        ("categories > 0", categories > 0),
        ("total_topics >= categories", total_topics >= categories),
    ]

    print("\n--- Clustering result assertions ---")
    passed = True
    for name, result in checks:
        print(f"  [{'PASS' if result else 'FAIL'}] {name}")
        if not result:
            passed = False
    return passed


def verify_hierarchy(topics: list) -> bool:
    topic_map = {t["topic_id"]: t for t in topics if t["topic_id"] >= 0}
    roots = {tid for tid, t in topic_map.items() if t.get("parent_topic_id") is None}
    non_roots = [t for t in topic_map.values() if t.get("parent_topic_id") is not None]

    all_parents_exist = all(t["parent_topic_id"] in topic_map for t in non_roots)

    def reaches_root(topic_id: int, depth: int = 0) -> bool:
        if depth > 20:
            return False  # cycle guard
        t = topic_map.get(topic_id)
        if t is None:
            return False
        if t.get("parent_topic_id") is None:
            return True
        return reaches_root(t["parent_topic_id"], depth + 1)

    chain_valid = all(reaches_root(tid) for tid in topic_map)

    checks = [
        ("has root topics (parent_topic_id=None)", len(roots) > 0),
        ("has non-root topics", len(non_roots) > 0),
        ("all parent_topic_ids exist in topic set", all_parents_exist),
        ("all topics chain to a root without cycles", chain_valid),
    ]

    print("\n--- Hierarchy assertions ---")
    passed = True
    for name, result in checks:
        print(f"  [{'PASS' if result else 'FAIL'}] {name}")
        if not result:
            passed = False

    if passed:
        depth_counts: dict = {}
        for tid in topic_map:
            d = 0
            cur = tid
            while topic_map.get(cur, {}).get("parent_topic_id") is not None:
                cur = topic_map[cur]["parent_topic_id"]
                d += 1
            depth_counts[d] = depth_counts.get(d, 0) + 1
        print(f"  [INFO] topics by depth: { {k: v for k, v in sorted(depth_counts.items())} }")

    return passed


def test_clustering():
    resp = httpx.post(
        f"{BASE_URL}/consolidation/cluster/{WORKSPACE_ID}",
        timeout=30,
    )
    print_response("CLUSTER START", resp)
    if resp.status_code != 200:
        return False, None

    print(f"\nPolling every {POLL_INTERVAL}s (max {POLL_MAX * POLL_INTERVAL // 60} min)...")
    start = time.time()
    for _ in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        try:
            status_resp = httpx.get(
                f"{BASE_URL}/consolidation/cluster/status/{WORKSPACE_ID}",
                timeout=30,
            )
            data = status_resp.json()
            status = data.get("status")
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] status={status}")
            if status in ("done", "failed", "skipped"):
                print_response("FINAL RESULT", status_resp)
                if status == "done":
                    return verify_clustering_result(data), data
                return False, data
        except httpx.TimeoutException:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] poll timed out — retrying...")

    print("Timed out waiting for clustering.")
    return False, None


def test_topics():
    resp = httpx.get(
        f"{BASE_URL}/consolidation/topics/{WORKSPACE_ID}",
        timeout=30,
    )
    print_response("TOPICS", resp)
    data = resp.json()
    topics = data.get("topics", [])
    print(f"\n{len(topics)} topics returned from DB.")
    return verify_hierarchy(topics)


if __name__ == "__main__":
    print("Running clustering test...\n")

    ok, _ = test_clustering()

    if ok:
        print("\nFetching stored topics...")
        hierarchy_ok = test_topics()
        if not hierarchy_ok:
            print("\nHierarchy verification failed.")
    else:
        print("\nClustering failed or skipped.")
