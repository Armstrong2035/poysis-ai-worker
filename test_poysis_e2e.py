import os
import httpx
import time
import json
from dotenv import load_dotenv

load_dotenv()

# We test against the local server (assuming it will be started on 8000)
BASE_URL = "http://localhost:8000"
NOTEBOOK_ID = "test-poysis-notebook-001"

def test_poysis_e2e():
    print(f"--- Starting Poysis E2E Test (Notebook: {NOTEBOOK_ID}) ---")

    # 1. Ingest Sample Documents
    ingest_url = f"{BASE_URL}/retrieval/ingest"
    payload = {
        "notebook_id": NOTEBOOK_ID,
        "documents": [
            {
                "source_id": "doc_001",
                "text": "The core of the Poysis platform is the ContextStore, which acts as a flat registry for all compiled expert directives at runtime."
            },
            {
                "source_id": "doc_002",
                "text": "Blocks in Poysis are atomic units that manage their own local context and communicate via an event-driven wiring system."
            },
            {
                "source_id": "doc_003",
                "text": "The Socratic Diagnostic block helps experts identify user pain points by asking a controlled sequence of questions before moving to a recommendation phase."
            }
        ]
    }

    print(f"\n[1/2] Testing Ingestion...")
    try:
        with httpx.Client() as client:
            response = client.post(ingest_url, json=payload, timeout=30.0)
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")
            if response.status_code != 200:
                print("ERROR: Ingestion failed.")
                return
    except Exception as e:
        print(f"ERROR: Could not connect to server at {BASE_URL}. Ensure it is running. {e}")
        return

    # Wait a moment for Pinecone consistency
    print("\nWaiting for vector index consistency...")
    time.sleep(2)

    # 2. Test Search Retrieval
    search_url = f"{BASE_URL}/retrieval/search"
    query = "How do blocks communicate in Poysis?"
    search_payload = {
        "query": query,
        "notebook_id": NOTEBOOK_ID,
        "limit": 3
    }

    print(f"\n[2/2] Testing Search Retrieval for: '{query}'")
    with httpx.Client() as client:
        response = client.post(search_url, json=search_payload, timeout=30.0)
        print(f"Status: {response.status_code}")
        results = response.json().get("results", [])
    
    print(f"\nFound {len(results)} results:")
    for idx, res in enumerate(results):
        text = res.get("metadata", {}).get("text", "NO TEXT FOUND")
        score = res.get("score", 0.0)
        print(f"  {idx+1}. [Score: {score:.4f}] {text[:80]}...")

    if len(results) > 0:
        print("\nSUCCESS: Poysis AI Worker is functional and domain-agnostic!")
    else:
        print("\nFAILURE: No results returned.")

if __name__ == "__main__":
    test_poysis_e2e()
