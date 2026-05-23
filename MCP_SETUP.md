# Poysis MCP Server Setup

The Poysis MCP server lets you query your consolidated knowledge base from Claude, ChatGPT, and other MCP-compatible clients.

## Installation

1. **Install MCP library:**
   ```bash
   pip install mcp
   ```

2. **Set your API key:**
   ```bash
   export POYSIS_API_KEY="your-user-id-here"
   ```

3. **Start the server:**
   ```bash
   mcp run python mcp_server.py
   ```

   The server will listen on stdio by default.

## Configure in Claude Code

1. Open Claude Code settings
2. Add MCP server:
   ```json
   {
     "mcpServers": {
       "poysis": {
         "command": "python",
         "args": ["mcp_server.py"],
         "env": {
           "POYSIS_API_KEY": "your-user-id",
           "WORKER_URL": "http://localhost:8000"
         }
       }
     }
   }
   ```

3. Restart Claude Code

## Configure in Claude Desktop

1. Edit `~/.claude/settings.json`:
   ```json
   {
     "mcpServers": {
       "poysis": {
         "command": "python",
         "args": ["/path/to/mcp_server.py"],
         "env": {
           "POYSIS_API_KEY": "your-user-id",
           "WORKER_URL": "http://your-worker.railway.app"
         }
       }
     }
   }
   ```

2. Restart Claude Desktop

## Available Tools

### `query_knowledge_base`
Search your consolidated knowledge base.

**Inputs:**
- `workspace_id` (required): Your workspace ID
- `query` (required): Search query
- `top_k` (optional): Number of results (default: 5)
- `min_score` (optional): Minimum relevance score (default: 0.5)

**Returns:** Matching documents with sources (title, URL, source type)

### `list_documents`
Search documents in your knowledge base by name or source type.

**Inputs:**
- `workspace_id` (required): Your workspace ID
- `search` (optional): Filter by document title (case-insensitive)
- `source_type` (optional): Filter by source (e.g., "google_drive", "notion")

**Returns:** Document list with titles, sources, and text previews

**Examples:**
- Find all Google Drive documents: `source_type="google_drive"`
- Find documents with "budget" in the name: `search="budget"`
- Find budget documents from Notion: `search="budget"` + `source_type="notion"`

## Getting Your Workspace ID

Your workspace ID is shown in the Poysis dashboard. You can also find it in the URL when you're logged in:
```
https://poysis.app/workspace?id=YOUR_WORKSPACE_ID
```

## Environment Variables

- `POYSIS_API_KEY`: Your user ID (required) — acts as authentication for queries
- `WORKER_URL`: Worker backend URL (default: `http://localhost:8000`)

## Example Usage in Claude

```
I want to search my knowledge base for information about project timelines.
```

Claude will then use the `query_knowledge_base` tool to find matching documents and return them with sources.

## Troubleshooting

**"POYSIS_API_KEY not set"**
- Make sure you've set the environment variable before starting the server

**"Worker error: 401"**
- Your API key is invalid or the server is rejecting it

**"Connection refused"**
- Make sure the FastAPI worker is running on the configured WORKER_URL

**"Unknown tool"**
- Restart Claude Code/Desktop to refresh the tool definitions
