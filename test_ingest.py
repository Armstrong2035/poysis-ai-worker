"""
Quick smoke test for spreadsheet ingestion.
Run with: python test_ingest.py

Requires the server to be running on localhost:8000.
"""

import requests
import csv
import os
import tempfile
import json

BASE_URL = "http://localhost:8000"
NOTEBOOK_ID = "test-spreadsheet-ingestion"

# --- 1. Create a small sample CSV ---
SAMPLE_ROWS = [
    {"product_name": "Widget A", "price": "9.99", "category": "Tools", "in_stock": "true"},
    {"product_name": "Gadget B", "price": "24.50", "category": "Electronics", "in_stock": "false"},
    {"product_name": "Doohickey C", "price": "4.75", "category": "Tools", "in_stock": "true"},
]

def create_sample_csv() -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", newline="")
    writer = csv.DictWriter(tmp, fieldnames=SAMPLE_ROWS[0].keys())
    writer.writeheader()
    writer.writerows(SAMPLE_ROWS)
    tmp.close()
    return tmp.name


def test_ingest(csv_path: str):
    print(f"\n[1] Posting '{csv_path}' to /retrieval/ingest-file ...")
    with open(csv_path, "rb") as f:
        res = requests.post(
            f"{BASE_URL}/retrieval/ingest-file",
            data={"notebook_id": NOTEBOOK_ID},
            files={"file": ("sample.csv", f, "text/csv")},
        )

    print(f"    Status: {res.status_code}")
    if res.status_code != 200:
        print(f"    ERROR: {res.text}")
        return False

    body = res.json()
    print(f"    Response: {json.dumps(body, indent=2)}")
    vectors = body.get("vectors_indexed", 0)
    print(f"    Vectors indexed: {vectors}")

    if vectors == 0:
        print("    FAIL: 0 vectors indexed.")
        return False

    print("    PASS: ingestion returned vectors > 0")
    return True


def test_search():
    print(f"\n[2] Searching for 'tools' in notebook '{NOTEBOOK_ID}' ...")
    res = requests.post(
        f"{BASE_URL}/retrieval/search",
        json={"notebook_id": NOTEBOOK_ID, "query": "tools category", "limit": 5, "min_score": 0.3},
    )

    print(f"    Status: {res.status_code}")
    if res.status_code != 200:
        print(f"    ERROR: {res.text}")
        return False

    body = res.json()
    results = body.get("results", [])
    print(f"    Results returned: {len(results)}")

    if not results:
        print("    FAIL: no results returned.")
        return False

    print("\n    Checking metadata on first result:")
    first = results[0]
    metadata = first.get("metadata", {})
    text = first.get("text", "")

    print(f"      text  : {text}")
    print(f"      metadata keys: {list(metadata.keys())}")

    # Key check: metadata should have column fields, not just node_content
    expected_fields = {"product_name", "price", "category", "in_stock"}
    found_fields = expected_fields & set(metadata.keys())
    if found_fields:
        print(f"    PASS: column fields found in metadata: {found_fields}")
        return True
    else:
        print(f"    FAIL: column fields NOT in metadata. Got: {list(metadata.keys())}")
        print("    This means the old LlamaIndex path is still being used.")
        return False


if __name__ == "__main__":
    csv_path = create_sample_csv()
    try:
        ok = test_ingest(csv_path)
        if ok:
            test_search()
    finally:
        os.remove(csv_path)
        print(f"\n[cleanup] Temp file removed.")