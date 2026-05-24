"""
HTTP tunnel for MCP Cloud Connectors.

Claude.ai Cloud Connectors need HTTP endpoints. This module translates
HTTP requests from Claude into MCP tool calls against the retrieval API.

When a user adds https://poysis.ai/mcp?workspace_id=xxx as a Cloud Connector,
Claude calls this endpoint with tool names and parameters.
"""

from fastapi import APIRouter, Query, HTTPException
from app.primitives.database import DatabaseService
from app.primitives.knowledge.embedder import Embedder
from app.primitives.knowledge.vector_store import VectorService

router = APIRouter(prefix="/mcp", tags=["mcp"])
db = DatabaseService()


async def _validate_workspace(workspace_id: str) -> bool:
    """Verify workspace exists and has been set up (has consolidation data)."""
    if not workspace_id or workspace_id.strip() == "":
        raise HTTPException(status_code=400, detail="workspace_id required")

    try:
        topics = await db.get_topics(workspace_id)
        if not topics:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found or hasn't completed consolidation yet"
            )
        return True
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MCP] Error validating workspace: {e}")
        raise HTTPException(status_code=500, detail="Failed to validate workspace")


@router.get("/")
async def mcp_tools_list(workspace_id: str = Query(...)):
    """
    MCP tool discovery endpoint.

    Returns the list of available tools Claude can call.
    Called when Claude first connects to this MCP server.
    """
    await _validate_workspace(workspace_id)

    return {
        "tools": [
            {
                "name": "retrieve_from_knowledge_base",
                "description": "Search through consolidated knowledge using semantic search. Returns relevant documents with scores.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for in the knowledge base"
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default 5)",
                            "default": 5
                        },
                        "min_score": {
                            "type": "number",
                            "description": "Minimum relevance score (0-1, default 0.5)",
                            "default": 0.5
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "list_documents",
                "description": "Browse documents by title or source type. Returns metadata without full content.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Document title filter (optional)"
                        },
                        "source_type": {
                            "type": "string",
                            "description": "Filter by source (e.g., 'google_drive', 'notion')",
                            "enum": ["google_drive", "notion", "gmail", "slack"]
                        }
                    }
                }
            }
        ]
    }


@router.post("/tools/{tool_name}")
async def call_mcp_tool(
    tool_name: str,
    workspace_id: str = Query(...),
    **kwargs
):
    """
    MCP tool call endpoint.

    Claude calls this with:
    - tool_name: "retrieve_from_knowledge_base" or "list_documents"
    - workspace_id: the user's workspace
    - Additional params depend on the tool

    This endpoint routes to the appropriate retrieval logic.
    """
    await _validate_workspace(workspace_id)

    if tool_name == "retrieve_from_knowledge_base":
        query = kwargs.get("query")
        if not query:
            raise HTTPException(status_code=400, detail="query parameter required")

        top_k = kwargs.get("top_k", 5)
        min_score = kwargs.get("min_score", 0.5)

        try:
            embedder = Embedder()
            vector_service = VectorService()

            # Embed the query
            query_embedding = await embedder.get_embedding(
                query,
                task_type="retrieval_query"
            )

            # Search vectors in the workspace namespace
            raw_results = vector_service.query_vectors(
                query_embedding=query_embedding,
                namespace=workspace_id,
                top_k=top_k * 2,
            )

            # Filter by min_score
            filtered = [r for r in raw_results if r.get("score", 0) >= min_score]
            top_results = filtered[:top_k]

            # Format results with sources
            results = []
            for r in top_results:
                metadata = r.get("metadata", {})
                results.append({
                    "id": r["id"],
                    "score": round(r["score"], 4),
                    "text": metadata.get("_text", ""),
                    "source": {
                        "title": metadata.get("title"),
                        "url": metadata.get("url"),
                        "source_type": metadata.get("source_type"),
                        "source_id": metadata.get("source_id"),
                    }
                })

            return {
                "tool": tool_name,
                "query": query,
                "results": results,
                "total": len(results)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Retrieval error: {str(e)}")

    elif tool_name == "list_documents":
        search = kwargs.get("search")
        source_type = kwargs.get("source_type")

        try:
            vector_service = VectorService()
            all_docs = vector_service.list_documents_with_snippets(
                namespace=workspace_id,
                snippet_words=200
            )

            # Filter by search term (case-insensitive)
            if search:
                search_lower = search.lower()
                all_docs = [d for d in all_docs if search_lower in d.get("title", "").lower()]

            # Filter by source_type
            if source_type:
                all_docs = [d for d in all_docs if d.get("source_type") == source_type]

            return {
                "tool": tool_name,
                "documents": all_docs,
                "total": len(all_docs)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"List documents error: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")


@router.get("/endpoint-info")
async def mcp_endpoint_info(workspace_id: str = Query(...)):
    """
    Returns info about this MCP endpoint for debugging.
    """
    await _validate_workspace(workspace_id)

    return {
        "workspace_id": workspace_id,
        "endpoint": f"/mcp?workspace_id={workspace_id}",
        "platform": "Claude",
        "capabilities": ["retrieve_from_knowledge_base", "list_documents"],
        "info": "Add this URL to Claude.ai → Settings → Cloud Connectors"
    }
