"""
MCP server for Claude.ai Remote Connectors and Claude Desktop.

Speaks JSON-RPC 2.0 over HTTP per the MCP Streamable HTTP transport spec.
Each workspace gets its own URL: POST /mcp/{workspace_id}

Implemented methods:
- initialize          → handshake, capabilities
- tools/list          → returns available tools
- tools/call          → invokes a tool
- notifications/initialized → noop ack (client tells us setup is done)

Tools:
- retrieve_from_knowledge_base — semantic search over the workspace's vectors
- list_documents               — list/filter indexed documents
"""

import traceback
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.primitives.database import DatabaseService
from app.primitives.knowledge.engine import KnowledgeEngine
from app.primitives.knowledge.vector_store import VectorService

router = APIRouter(prefix="/mcp", tags=["mcp"])
db = DatabaseService()

# Match the MCP spec version we're targeting.
PROTOCOL_VERSION = "2025-03-26"

SERVER_INFO = {
    "name": "poysis-knowledge",
    "version": "1.0.0",
}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "retrieve_from_knowledge_base",
        "description": (
            "Search the user's consolidated knowledge base using semantic search. "
            "Returns the most relevant chunks of text with their source documents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Use the user's natural-language question.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many results to return. Default 5.",
                    "default": 5,
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum relevance score (0–1). Default 0.5.",
                    "default": 0.5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List documents in the user's knowledge base. Optionally filter by title or source type. "
            "Returns titles, URLs, and short snippets — no full text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Case-insensitive substring match on document title.",
                },
                "source_type": {
                    "type": "string",
                    "description": "Filter by source type, e.g. 'youtube', 'google_drive', 'notion'.",
                },
            },
        },
    },
    {
        "name": "list_topics",
        "description": (
            "List the AI-generated topic clusters in the user's knowledge base. "
            "Each topic has a label, document count, semantic summary, and key themes. "
            "Use this to understand how the knowledge base is organised before retrieving content "
            "or helping the owner decide which topics to expose in a shared playground."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _jsonrpc_result(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Optional[Any] = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_content(text: str, is_error: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        payload["isError"] = True
    return payload


async def _validate_workspace(workspace_id: str) -> None:
    if not workspace_id or not workspace_id.strip():
        raise HTTPException(status_code=400, detail="workspace_id required")
    topics = await db.get_topics(workspace_id)
    if not topics:
        raise HTTPException(
            status_code=404,
            detail="Workspace not found or hasn't completed consolidation yet",
        )


async def _tool_retrieve(workspace_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return _tool_content("Missing required 'query' argument.", is_error=True)

    top_k = int(args.get("top_k", 5) or 5)
    min_score = float(args.get("min_score", 0.5) or 0.5)

    engine = KnowledgeEngine()
    namespace = f"consolidation_{workspace_id}"

    raw = await engine.fetch_raw(
        notebook_id=namespace,
        text=query,
        top_k=top_k * 2,  # over-fetch, then filter
    )
    filtered = [r for r in raw if r.get("score", 0) >= min_score][:top_k]

    if not filtered:
        return _tool_content(
            f"No results above relevance threshold {min_score} for query: {query!r}"
        )

    lines = [f"Found {len(filtered)} relevant result(s) for: {query!r}\n"]
    for i, r in enumerate(filtered, 1):
        meta = r.get("metadata", {}) or {}
        title = meta.get("title") or "(untitled)"
        url = meta.get("url") or ""
        source_type = meta.get("source_type") or "unknown"
        score = round(r.get("score", 0), 4)
        text = r.get("text") or meta.get("_text") or ""
        lines.append(
            f"[{i}] {title}  (score {score}, source: {source_type})\n"
            f"    URL: {url}\n"
            f"    {text.strip()}\n"
        )
    return _tool_content("\n".join(lines))


async def _tool_list_documents(workspace_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    search = (args.get("search") or "").strip() or None
    source_type = (args.get("source_type") or "").strip() or None

    namespace = f"consolidation_{workspace_id}"
    vector_service = VectorService()
    all_docs = vector_service.list_documents_with_snippets(
        namespace=namespace,
        snippet_words=80,
    )

    if search:
        s = search.lower()
        all_docs = [d for d in all_docs if s in (d.get("title") or "").lower()]
    if source_type:
        all_docs = [d for d in all_docs if d.get("source_type") == source_type]

    if not all_docs:
        return _tool_content("No documents found matching the filters.")

    lines = [f"{len(all_docs)} document(s):\n"]
    for d in all_docs[:50]:  # cap output size
        title = d.get("title") or "(untitled)"
        url = d.get("url") or ""
        st = d.get("source_type") or "unknown"
        snippet = (d.get("snippet") or "").strip()
        lines.append(f"• {title}  ({st})\n  {url}\n  {snippet[:200]}\n")
    if len(all_docs) > 50:
        lines.append(f"\n…and {len(all_docs) - 50} more (refine your filters to narrow down).")
    return _tool_content("\n".join(lines))


async def _tool_list_topics(workspace_id: str) -> Dict[str, Any]:
    topics = await db.get_topics(workspace_id)
    if not topics:
        return _tool_content("No topics found. Run consolidation first to generate topic clusters.")

    # Separate top-level and sub-topics for readable output
    top_level = [t for t in topics if not t.get("parent_topic_id")]
    sub_topics = [t for t in topics if t.get("parent_topic_id")]
    sub_by_parent: Dict[int, List] = {}
    for s in sub_topics:
        sub_by_parent.setdefault(s["parent_topic_id"], []).append(s)

    lines = [f"{len(top_level)} topic cluster(s) in this knowledge base:\n"]
    for t in top_level:
        tid = t["topic_id"]
        label = t.get("label", "Untitled")
        count = t.get("doc_count", 0)
        summary = t.get("semantic_summary") or ""
        themes = t.get("key_themes") or []

        lines.append(f"• [{tid}] {label}  ({count} docs)")
        if summary:
            lines.append(f"  {summary}")
        if themes:
            lines.append(f"  Themes: {', '.join(themes)}")

        for s in sub_by_parent.get(tid, []):
            sid = s["topic_id"]
            slabel = s.get("label", "Untitled")
            scount = s.get("doc_count", 0)
            lines.append(f"    └─ [{sid}] {slabel}  ({scount} docs)")
        lines.append("")

    return _tool_content("\n".join(lines))


async def _dispatch(method: str, params: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        await _validate_workspace(workspace_id)
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name == "retrieve_from_knowledge_base":
            return await _tool_retrieve(workspace_id, args)
        if name == "list_documents":
            return await _tool_list_documents(workspace_id, args)
        if name == "list_topics":
            return await _tool_list_topics(workspace_id)
        raise HTTPException(status_code=400, detail=f"Unknown tool: {name}")
    # Common notifications — ack with no body.
    if method.startswith("notifications/"):
        return {}
    raise HTTPException(status_code=400, detail=f"Unsupported method: {method}")


@router.post("/{workspace_id}")
async def mcp_endpoint(workspace_id: str, request: Request):
    """JSON-RPC 2.0 entrypoint. One MCP server, scoped by URL path."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(None, -32700, "Parse error: invalid JSON"),
        )

    # Spec allows batched requests (array); handle both shapes.
    requests = body if isinstance(body, list) else [body]
    responses: List[Dict[str, Any]] = []

    for msg in requests:
        if not isinstance(msg, dict):
            responses.append(_jsonrpc_error(None, -32600, "Invalid request"))
            continue

        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if not method:
            responses.append(_jsonrpc_error(req_id, -32600, "Missing 'method'"))
            continue

        try:
            result = await _dispatch(method, params, workspace_id)
            # Notifications (no id) get no response.
            if req_id is None:
                continue
            responses.append(_jsonrpc_result(req_id, result))
        except HTTPException as e:
            responses.append(_jsonrpc_error(req_id, -32000, e.detail))
        except Exception as e:
            print(f"[MCP] Unhandled error in method={method}: {e}\n{traceback.format_exc()}")
            responses.append(_jsonrpc_error(req_id, -32603, "Internal error", str(e)))

    if not responses:
        # All were notifications — spec says respond with 202 No Content,
        # but most clients accept an empty 200.
        return JSONResponse(status_code=200, content=None)

    if isinstance(body, list):
        return JSONResponse(content=responses)
    return JSONResponse(content=responses[0])


@router.get("/{workspace_id}")
async def mcp_endpoint_info(workspace_id: str):
    """
    Browser/curl-friendly description. Real MCP traffic is POST.
    Useful for users sanity-checking their connector URL.
    """
    await _validate_workspace(workspace_id)
    return {
        "workspace_id": workspace_id,
        "protocol": "MCP (JSON-RPC 2.0 over HTTP)",
        "protocolVersion": PROTOCOL_VERSION,
        "tools": [t["name"] for t in TOOLS],
        "usage": "Register this URL in Claude.ai → Settings → Connectors, or in Claude Desktop config.",
    }
