# Poysis Consolidation Pipeline

**Transform fragmented knowledge into an organized, searchable knowledge graph that Claude can query in real time.**

## What It Does

The Poysis consolidation pipeline takes all of a user's documents (from Google Drive, Notion, email, etc.) and:

1. **Discovers** documents from configured sources
2. **Consolidates** them into a vector database with semantic embeddings
3. **Clusters** documents into hierarchical topics using AI categorization
4. **Surfaces** a Claude-native integration (Cloud Connector) so Claude can query the knowledge base directly

## Why It Matters

- **Knowledge Fragmentation Problem**: Users have knowledge scattered across Google Drive, Notion, Gmail, Slack. They can't search across sources or ask questions about the whole knowledge base at once.
- **Claude Integration**: Claude can now see and reason about a user's entire knowledge base. Ask Claude: "What did we discuss about our Q3 roadmap?" and it retrieves from all sources, across all documents.
- **B2B SaaS Model**: Enterprises can consolidate team knowledge and route queries through Claude for insights, summaries, and research.

## How to Use It

### Step 1: Authenticate with Google (Optional)

The pipeline can consolidate Google Drive documents. To enable:

```bash
# Backend runs at http://localhost:8000
# User visits: http://localhost:8000/auth/google/callback
# Exchanges OAuth code for access token
```

### Step 2: Start Consolidation

**Endpoint:** `POST /consolidation/snapshot`

```bash
curl -X POST http://localhost:8000/consolidation/snapshot \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user-123" \
  -d '{
    "workspace_id": "ws_user123",
    "sources": ["google_drive"],
    "time_window_days": 30,
    "doc_limit": 500
  }'
```

**Response:**
```json
{
  "status": "started",
  "workspace_id": "ws_user123",
  "job_id": "job_abc123"
}
```

This kicks off an async job that:
- Connects to user's Google Drive
- Pulls documents updated in the last 30 days (or all documents)
- Converts them to text (PDFs, Docs, Sheets, etc.)
- Embeds them using Gemini embeddings
- Stores vectors in Supabase with metadata (title, source, URL)

### Step 3: Check Status

**Endpoint:** `GET /consolidation/snapshot/status/{workspace_id}`

```bash
curl http://localhost:8000/consolidation/snapshot/status/ws_user123 \
  -H "X-User-ID: user-123"
```

**Response:**
```json
{
  "status": "done",
  "workspace_id": "ws_user123",
  "vectors_indexed": 245,
  "docs_processed": 87,
  "docs_skipped": 3,
  "vectors_indexed": 245
}
```

Wait for `status: "done"` before proceeding.

### Step 4: Run Clustering

**Endpoint:** `POST /consolidation/cluster/{workspace_id}`

```bash
curl -X POST http://localhost:8000/consolidation/cluster/ws_user123 \
  -H "X-User-ID: user-123"
```

**Response:**
```json
{
  "status": "started",
  "workspace_id": "ws_user123"
}
```

This triggers the clustering engine, which:
- Fetches all consolidated documents
- Groups them into themes using Gemini LLM
- Creates a hierarchical topic structure (parent topics → sub-topics)
- Stores the hierarchy in the `consolidation_topics` table
- Updates vector metadata with topic assignments

### Step 5: Get the Knowledge Hierarchy

**Endpoint:** `GET /consolidation/topics/{workspace_id}`

```bash
curl http://localhost:8000/consolidation/topics/ws_user123 \
  -H "X-User-ID: user-123"
```

**Response:**
```json
{
  "workspace_id": "ws_user123",
  "topics": [
    {
      "topic_id": 1,
      "label": "Q3 Roadmap",
      "doc_count": 23,
      "parent_topic_id": null,
      "updated_at": "2026-05-21T10:30:00Z"
    },
    {
      "topic_id": 2,
      "label": "Backend Services",
      "doc_count": 12,
      "parent_topic_id": 1,
      "updated_at": "2026-05-21T10:30:00Z"
    }
  ]
}
```

This is the complete knowledge hierarchy—a navigable tree of topics extracted from the user's documents.

### Step 6: Query the Knowledge Base

Now Claude (or any app) can ask questions about the consolidated knowledge.

**Endpoint:** `POST /retrieval/query_knowledge_base`

```bash
curl -X POST http://localhost:8000/retrieval/query_knowledge_base \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user-123" \
  -d '{
    "workspace_id": "ws_user123",
    "query": "What are our Q3 goals?"
  }'
```

**Response:**
```json
{
  "query": "What are our Q3 goals?",
  "results": [
    {
      "id": "vec_123",
      "score": 0.92,
      "text": "Q3 goals include launching the new API...",
      "source": {
        "title": "Q3 Planning Doc",
        "url": "https://drive.google.com/...",
        "source_type": "google_drive",
        "source_id": "1a2b3c..."
      }
    }
  ],
  "total": 3
}
```

Results include:
- **Semantic relevance score** (0-1)
- **Full text snippet** of the matching section
- **Source attribution** (title, URL, source type)

### Step 7: Use with Claude (Claude-Specific)

After clustering completes, users get an MCP URL:

```
https://poysis.ai/mcp?workspace_id=ws_user123
```

They paste this into Claude.ai → Settings → Cloud Connectors, and Claude automatically:
- Recognizes the MCP connection
- Has access to two tools: `retrieve_from_knowledge_base` and `list_documents`
- Uses these tools when answering questions about the user's knowledge

Example Claude interaction:
```
User: "Summarize our Q3 roadmap"
Claude: [calls retrieve_from_knowledge_base("Q3 roadmap")]
Claude: "Based on your documents, your Q3 roadmap includes..."
```

---

## Architecture

### Components

| Component | Purpose |
|-----------|---------|
| **SnapshotRunner** | Orchestrates document discovery, conversion, embedding, and storage |
| **ClusteringEngine** | Groups documents into topics using LLM-based categorization |
| **Embedder** | Converts text to vectors using Gemini embeddings |
| **VectorService** | Manages Supabase pgvector storage and semantic search |
| **CategorizerEngine** | Uses Gemini LLM to categorize documents into hierarchies |
| **MCP Server** | Exposes consolidation queries as Claude-native tools |

### Data Flow

```
User Sources (Google Drive, Notion, Email)
    ↓
SnapshotRunner (discover + convert + embed)
    ↓
Supabase pgvector (vectors with metadata)
    ↓
ClusteringEngine (group into topics)
    ↓
consolidation_topics table (hierarchy)
    ↓
Claude MCP Server (tools for retrieval)
    ↓
Claude.ai Cloud Connector (user queries)
```

### Database Schema

**vectors** table (Supabase pgvector):
- `id` (UUID)
- `embedding` (vector, 768 dims)
- `namespace` (e.g., `consolidation_ws_123`)
- `metadata` (JSON): title, url, source_type, source_id, page_number, category_id, category_label
- `text` (original chunk text)

**consolidation_topics** table:
- `workspace_id` (foreign key)
- `topic_id` (integer)
- `label` (string) — human-readable topic name
- `keywords` (JSON array) — tags
- `doc_count` (integer) — documents in this topic
- `parent_topic_id` (nullable) — for hierarchies (max 2 levels)
- `updated_at` (timestamp)

---

## Getting Started

### Prerequisites

- Python 3.10+
- FastAPI backend running on `http://localhost:8000`
- Supabase project (for vector storage)
- Gemini API key (for embeddings and categorization)
- Google OAuth credentials (optional, for Google Drive)

### Installation

```bash
# Backend
cd poysis-ai-worker
pip install -r requirements.txt
python main.py

# The backend exposes all consolidation endpoints
# Test: curl http://localhost:8000/health
```

### Environment Variables

```bash
# Supabase
SUPABASE_PRODUCT_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sk_service_...

# LLM APIs
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key  # for embeddings fallback

# Google OAuth (for Drive integration)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback

# MCP Server
MCP_SERVER_URL=https://poysis.ai/mcp  # or http://localhost:8000/mcp for local testing
WORKER_URL=http://localhost:8000
```

### Running the Pipeline

```bash
# Start the backend
python main.py

# In another terminal, trigger consolidation
curl -X POST http://localhost:8000/consolidation/snapshot \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user-123" \
  -d '{
    "workspace_id": "ws_test",
    "sources": ["google_drive"],
    "time_window_days": 30,
    "doc_limit": 100
  }'

# Check status
curl http://localhost:8000/consolidation/snapshot/status/ws_test \
  -H "X-User-ID: user-123"

# Once done, run clustering
curl -X POST http://localhost:8000/consolidation/cluster/ws_test \
  -H "X-User-ID: user-123"

# Check clustering status
curl http://localhost:8000/consolidation/cluster/status/ws_test \
  -H "X-User-ID: user-123"

# View topics
curl http://localhost:8000/consolidation/topics/ws_test \
  -H "X-User-ID: user-123"

# Query the knowledge base
curl -X POST http://localhost:8000/retrieval/query_knowledge_base \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user-123" \
  -d '{
    "workspace_id": "ws_test",
    "query": "What are the main topics?"
  }'
```

---

## Business Value

### For End Users
- **Unified Search**: One search box for all documents across all sources
- **Claude Integration**: Ask Claude anything about their knowledge base; it retrieves and answers
- **Organization**: Auto-generated topic hierarchy shows what knowledge exists and where

### For Teams
- **Institutional Knowledge**: Consolidate team docs, meeting notes, and decisions
- **Knowledge Discovery**: Find connections and gaps in team knowledge
- **Onboarding**: New team members ask Claude about company knowledge instead of asking in Slack

### For Enterprises
- **Data Loss Prevention**: All team knowledge backed up and indexed
- **Audit Trail**: Know what documents exist, who accessed them, when they were updated
- **Integration Ready**: Route queries through Claude for insights, summaries, compliance reviews

---

## Limitations & Future Work

**Current:**
- Two-level hierarchies (parent topics → children)
- Google Drive primary source (Notion, Slack coming soon)
- Consolidation runs on-demand (no scheduling)

**Roadmap:**
- Multi-level topic hierarchies
- Real-time incremental consolidation (detect new docs, add to existing topics)
- Custom categorization rules (let users define their own topic structure)
- Export knowledge graph as JSON/RDF
- Pricing & billing integration (usage-based, per-workspace)

---

## Testing

```bash
# Run test suite
pytest tests/ -v

# Key test files
pytest tests/test_end_to_end.py          # Full consolidation flow
pytest tests/test_mcp_tools.py           # Retrieval endpoints
pytest tests/test_job_persistence.py     # Job state across restarts
pytest tests/test_error_handling.py      # Edge cases
pytest tests/test_auth_isolation.py      # Multi-user isolation
```

---

## Support

- **Docs**: See `KNOWLEDGE_HIERARCHY_CLIENT.md` for frontend integration
- **API Reference**: FastAPI auto-docs at `http://localhost:8000/docs`
- **MCP**: See `mcp_server.py` for Claude integration
