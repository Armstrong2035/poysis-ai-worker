# MCP Integration Guide — Frontend

**Goal:** Display the Claude integration URL after clustering completes, so users can add their knowledge to Claude.ai.

---

## Overview

After clustering finishes, the backend returns an `mcp_url`. The frontend displays this URL with instructions for adding it to Claude.ai as a Cloud Connector.

### Data Flow

```
User clicks "Run Clustering"
  ↓
POST /consolidation/cluster/{workspace_id}
  ↓
Backend starts job, returns job_id
  ↓
Frontend polls GET /consolidation/cluster/status/{workspace_id}
  ↓
Status returns: { status: "done", mcp_url: "https://..." }
  ↓
Display URL with setup instructions
```

---

## API Integration

### 1. Start Clustering

```typescript
async function startClustering(workspaceId: string, userId: string) {
  const response = await fetch(
    `${WORKER_URL}/consolidation/cluster/${workspaceId}`,
    {
      method: "POST",
      headers: {
        "X-User-ID": userId,
      },
    }
  );

  const data = await response.json();
  return data.job_id; // Return job ID for polling
}
```

### 2. Poll Clustering Status

```typescript
async function getClusteringStatus(
  workspaceId: string,
  userId: string
): Promise<{
  status: "running" | "done" | "failed" | "not_started";
  leaf_topics: number;
  total_topics: number;
  hierarchy_depth: number;
  mcp_url?: string; // NEW: URL for Claude integration
  error?: string;
}> {
  const response = await fetch(
    `${WORKER_URL}/consolidation/cluster/status/${workspaceId}`,
    {
      headers: {
        "X-User-ID": userId,
      },
    }
  );

  return response.json();
}
```

### 3. Poll Until Complete

```typescript
async function waitForClustering(
  workspaceId: string,
  userId: string,
  maxWaitMs = 300000
): Promise<{
  status: string;
  mcp_url?: string;
  error?: string;
}> {
  const startTime = Date.now();
  const pollInterval = 2000; // 2 seconds

  while (Date.now() - startTime < maxWaitMs) {
    const status = await getClusteringStatus(workspaceId, userId);

    if (status.status === "done") {
      return status; // ← mcp_url is here
    }

    if (status.status === "failed") {
      throw new Error(status.error || "Clustering failed");
    }

    await new Promise((resolve) => setTimeout(resolve, pollInterval));
  }

  throw new Error("Clustering timeout");
}
```

---

## Component: MCP URL Display

### Minimal Version (Copy-Paste)

```typescript
// components/MCPUrlCard.tsx
import { Copy, CheckCircle } from "lucide-react";
import { useState } from "react";

type MCPUrlCardProps = {
  mcp_url: string;
  workspace_id: string;
};

export function MCPUrlCard({ mcp_url, workspace_id }: MCPUrlCardProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(mcp_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-6 max-w-2xl">
      <div className="flex items-start gap-4">
        <CheckCircle className="w-6 h-6 text-green-600 flex-shrink-0 mt-1" />
        <div className="flex-1">
          <h3 className="text-lg font-semibold text-gray-900 mb-2">
            ✨ Ready for Claude
          </h3>
          <p className="text-sm text-gray-600 mb-4">
            Your knowledge has been consolidated and organized. Now you can query it through Claude.ai.
          </p>

          {/* URL Display */}
          <div className="bg-white border border-gray-300 rounded px-3 py-2 font-mono text-sm mb-4 flex items-center justify-between">
            <code className="text-gray-700 truncate">{mcp_url}</code>
            <button
              onClick={handleCopy}
              className="ml-2 p-1 hover:bg-gray-100 rounded text-gray-600"
            >
              <Copy className="w-4 h-4" />
              {copied && <span className="text-xs text-green-600">Copied!</span>}
            </button>
          </div>

          {/* Instructions */}
          <div className="bg-blue-50 rounded p-4 mb-4">
            <h4 className="font-semibold text-sm text-gray-900 mb-2">
              How to add to Claude.ai:
            </h4>
            <ol className="text-sm text-gray-700 space-y-1 list-decimal list-inside">
              <li>Go to claude.ai</li>
              <li>Click Settings → Cloud Connectors</li>
              <li>Click "Add Custom MCP Server"</li>
              <li>Paste the URL above</li>
              <li>Start asking Claude about your knowledge!</li>
            </ol>
          </div>

          {/* Example Queries */}
          <div className="bg-gray-50 rounded p-4">
            <h4 className="font-semibold text-sm text-gray-900 mb-2">
              Try asking Claude:
            </h4>
            <ul className="text-sm text-gray-700 space-y-1">
              <li>• "Summarize my Q3 roadmap"</li>
              <li>• "What are the main topics in my knowledge base?"</li>
              <li>• "Find all documents about budgeting"</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
```

### Usage in Dashboard

```typescript
// app/app/dashboard/page.tsx
import { useState, useEffect } from "react";
import { MCPUrlCard } from "@/components/MCPUrlCard";

export default function Dashboard() {
  const [clusteringStatus, setClusteringStatus] = useState<{
    status: string;
    mcp_url?: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  const handleStartClustering = async () => {
    setLoading(true);
    try {
      const jobId = await startClustering(workspaceId, userId);

      // Poll until done
      const finalStatus = await waitForClustering(workspaceId, userId);
      setClusteringStatus(finalStatus);
    } catch (error) {
      console.error("Clustering failed:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-8">Knowledge Dashboard</h1>

      {/* Clustering Controls */}
      <button
        onClick={handleStartClustering}
        disabled={loading}
        className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Clustering..." : "Run Clustering"}
      </button>

      {/* MCP URL Display (when ready) */}
      {clusteringStatus?.status === "done" && clusteringStatus.mcp_url && (
        <div className="mt-8">
          <MCPUrlCard
            mcp_url={clusteringStatus.mcp_url}
            workspace_id={workspaceId}
          />
        </div>
      )}

      {/* Status Messages */}
      {clusteringStatus?.status === "failed" && (
        <div className="mt-4 p-4 bg-red-50 border border-red-200 text-red-700 rounded">
          Clustering failed. Please try again.
        </div>
      )}
    </div>
  );
}
```

---

## Advanced: SourcesModal Integration

If you have a SourcesModal component, you could display the MCP URL there after clustering:

```typescript
// components/SourcesModal.tsx
type SourcesModalProps = {
  workspace_id: string;
  user_id: string;
  cluster_status?: {
    status: string;
    mcp_url?: string;
  };
};

export function SourcesModal({
  workspace_id,
  user_id,
  cluster_status,
}: SourcesModalProps) {
  return (
    <div>
      {/* Existing sources list */}
      <SourcesList workspace_id={workspace_id} />

      {/* MCP URL section (after clustering) */}
      {cluster_status?.status === "done" && cluster_status.mcp_url && (
        <div className="mt-8 border-t pt-8">
          <h3 className="text-lg font-semibold mb-4">🤖 Claude Integration</h3>
          <MCPUrlCard
            mcp_url={cluster_status.mcp_url}
            workspace_id={workspace_id}
          />
        </div>
      )}

      {/* Run clustering button (if not done yet) */}
      {cluster_status?.status !== "done" && (
        <button
          onClick={() => startClustering(workspace_id, user_id)}
          className="mt-4 px-4 py-2 bg-blue-600 text-white rounded"
        >
          Organize with AI Clustering
        </button>
      )}
    </div>
  );
}
```

---

## Types

```typescript
// types/consolidation.ts
export type ClusteringStatus = {
  workspace_id: string;
  status: "running" | "done" | "failed" | "not_started";
  job_id?: string;
  vectors_indexed?: number;
  docs_processed?: number;
  docs_skipped?: number;
  leaf_topics?: number;
  total_topics?: number;
  hierarchy_depth?: number;
  mcp_url?: string; // NEW
  error?: string;
  started_at?: string;
  completed_at?: string;
};
```

---

## Testing the Integration

### 1. Start Clustering
```bash
curl -X POST http://localhost:8000/consolidation/cluster/ws_test \
  -H "X-User-ID: user-123"
```

### 2. Poll Status (repeat until status = "done")
```bash
curl http://localhost:8000/consolidation/cluster/status/ws_test \
  -H "X-User-ID: user-123"
```

Expected response when done:
```json
{
  "workspace_id": "ws_test",
  "status": "done",
  "vectors_indexed": 245,
  "leaf_topics": 12,
  "total_topics": 18,
  "hierarchy_depth": 2,
  "mcp_url": "http://localhost:8000/mcp?workspace_id=ws_test"
}
```

### 3. Display the MCP URL
Render the `MCPUrlCard` component with the `mcp_url` from the response.

---

## Error Handling

```typescript
// Handle common errors
const handleClusteringError = (error: unknown) => {
  if (error instanceof HTTPException) {
    switch (error.status) {
      case 409:
        showError("Clustering already running for this workspace");
        break;
      case 401:
        showError("You don't have permission to cluster this workspace");
        break;
      default:
        showError(error.message || "Clustering failed");
    }
  }
};
```

---

## Notes for Frontend Developers

1. **MCP URL is opaque** — Just display it and copy it. Users paste it into Claude.ai.
2. **URL includes workspace_id** — Each workspace gets its own URL; workspace IDs are per-user.
3. **No token refresh needed** — The URL is stateless; Claude calls it with the workspace_id in the query param.
4. **Works immediately** — Once clustering is done and mcp_url is returned, users can add it to Claude right away.
5. **Claude-specific** — This only works with Claude.ai, Claude Desktop, or Claude Code. Not ChatGPT or other AI platforms.

---

## What Claude Can Do After Integration

Once users add the MCP URL to Claude, they can:

**Semantic search:**
- "What was discussed about Q3 goals?"
- "Find all documents mentioning budgets"
- "Summarize the architecture decisions"

**Topic browsing:**
- "What are the main topics in my knowledge base?"
- "List all documents from Google Drive"
- "Show me documents about marketing"

**Analysis:**
- "What patterns do you see in my meeting notes?"
- "Summarize the key decisions from last quarter"
- "Extract all action items from my documents"

Claude has full access to retrieval and list_documents tools with full source attribution.
