"""
test_chat_endpoints.py

Tests the new chat and MCP endpoints introduced in the current branch.
Runs at two levels:
  1. Function-level (no server needed) — verifies retrieval, scoping, diversity, MCP list_topics
  2. HTTP-level (requires `uvicorn main:app` running on :8000) — verifies streaming /chat

Usage:
    # Function-level tests only (fast):
    python test_chat_endpoints.py <workspace_id>

    # Include HTTP streaming test (start server first):
    uvicorn main:app --port 8000 &
    python test_chat_endpoints.py <workspace_id> --http

Workspace ID from seed_youtube.py if not provided:
    python test_chat_endpoints.py 98b8d281-72c9-44d8-9407-68af84411733
"""
import asyncio
import json
import sys
import os

from dotenv import load_dotenv
load_dotenv()

WORKSPACE_ID = sys.argv[1] if len(sys.argv) > 1 else "98b8d281-72c9-44d8-9407-68af84411733"
HTTP_MODE = "--http" in sys.argv
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
USER_ID = os.getenv("TEST_USER_ID", "")  # set in .env or pass via env


def header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def ok(msg: str):
    print(f"  ✓  {msg}")


def fail(msg: str):
    print(f"  ✗  {msg}")


# ---------------------------------------------------------------------------
# Test 1: basic retrieval from consolidation namespace
# ---------------------------------------------------------------------------
async def test_basic_retrieval():
    header("1. Basic retrieval — consolidation namespace")
    from app.primitives.knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine()
    namespace = f"consolidation_{WORKSPACE_ID}"

    results = await engine.fetch_raw(namespace, "grace and salvation", top_k=10)
    if results:
        ok(f"{len(results)} chunks returned")
        for r in results[:3]:
            meta = r.get("metadata", {})
            print(f"     score={r['score']:.3f} | {meta.get('title','?')[:60]} | source_type={meta.get('source_type','?')}")
    else:
        fail("No results — is the workspace indexed?")
    return bool(results)


# ---------------------------------------------------------------------------
# Test 2: source_type scoping (allowed_connection_ids)
# ---------------------------------------------------------------------------
async def test_source_type_filter():
    header("2. Connection-level scoping — source_types=['youtube']")
    from app.primitives.knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine()
    namespace = f"consolidation_{WORKSPACE_ID}"

    all_results = await engine.fetch_raw(namespace, "grace and salvation", top_k=10)
    youtube_results = await engine.fetch_raw(namespace, "grace and salvation", top_k=10, source_types=["youtube"])

    all_types = {r["metadata"].get("source_type") for r in all_results}
    yt_types = {r["metadata"].get("source_type") for r in youtube_results}

    ok(f"Unscoped returned source_types: {all_types}")
    ok(f"YouTube-scoped returned source_types: {yt_types}")

    if yt_types and yt_types <= {"youtube"}:
        ok("Filter working — only YouTube chunks returned")
    elif not youtube_results:
        fail("No YouTube results — either no YouTube data or filter broken")
    else:
        fail(f"Unexpected source_types in scoped results: {yt_types}")


# ---------------------------------------------------------------------------
# Test 3: topic_ids scoping (allowed_topic_ids)
# ---------------------------------------------------------------------------
async def test_topic_filter():
    header("3. Topic-level scoping — allowed_topic_ids")
    from app.primitives.database import DatabaseService
    from app.primitives.knowledge.engine import KnowledgeEngine

    db = DatabaseService()
    topics = await db.get_topics(WORKSPACE_ID)
    if not topics:
        fail("No topics found — run clustering first")
        return

    ok(f"{len(topics)} topics available")
    for t in topics[:4]:
        print(f"     [{t['topic_id']}] {t['label']} — {t['doc_count']} docs")

    # Filter to first topic only
    first_id = topics[0]["topic_id"]
    engine = KnowledgeEngine()
    namespace = f"consolidation_{WORKSPACE_ID}"

    scoped = await engine.fetch_raw(namespace, "sermon", top_k=10, topic_ids=[first_id])
    if scoped:
        returned_cats = {r["metadata"].get("category_id") for r in scoped}
        ok(f"topic_ids=[{first_id}] returned category_ids: {returned_cats}")
        if returned_cats <= {first_id}:
            ok("Topic filter working correctly")
        else:
            fail(f"Chunks from other topics leaked through: {returned_cats - {first_id}}")
    else:
        ok(f"No results for topic {first_id} (may be expected if topic has no vectors matching 'sermon')")


# ---------------------------------------------------------------------------
# Test 4: source diversity
# ---------------------------------------------------------------------------
async def test_diversity():
    header("4. Source diversity — round-robin across videos")
    from app.primitives.knowledge.engine import KnowledgeEngine
    from app.api.chat import _diversify

    engine = KnowledgeEngine()
    namespace = f"consolidation_{WORKSPACE_ID}"

    candidates = await engine.fetch_raw(namespace, "faith", top_k=30, source_types=["youtube"])
    if not candidates:
        fail("No YouTube chunks to test diversity — skip")
        return

    diverse = _diversify(candidates, top_k=5)
    source_ids = [c["metadata"].get("source_id") for c in diverse]
    unique_sources = len(set(source_ids))

    ok(f"5 results from {unique_sources} distinct video(s)")
    for c in diverse:
        meta = c.get("metadata", {})
        print(f"     {meta.get('source_id','?')[:12]}… | {meta.get('title','?')[:50]}")

    if unique_sources > 1:
        ok("Diversity working — results span multiple videos")
    elif len(candidates) > 0:
        ok("Only 1 video in the indexed data — diversity not testable yet")


# ---------------------------------------------------------------------------
# Test 5: MCP list_topics
# ---------------------------------------------------------------------------
async def test_mcp_list_topics():
    header("5. MCP — list_topics tool")
    from app.api.mcp_http import _tool_list_topics

    result = await _tool_list_topics(WORKSPACE_ID)
    content = result.get("content", [{}])[0].get("text", "")

    if "topic cluster" in content.lower() or "No topics" in content:
        ok("list_topics returned valid output")
        print()
        # Print first 800 chars of output
        print(content[:800])
        if len(content) > 800:
            print("  …(truncated)")
    else:
        fail(f"Unexpected output: {content[:200]}")


# ---------------------------------------------------------------------------
# Test 6: MCP list_documents with source_type filter
# ---------------------------------------------------------------------------
async def test_mcp_list_documents():
    header("6. MCP — list_documents filtered by source_type=youtube")
    from app.api.mcp_http import _tool_list_documents

    result = await _tool_list_documents(WORKSPACE_ID, {"source_type": "youtube"})
    content = result.get("content", [{}])[0].get("text", "")

    if "document" in content.lower() or "No documents" in content:
        ok("list_documents returned valid output")
        print(content[:600])
    else:
        fail(f"Unexpected output: {content[:200]}")


# ---------------------------------------------------------------------------
# Test 7: HTTP streaming /chat (requires server running)
# ---------------------------------------------------------------------------
async def test_http_chat():
    header("7. HTTP — POST /chat (streaming)")
    if not USER_ID:
        fail("TEST_USER_ID not set in .env — skipping HTTP test")
        print("     Set TEST_USER_ID=<your_supabase_uid> in .env to enable")
        return

    try:
        import httpx
    except ImportError:
        fail("httpx not installed — run: pip install httpx")
        return

    payload = {
        "workspace_id": WORKSPACE_ID,
        "query": "what has been preached about grace?",
        "top_k": 3,
        "allowed_connection_ids": ["youtube"],
    }

    print(f"  Hitting {WORKER_URL}/chat ...")
    full_text = ""
    sources = []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{WORKER_URL}/chat",
                json=payload,
                headers={"X-User-ID": USER_ID},
            ) as resp:
                if resp.status_code != 200:
                    fail(f"HTTP {resp.status_code}")
                    print(f"     {await resp.aread()}")
                    return
                async for chunk in resp.aiter_text():
                    if "__SOURCES__" in chunk:
                        parts = chunk.split("__SOURCES__", 1)
                        full_text += parts[0]
                        try:
                            sources = json.loads(parts[1])
                        except Exception:
                            pass
                    else:
                        full_text += chunk

        ok(f"Stream completed — {len(full_text)} chars, {len(sources)} source(s)")
        print(f"\n  Answer preview:\n  {full_text[:400].strip()}")
        if sources:
            print(f"\n  Sources:")
            for s in sources:
                ts = s.get("start_time", "")
                print(f"     • {s.get('title','?')[:50]}" + (f" @ {ts}" if ts else "") + f" (score {s.get('score',0):.3f})")
        else:
            print("  No sources returned")

    except Exception as e:
        fail(f"Request failed: {e}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def main():
    print(f"\nWorkspace: {WORKSPACE_ID}")
    print(f"HTTP tests: {'enabled' if HTTP_MODE else 'disabled (pass --http to enable)'}")

    await test_basic_retrieval()
    await test_source_type_filter()
    await test_topic_filter()
    await test_diversity()
    await test_mcp_list_topics()
    await test_mcp_list_documents()

    if HTTP_MODE:
        await test_http_chat()

    print(f"\n{'='*60}")
    print("  Done.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
