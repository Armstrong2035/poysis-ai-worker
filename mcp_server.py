#!/usr/bin/env python3
"""
Poysis MCP Server
Exposes the knowledge base API as an MCP server for Claude, ChatGPT, and other clients.
"""

import os
import sys
import json
import httpx
from typing import Any
from mcp.server import Server
from mcp.types import Tool, TextContent, ToolResult
from pydantic import BaseModel

# Configuration
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
API_KEY = os.getenv("POYSIS_API_KEY", "")  # User provides their API key

if not API_KEY:
    print("[ERROR] POYSIS_API_KEY environment variable not set", file=sys.stderr)
    sys.exit(1)

# Initialize MCP server
server = Server("poysis-knowledge-base")


class QueryRequest(BaseModel):
    workspace_id: str
    query: str
    top_k: int = 5
    min_score: float = 0.5


class ListDocumentsRequest(BaseModel):
    workspace_id: str


async def call_worker(
    endpoint: str,
    method: str = "POST",
    data: dict = None,
    params: dict = None
) -> dict:
    """Make authenticated request to the Poysis Worker API."""
    url = f"{WORKER_URL}/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": API_KEY,  # API key acts as user ID for now
    }

    async with httpx.AsyncClient() as client:
        if method == "POST":
            response = await client.post(url, json=data, headers=headers)
        else:  # GET
            response = await client.get(url, params=params or {}, headers=headers)

        if response.status_code >= 400:
            raise Exception(f"Worker error: {response.status_code} {response.text}")

        return response.json()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="query_knowledge_base",
            description="""
Use this when the user is asking a QUESTION or SEARCHING BY MEANING/CONTENT.
This searches ACROSS all document content using semantic similarity.

WHEN TO USE:
- User asks a question: "What was decided about the budget?"
- User wants information by topic: "Find notes about performance reviews"
- User needs answers, not documents: "When is the deadline?"

EXAMPLES:
✓ "What did we decide about hiring?" → query_knowledge_base
✓ "Find information on project roadmap" → query_knowledge_base
✓ "Tell me about our Q4 strategy" → query_knowledge_base

DO NOT USE when user wants to find or browse documents by name/source.
            """,
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace_id": {
                        "type": "string",
                        "description": "Your workspace ID"
                    },
                    "query": {
                        "type": "string",
                        "description": "Question or search query (e.g., 'What are the project deadlines?')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5
                    },
                    "min_score": {
                        "type": "number",
                        "description": "Minimum relevance score 0-1 (default: 0.5). Lower = broader results.",
                        "default": 0.5
                    }
                },
                "required": ["workspace_id", "query"]
            }
        ),
        Tool(
            name="list_documents",
            description="""
Use this when the user wants to FIND or BROWSE DOCUMENTS by name, source, or type.
This searches BY DOCUMENT METADATA (title, where it came from), not content.

WHEN TO USE:
- User wants to browse/find specific documents: "Show me all my Notion docs"
- User asks for documents by name: "Do I have notes about the budget meeting?"
- User wants to filter by source: "List all my Google Drive files"

EXAMPLES:
✓ "Do I have a document about the budget meeting?" → list_documents with search="budget"
✓ "Show me all my Notion files" → list_documents with source_type="notion"
✓ "Find all documents with 'Q4' in the name" → list_documents with search="Q4"

USE query_knowledge_base if user wants ANSWERS/INFORMATION, not documents themselves.
            """,
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace_id": {
                        "type": "string",
                        "description": "Your workspace ID"
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional: filter by document title (case-insensitive). E.g., 'budget', 'meeting notes'"
                    },
                    "source_type": {
                        "type": "string",
                        "description": "Optional: filter by source. Options: 'google_drive', 'notion', 'gmail'"
                    }
                },
                "required": ["workspace_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ToolResult]:
    """Execute a tool."""
    try:
        if name == "query_knowledge_base":
            result = await call_worker(
                endpoint="retrieval/query_knowledge_base",
                method="POST",
                data=arguments
            )

            # Format results for readability
            text = f"Found {result['total']} results for: {arguments['query']}\n\n"
            for i, r in enumerate(result['results'], 1):
                source = r.get('source', {})
                text += f"{i}. {source.get('title', 'Unknown')} (Score: {r['score']})\n"
                text += f"   Source: {source.get('source_type')} - {source.get('url', 'N/A')}\n"
                text += f"   {r.get('text', '')[:200]}...\n\n"

            return [TextContent(type="text", text=text)]

        elif name == "list_documents":
            params = {"workspace_id": arguments["workspace_id"]}
            if "search" in arguments:
                params["search"] = arguments["search"]
            if "source_type" in arguments:
                params["source_type"] = arguments["source_type"]

            result = await call_worker(
                endpoint="retrieval/list_documents",
                method="GET",
                params=params
            )

            # Format documents for readability
            text = f"Found {result['total']} documents"
            if result.get("search_query"):
                text += f" matching '{result['search_query']}'"
            if result.get("source_filter"):
                text += f" from {result['source_filter']}"
            text += ":\n\n"

            if not result['documents']:
                text += "No documents found."
            else:
                for doc in result['documents']:
                    text += f"📄 {doc.get('title', 'Untitled')}\n"
                    text += f"   Source: {doc.get('source_type', 'Unknown')}\n"
                    text += f"   URL: {doc.get('url', 'N/A')}\n"
                    text += f"   Preview: {doc.get('snippet', 'No preview')[:150]}...\n\n"

            return [TextContent(type="text", text=text)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


if __name__ == "__main__":
    # Run with: mcp run python mcp_server.py
    import asyncio
    asyncio.run(server.run())
