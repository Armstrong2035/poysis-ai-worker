# Poysis MCP — Production Deployment

This guide covers deploying the MCP server so real users can query from Claude, ChatGPT, etc.

## Option 1: Deploy as Standalone Service (Recommended)

The MCP server can run alongside your FastAPI worker, or as a separate service.

### On Railway (alongside worker)

1. **Add to `Procfile`:**
   ```
   web: gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
   mcp: python mcp_server.py
   ```

2. **Railway will run both processes.** The MCP server listens on stdio (standard MCP transport).

3. **Users configure it in Claude Code/Desktop:**
   ```json
   {
     "mcpServers": {
       "poysis": {
         "command": "ssh",
         "args": ["user@your-railway-domain.com", "python", "/app/mcp_server.py"],
         "env": {
           "POYSIS_API_KEY": "user-api-key"
         }
       }
     }
   }
   ```

### Or: Docker Container

1. **Create `Dockerfile.mcp`:**
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   COPY . .
   ENV PYTHONUNBUFFERED=1
   CMD ["python", "mcp_server.py"]
   ```

2. **Deploy to any container service (Railway, Fly, etc.)**

3. **Users SSH into it** or configure with stdio transport.

---

## Option 2: Bundle as NPM Package (Advanced)

For distribution via Claude Desktop/Code, you can publish the MCP server as an NPM package that Claude installs automatically.

This is a more advanced flow — see [Anthropic MCP docs](https://modelcontextprotocol.io/docs) for details.

---

## User Setup (Claude Code)

### Step 1: Get API Key
User logs into Poysis dashboard and generates an API key (really their user_id).

### Step 2: Configure Claude Code
Edit `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "poysis": {
      "command": "python",
      "args": ["/path/to/poysis/mcp_server.py"],
      "env": {
        "POYSIS_API_KEY": "YOUR_USER_ID_HERE",
        "WORKER_URL": "https://your-worker.railway.app"
      }
    }
  }
}
```

### Step 3: Restart Claude Code
Tools become available immediately.

---

## User Setup (Claude Desktop)

Same as Claude Code but edit `~/.claude/desktop/settings.json` instead.

---

## User Setup (ChatGPT / Other Tools)

Currently, ChatGPT doesn't natively support MCP. You can:

1. **Use the REST API directly** (without MCP)
   - Endpoints: `/retrieval/query_knowledge_base`, `/retrieval/list_documents`
   - Auth: `X-User-ID: {user-api-key}` header

2. **Build a ChatGPT Plugin** (wraps the endpoints as OpenAI tools)

3. **Wait for ChatGPT MCP support** (coming in future updates)

---

## Testing

1. **Start the server locally:**
   ```bash
   export POYSIS_API_KEY="test-user-id"
   export WORKER_URL="http://localhost:8000"
   python mcp_server.py
   ```

2. **Configure Claude Code to use it:**
   ```json
   {
     "mcpServers": {
       "poysis-local": {
         "command": "python",
         "args": ["./mcp_server.py"],
         "env": {
           "POYSIS_API_KEY": "test-user-id",
           "WORKER_URL": "http://localhost:8000"
         }
       }
     }
   }
   ```

3. **In Claude Code, ask:** "Query my knowledge base for information about project deadlines"

---

## Monitoring

**Logs** from the MCP server appear in:
- **Claude Code**: Debug console (View → Debug → Output)
- **Claude Desktop**: Logs in `~/.claude/logs/`
- **Standalone service**: Depends on where you deployed it

**Errors** show up in the tool responses, e.g.:
```
Error: Worker error: 401 — invalid API key
Error: Worker error: 500 — internal server error
```

---

## Production Checklist

- [ ] MCP server deployed and accessible
- [ ] `POYSIS_API_KEY` is user's actual user_id
- [ ] `WORKER_URL` points to production FastAPI worker
- [ ] FastAPI worker has rate limiting enabled (middleware)
- [ ] FastAPI worker has logging enabled
- [ ] User API key generation system in place (Poysis dashboard)
- [ ] Error messages are user-friendly (not stack traces)

---

## Troubleshooting

**"Cannot find module mcp"**
```bash
pip install mcp
```

**"Worker error: 401"**
- API key is wrong or expired
- User needs to generate a new one in dashboard

**"Worker error: 500"**
- Check FastAPI logs: `docker logs <container>`
- Check database connection
- Check Gemini API quota

**Claude Code doesn't show the tools**
- Restart Claude Code completely
- Check settings JSON syntax with `jq .mcpServers ~/.claude/settings.json`

---

## Distribution

**Free tier users:**
- Can use MCP tools
- API key expires after 30 days

**Paid tier users:**
- Unlimited API keys
- Can integrate directly into workflows
