import httpx
import json
import time

BASE_URL = "https://poysis-ai-worker-production.up.railway.app"
WORKSPACE_ID = "test123"

# 365-day test — poll for up to 90 minutes, every 20s
POLL_INTERVAL = 20
POLL_MAX = 270  # 270 × 20s = 90 minutes


def print_response(label: str, response: httpx.Response):
    print(f"\n{'='*50}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)
    print('='*50)


def test_discover():
    resp = httpx.post(
        f"{BASE_URL}/consolidation/discover",
        json={"workspace_id": WORKSPACE_ID, "time_window_days": 365},
        timeout=60,
    )
    print_response("DISCOVER", resp)
    return resp.status_code == 200


def test_snapshot():
    # Kick off background job — returns immediately
    resp = httpx.post(
        f"{BASE_URL}/consolidation/snapshot",
        json={"workspace_id": WORKSPACE_ID, "time_window_days": 365},
        timeout=30,
    )
    print_response("SNAPSHOT START", resp)
    if resp.status_code != 200:
        return False

    print(f"\nPolling for completion (every {POLL_INTERVAL}s, max {POLL_MAX * POLL_INTERVAL // 60} min)...")
    start = time.time()
    for _ in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        try:
            status_resp = httpx.get(
                f"{BASE_URL}/consolidation/snapshot/status/{WORKSPACE_ID}",
                timeout=30,
            )
            data = status_resp.json()
            status = data.get("status")
            vectors = data.get("vectors_indexed", "?")
            docs = data.get("docs_processed", "?")
            skipped = data.get("docs_skipped", "?")
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] status={status}  docs={docs}  skipped={skipped}  vectors={vectors}")
            if status in ("done", "failed"):
                print_response("FINAL RESULT", status_resp)
                return status == "done"
        except httpx.TimeoutException:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] poll timed out — retrying...")

    print("Timed out waiting for snapshot.")
    return False


if __name__ == "__main__":
    print("Running consolidation tests...\n")

    print("1. Testing discover...")
    discover_ok = test_discover()

    if discover_ok:
        print("\n2. Running snapshot (background job)...")
        test_snapshot()
    else:
        print("\nDiscover failed — skipping snapshot.")
