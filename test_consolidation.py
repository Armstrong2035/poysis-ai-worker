import httpx
import json
import time

BASE_URL = "https://poysis-ai-worker-production.up.railway.app"
WORKSPACE_ID = "test123"


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
        json={"workspace_id": WORKSPACE_ID},
        timeout=60,
    )
    print_response("DISCOVER", resp)
    return resp.status_code == 200


def test_snapshot():
    # Kick off background job — returns immediately
    resp = httpx.post(
        f"{BASE_URL}/consolidation/snapshot",
        json={"workspace_id": WORKSPACE_ID},
        timeout=30,
    )
    print_response("SNAPSHOT START", resp)
    if resp.status_code != 200:
        return False

    # Poll for completion
    print("\nPolling for completion (every 15s)...")
    for attempt in range(40):
        time.sleep(15)
        status_resp = httpx.get(
            f"{BASE_URL}/consolidation/snapshot/status/{WORKSPACE_ID}",
            timeout=15,
        )
        data = status_resp.json()
        status = data.get("status")
        vectors = data.get("vectors_indexed", "?")
        docs = data.get("docs_processed", "?")
        print(f"  [{attempt+1}] status={status}  docs={docs}  vectors={vectors}")
        if status in ("done", "failed"):
            print_response("FINAL RESULT", status_resp)
            return status == "done"

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
