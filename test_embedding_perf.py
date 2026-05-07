"""
Embedding performance diagnostic.
Runs discover + snapshot, cross-references file sizes with outcome,
then pulls Railway logs to surface per-file timing.
"""
import httpx
import json
import time

BASE_URL = "https://poysis-ai-worker-production.up.railway.app"
WORKSPACE_ID = "test123"
TIME_WINDOW_DAYS = 365


def run():
    client = httpx.Client(timeout=60)

    # 1. Discover — capture file sizes upfront
    print("=" * 60)
    print("DISCOVER")
    print("=" * 60)
    resp = client.post(f"{BASE_URL}/consolidation/discover", json={
        "workspace_id": WORKSPACE_ID,
        "time_window_days": TIME_WINDOW_DAYS,
    })
    discover = resp.json()
    print(f"Total files : {discover['total_files']}")
    print(f"Total size  : {discover['total_size_mb']} MB")
    print(f"Breakdown   : {discover['breakdown']}")
    if discover.get("large_files"):
        print(f"\nLarge file warnings ({len(discover['large_files'])}):")
        for f in discover["large_files"]:
            print(f"  {f['size_mb']}MB  {f['title']}")

    # 2. Kick off snapshot — record wall-clock start time
    print("\n" + "=" * 60)
    print("SNAPSHOT START")
    print("=" * 60)
    resp = client.post(f"{BASE_URL}/consolidation/snapshot", json={
        "workspace_id": WORKSPACE_ID,
        "time_window_days": TIME_WINDOW_DAYS,
    })
    print(f"Status: {resp.status_code}  {resp.json()}")
    wall_start = time.perf_counter()

    # 3. Poll until done, printing live updates
    print("\nPolling...")
    prev_docs = 0
    prev_vectors = 0
    for attempt in range(80):
        time.sleep(15)
        s = client.get(f"{BASE_URL}/consolidation/snapshot/status/{WORKSPACE_ID}").json()
        elapsed = time.perf_counter() - wall_start
        docs = s.get("docs_processed", 0)
        vectors = s.get("vectors_indexed", 0)
        skipped = s.get("docs_skipped", 0)
        orphaned = s.get("docs_orphaned", 0)
        new_docs = docs - prev_docs
        new_vectors = vectors - prev_vectors
        prev_docs, prev_vectors = docs, vectors
        print(
            f"  [{elapsed:6.0f}s] status={s['status']:<8} "
            f"docs={docs} (+{new_docs})  vectors={vectors} (+{new_vectors})  "
            f"skipped={skipped}  orphaned={orphaned}"
        )
        if s["status"] in ("done", "failed"):
            break

    # 4. Final summary
    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    final = client.get(f"{BASE_URL}/consolidation/snapshot/status/{WORKSPACE_ID}").json()
    print(json.dumps(final, indent=2))

    total_secs = time.perf_counter() - wall_start
    vectors = final.get("vectors_indexed", 0)
    print(f"\nTotal wall time : {total_secs:.1f}s")
    if vectors and total_secs:
        print(f"Throughput      : {vectors / total_secs:.1f} vectors/s")

    if final.get("errors"):
        print(f"\nFailed files ({len(final['errors'])}):")
        for e in final["errors"]:
            print(f"  {e}")


if __name__ == "__main__":
    run()
