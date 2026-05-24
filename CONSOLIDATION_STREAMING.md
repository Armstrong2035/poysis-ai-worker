# Real-Time Consolidation Streaming Guide

This guide explains how to show users live progress as their documents are being processed, indexed, and organized into topics.

## The Big Picture

When a user clicks "Start Consolidation", here's what happens:

1. **Backend starts a background job** that pulls documents from Google Drive
2. **Frontend opens a live stream** to watch the job's progress
3. **Backend sends updates** every time metrics change (docs processed, vectors created, topics found)
4. **Frontend displays** these updates in real-time (progress bars, counters, status messages)
5. **When done**, the backend sends the MCP URL that the user can copy to Claude

## Frontend Implementation

### Step 1: Start the Consolidation

```typescript
// User clicks "Start Consolidation"
const response = await fetch('http://localhost:8000/consolidation/snapshot', {
  method: 'POST',
  headers: {
    'X-User-ID': userId,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    workspace_id: workspaceId,
    sources: ['google_drive'],
  }),
});

const { job_id, status } = await response.json();
// status should be "started"
```

### Step 2: Open the Live Stream

```typescript
// Open Server-Sent Events (SSE) stream to watch progress
const eventSource = new EventSource(
  `http://localhost:8000/consolidation/snapshot/stream/${workspaceId}`,
  {
    headers: {
      'X-User-ID': userId,
    },
  }
);

// Listen for progress events
eventSource.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);

  if (data.type === 'progress') {
    // Update UI with current metrics
    updateProgressUI(data);
  } else if (data.type === 'complete') {
    // Job is done, show the MCP URL
    showCompletionScreen(data.mcp_url);
    eventSource.close();
  }
});

// Handle errors
eventSource.addEventListener('error', (event) => {
  console.error('Stream error:', event);
  eventSource.close();
});
```

### Step 3: Display Progress in UI

```typescript
function updateProgressUI(data) {
  // data contains:
  // - status: "running", "clustering", "done", "failed"
  // - docs_processed: number of docs indexed so far
  // - vectors_indexed: number of embeddings created
  // - docs_skipped: docs that failed to process
  // - docs_orphaned: orphaned documents
  // - iterations: batch number we're on
  // - leaf_topics: number of topics created (during clustering phase)
  // - total_topics: total topics in hierarchy
  // - error: error message if status is "failed"

  // Example: Update a progress bar
  const progress = (data.docs_processed / totalDocs) * 100;
  progressBar.style.width = `${progress}%`;

  // Example: Show current metrics
  document.querySelector('.doc-count').innerText = `${data.docs_processed} docs processed`;
  document.querySelector('.vector-count').innerText = `${data.vectors_indexed} vectors created`;

  // Example: Show phase
  if (data.status === 'clustering') {
    phaseIndicator.innerText = 'Organizing into topics...';
  } else if (data.status === 'running') {
    phaseIndicator.innerText = 'Indexing documents...';
  }

  // Example: Show topics as they're discovered
  if (data.leaf_topics) {
    topicCount.innerText = `${data.leaf_topics} topics discovered`;
  }

  // Example: Show errors
  if (data.error) {
    errorBanner.innerText = `Error: ${data.error}`;
    errorBanner.style.display = 'block';
  }
}

function showCompletionScreen(mpcUrl) {
  // Show the MCP URL so user can copy it
  mpcUrlDisplay.value = mpcUrl;
  completionMessage.innerText = '✓ Your knowledge base is ready! Copy this URL to Claude.ai';
}
```

## What Each Metric Means (Non-Technical)

| Metric | What It Means | User-Friendly Display |
|--------|---|---|
| `docs_processed` | How many documents have been read and indexed | "156 documents processed" |
| `vectors_indexed` | How many AI embeddings have been created (one per document chunk) | "1,240 vectors created" |
| `docs_skipped` | Documents that couldn't be processed (corrupted, unsupported format) | "2 documents skipped" |
| `iterations` | Which "batch" we're on (system processes docs in batches for efficiency) | "Batch 3 of 5" |
| `status` | Current phase of work | "Indexing..." or "Organizing..." |
| `leaf_topics` | Number of knowledge clusters/categories discovered | "12 topics discovered" |
| `total_topics` | Total topics including parent categories | Shows hierarchy depth |

## Example: Complete Flow

```typescript
async function startConsolidation(workspaceId, userId) {
  // 1. Start the job
  const startResponse = await fetch('http://localhost:8000/consolidation/snapshot', {
    method: 'POST',
    headers: {
      'X-User-ID': userId,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ workspace_id: workspaceId, sources: ['google_drive'] }),
  });

  if (!startResponse.ok) {
    alert('Failed to start consolidation');
    return;
  }

  // 2. Show progress UI
  showProgressScreen();

  // 3. Open stream
  const eventSource = new EventSource(
    `http://localhost:8000/consolidation/snapshot/stream/${workspaceId}`,
    { headers: { 'X-User-ID': userId } }
  );

  // 4. Handle updates
  eventSource.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);

    // Update every metric
    if (data.type === 'progress') {
      updateProgressUI(data);
    } else if (data.type === 'complete') {
      // Show success and MCP URL
      hideProgressScreen();
      showSuccessScreen(data.mcp_url);
      eventSource.close();
    }
  });

  // 5. Handle errors
  eventSource.addEventListener('error', () => {
    alert('Connection lost');
    eventSource.close();
  });
}
```

## Event Stream Format

The backend sends **Server-Sent Events** (SSE), which is a standard for live updates.

Each event looks like:
```
data: {"type": "progress", "status": "running", "docs_processed": 45, "vectors_indexed": 120, ...}

data: {"type": "progress", "status": "clustering", "leaf_topics": 8, ...}

data: {"type": "complete", "mcp_url": "https://...", "status": "done", ...}
```

Your JavaScript code receives these as `message` events and parses the JSON.

## Edge Cases

### What if the user closes the tab?

The stream closes. The backend job keeps running in the background. If they come back:

```typescript
// Check if job is still running
const status = await fetch(
  `http://localhost:8000/consolidation/snapshot/status/${workspaceId}`,
  { headers: { 'X-User-ID': userId } }
).then(r => r.json());

if (status.status === 'running') {
  // Reconnect to stream
  reconnectToStream(workspaceId, userId);
} else if (status.status === 'done') {
  // Show completion screen immediately
  showSuccessScreen(status.result.mcp_url);
}
```

### What if there's an error?

The stream will send an event with `status: "failed"` and an `error` field:

```typescript
if (data.status === 'failed') {
  showErrorMessage(data.error);
  eventSource.close();
}
```

### What if the job takes a really long time?

The stream stays open for up to 30 minutes. If it takes longer, the connection closes (but the job keeps running in the backend). Users can refresh and check status via the status endpoint.

## Headers Required

All requests need the `X-User-ID` header:

```typescript
headers: {
  'X-User-ID': userId,  // ← Always include this
  'Content-Type': 'application/json',
}
```

This tells the backend which user is making the request (for security).

## Testing Locally

1. Start the worker:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

2. Make sure you have a workspace and Google Drive connected

3. Open browser DevTools (F12) → Console

4. Paste this:
   ```javascript
   const userId = 'f87ceff1-282c-4355-87d3-c304cd5a0c1d';
   const workspaceId = 'ws_7c56cdfe-a516-45c4-a862-b2f5925af429';

   // Start consolidation
   fetch(`http://localhost:8000/consolidation/snapshot`, {
     method: 'POST',
     headers: { 'X-User-ID': userId, 'Content-Type': 'application/json' },
     body: JSON.stringify({ workspace_id: workspaceId, sources: ['google_drive'] }),
   }).then(r => r.json()).then(console.log);

   // Open stream
   const es = new EventSource(
     `http://localhost:8000/consolidation/snapshot/stream/${workspaceId}`,
     { headers: { 'X-User-ID': userId } }
   );
   es.addEventListener('message', e => console.log(JSON.parse(e.data)));
   es.addEventListener('error', () => { console.log('Stream closed'); es.close(); });
   ```

5. Watch the console as events stream in!

## Summary

- **POST `/consolidation/snapshot`** = Start the job
- **GET `/consolidation/snapshot/stream/{workspace_id}`** = Open live stream
- **GET `/consolidation/snapshot/status/{workspace_id}`** = Check status anytime
- Each event includes all current metrics so you can display whatever you want
- Connection stays open until the job finishes or an error occurs
