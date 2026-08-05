# Client Integration Guide — `feat/directory-seeding`

What changed on the worker that the frontend needs to know about. Four areas:

1. **Chat gained a retrieval mode** — a stream-contract change you must handle. ⚠️ Action required.
2. **YouTube playlists → categories** — two new endpoints to build the "organize by playlist" UI. New feature.
3. **Transcripts now work from production** — server-side only, no client action.
4. **`sources` is required on snapshot/discover** — the `["google_drive"]` default is gone. ⚠️ Action required.
5. **Live progress stream fixed** — reads persisted job state, reports `not_started` instead of hanging. ⚠️ Action required (`EventSource` won't authenticate).

---

## 1. Chat: retrieval vs synthesis mode ⚠️

`POST /chat` used to always **synthesize** (stream a written answer). It now also does **retrieval** (return ranked sources, no written answer), and a classifier picks per-query — **inferred and ON by default when the request omits `mode`.**

**Why you can't ignore this:** because routing is inferred, an ordinary query can now come back retrieval-shaped **without you asking for it.** If your parser assumes "prose → `__SOURCES__` → `__META__`", a retrieval reply will render the literal `__MODE__{...}` marker as answer text and show an empty body.

### Request — one new optional field

| field  | type | meaning |
|--------|------|---------|
| `mode` | string | `"retrieval"` \| `"synthesis"` \| omitted. Omitted → classifier decides. Set → forces that mode, classifier skipped. |

Everything else (`workspace_id`, `query`, `top_k`, `instructions`, `allowed_topic_ids`, …) is unchanged.

### Response contract (`text/event-stream`, same transport as today)

**Synthesis** — *byte-for-byte identical to today, no leading marker:*
```
<answer tokens streamed…>\n\n__SOURCES__<json>\n\n__META__<json>
```
`__META__` = `{ scale, themes, key_quote }`. Mid-stream failure → `\n\n__ERROR__<json>`.

**Retrieval** — *new shape:*
```
__MODE__{"mode":"retrieval"}\n\n__SOURCES__<json>\n\n__META__<json>
```
- Leads with `__MODE__` (**no leading `\n\n`** — first bytes of the stream).
- **No answer tokens.**
- `__META__` = `{ scale, themes }` — **no `key_quote`** (retrieval is LLM-free; treat it as optional/absent).

**Empty results:** synthesis sends `I couldn't find…\n\n__SOURCES__[]`; retrieval sends `__MODE__{"mode":"retrieval"}\n\n__SOURCES__[]` (still self-identifies).

### What to change
1. **Detect a leading `__MODE__` marker** → render a source-list view instead of a typing indicator. Buffer the stream; don't assume the marker arrives as one chunk.
2. **Never render `__MODE__…` as answer text.**
3. **Tolerate missing `key_quote`** in retrieval `__META__`.
4. **Retrieval empty-state:** `__MODE__` + `[]` → "no results", not blank prose.

### Reference parser (the delta is the `__MODE__` handling)
```ts
const MODE = "__MODE__", SRC = "__SOURCES__", META = "__META__", ERR = "__ERROR__";

// Call with the full accumulated stream string; safe on every chunk.
function parseChatStream(buf: string) {
  const isRetrieval = buf.trimStart().startsWith(MODE);        // NEW
  const iSrc = buf.indexOf(SRC), iErr = buf.indexOf(ERR), iMeta = buf.indexOf(META);

  let answer = "";
  if (!isRetrieval) {
    const first = [iSrc, iErr].filter(i => i >= 0).sort((a,b)=>a-b)[0] ?? buf.length;
    answer = buf.slice(0, first).replace(/\n\n$/, "");
  }
  const sliceJson = (start: number, marker: string) => {
    if (start < 0) return undefined;
    const rest = buf.slice(start + marker.length);
    const end = [rest.indexOf(SRC), rest.indexOf(META), rest.indexOf(ERR)].filter(i=>i>=0).sort((a,b)=>a-b)[0];
    try { return JSON.parse(end === undefined ? rest : rest.slice(0, end)); } catch { return undefined; }
  };

  return {
    mode: isRetrieval ? "retrieval" : "synthesis",
    answer,                                   // "" in retrieval mode
    sources: sliceJson(iSrc, SRC) ?? [],
    meta: sliceJson(iMeta, META) ?? null,
    error: iErr >= 0 ? (sliceJson(iErr, ERR)?.message ?? "Something went wrong") : null,
  };
}
```

### Migration levers
- **To defer this work:** send `mode: "synthesis"` on every `/chat` request. That pins today's behavior exactly (classifier skipped, nothing new comes back) until you're ready.
- **To use retrieval intentionally** (e.g. a "Search" tab): send `mode: "retrieval"`.

> Recommended: don't rely on inferred routing in production until the parser handles `__MODE__`. Pin `mode:"synthesis"` in the meantime — zero risk.

---

## 1b. Chat: conversation memory + sources-first streaming

Two new optional `/chat` fields. Both are **opt-in — omit them and nothing changes.**

### `history` — makes follow-ups work

`/chat` was stateless: every turn was an independent query. "Tell me more about that" retrieved nothing useful, because the subject lived in the previous turn.

Send the transcript and it resolves:

| field | type | meaning |
|-------|------|---------|
| `history` | array | `[{ role: "user" \| "assistant", content: string }]`, oldest → newest, **excluding** the current `query`. |

```jsonc
{
  "workspace_id": "ws_…",
  "query": "what about doubt?",
  "history": [
    { "role": "user",      "content": "what does he teach about faith?" },
    { "role": "assistant", "content": "He frames faith as trust under pressure…" }
  ]
}
```

- **The client owns the transcript.** The server stores nothing — keep it in component state and send the window each turn.
- Server caps at the **last 6 turns**; assistant turns are truncated to ~300 chars. Sending more is fine, it just gets trimmed. Over **20 turns → 422.**
- On a follow-up the server rewrites the query into a standalone one before retrieving. Costs one cheap model call, **only when `history` is non-empty** — first turns are unaffected.

### `sources_first` — the perceived-latency win

| field | type | meaning |
|-------|------|---------|
| `sources_first` | bool | Default `false`. `true` → `__SOURCES__` leads the stream. |

```
__SOURCES__<json>\n\n<answer tokens streamed…>\n\n__META__<json>
```

Same payload, same `__META__` position — only the sources move. Today you can't render anything until the whole answer finishes generating; with this you paint source cards the moment retrieval lands and stream prose into place.

⚠️ **It changes the byte order.** Ship the parser change first, then flip the flag — otherwise the client prints `__SOURCES__{…}` as answer text. This will become the default once clients have migrated.

---

## 2. YouTube playlists → categories (new feature)

Lets an owner turn a channel's **playlists** into notebook **categories** — works even with no transcripts, because it uses the YouTube Data API (not the blocked scrapers). Imported playlists become real, **locked** topic categories that show up in the existing topic surfaces (`get_topics`, MCP `list_topics`) and are usable as chat scope (`allowed_topic_ids`).

Both endpoints require the usual auth (owner's `X-User-ID` / JWT) and check workspace ownership.

### List a channel's playlists
```
GET /sources/youtube/playlists?workspace_id=<id>
```
```jsonc
{
  "workspace_id": "…",
  "playlists": [
    {
      "playlist_id": "PLgoPFqS4_psq0du-ff2",
      "title": "Sermons",
      "description": "…",
      "item_count": 172,
      "thumbnail": "https://…",
      "channel_id": "UC…",
      "channel_name": "Emmanuel Iren Live"
    }
    // …
  ]
}
```

### Import selected playlists as categories
```
POST /sources/youtube/playlists/import
Content-Type: application/x-www-form-urlencoded

workspace_id=<id>&playlist_ids=PLxxx,PLyyy
```
- `playlist_ids`: **comma-separated** list of playlist ids the user picked.
```jsonc
{
  "status": "imported",
  "workspace_id": "…",
  "categories": [
    { "topic_id": 517, "label": "Sermons", "video_count": 172, "vectors_stamped": 0 }
  ]
}
```
- `topic_id` — the category id; use it anywhere topics are used (e.g. `allowed_topic_ids: [517]` to scope chat to that playlist).
- `vectors_stamped` — how many already-ingested chunks were tagged into this category immediately (0 until that channel's transcripts exist).
- **Idempotent:** re-importing the same playlist reuses its `topic_id` and refreshes membership — no duplicates.

**Behavior notes**
- This is **overlay** categorization: it labels content, it does **not** restrict ingestion (the whole channel is still ingested).
- Categories are **visible/browsable immediately**. They become **chat-queryable** once the channel's transcripts are ingested (see §3) — for already-transcribed workspaces that's immediate.
- Schema: adds `topic_documents.metadata` (already applied to the DB — no client action).

---

## 3. Transcripts now work from production (no client action)

Previously YouTube blocked transcript fetching from the datacenter IP, so newly-connected channels couldn't ingest captions. That's fixed server-side (caption fetch now routes through a residential proxy, configured via Railway env vars). **Nothing to change on the client** — just be aware that connecting a channel now actually produces transcripts + a queryable notebook.

---

## 4. `sources` is now required on snapshot/discover ⚠️

**Short answer: yes — always send `sources`, and list every source you want ingested.**

`POST /consolidation/snapshot` and `POST /consolidation/discover` used to default `sources` to `["google_drive"]` when you omitted it. That default is gone. The field is required and must be non-empty.

### Request

| field | type | meaning |
|-------|------|---------|
| `sources` | `string[]` | **Required, non-empty.** `"google_drive"` \| `"youtube"` |
| `skip_clustering` | `boolean` | Optional, default `false`. Index only — don't build the topic hierarchy. |

Everything else (`time_window_days`, `doc_limit`, `drive_folder_ids`, `cluster_instructions`) is unchanged and still optional.

**`skip_clustering`** is for large backfills. Clustering is the expensive, non-linear tail of a run — UMAP/HDBSCAN plus per-topic semantic analysis over the entire namespace, re-done from scratch every time. On a multi-thousand-video ingest it's worth skipping and running once at the end via `POST /consolidation/cluster/{workspace_id}`. Until you do, the workspace has vectors but no topics, so retrieval works and topic-scoped browsing doesn't.

**The array is the whole instruction — only what you list gets ingested.** Send what the workspace actually has connected:

```jsonc
{ "workspace_id": "ws_...", "sources": ["google_drive"] }              // Drive-only workspace
{ "workspace_id": "ws_...", "sources": ["youtube"] }                   // YouTube-only workspace
{ "workspace_id": "ws_...", "sources": ["google_drive", "youtube"] }   // both — one snapshot covers them
```

You don't pass channel IDs or folder IDs for YouTube — the worker resolves connected channels from the workspace itself. `"youtube"` just means "include them".

### Errors you'll now get

| response | cause |
|---|---|
| `422` `Field required` | `sources` omitted |
| `422` `List should have at least 1 item` | `sources: []` |
| `422` `Input should be 'google_drive' or 'youtube'` | unrecognized value / typo |
| `400` `sources includes 'youtube' but no YouTube channels are connected…` | asked for YouTube on a workspace with none attached |
| `401` `No Google token found…` | `"google_drive"` listed, OAuth not completed (unchanged) |

### Why this changed

Omitting `sources` on a YouTube-only workspace previously fell through to the Drive default, hit the Google-token check, and returned `401` **before the job record was created** — so the run left no trace in the jobs table and looked, from the dashboard, like nothing had happened at all. Requiring the field makes the caller's intent explicit and turns that class of failure into an immediate, readable error.

### What to change

Find every call to `/consolidation/snapshot` and `/consolidation/discover` and make sure `sources` is populated from the workspace's connected sources rather than left to the default. If a workspace has both Drive and YouTube connected, send both — a single snapshot ingests them together.

---

## 5. Live consolidation progress (SSE) ⚠️

`GET /consolidation/snapshot/stream/{workspace_id}` now reads the job row in Postgres instead of in-process memory. It survives a worker redeploy mid-run, and it can no longer sit silent when there's nothing to report.

### The flow

```
POST /consolidation/snapshot   →  { "status": "started", "job_id": "..." }
GET  /consolidation/snapshot/stream/{workspace_id}?job_id={job_id}
```

**Pass `job_id`.** It's optional — without it the stream follows the workspace's *latest* snapshot job, which can briefly report the previous run's result while the new row is being created. With it you always watch the run you just started.

### ⚠️ `EventSource` cannot send the `X-User-ID` header

The existing snippet in `CONSOLIDATION_STREAMING.md` is wrong:

```typescript
// ✗ Does NOT work — `headers` is silently ignored
new EventSource(url, { headers: { 'X-User-ID': userId } });
```

The browser `EventSource` constructor's second argument is `EventSourceInit`, which accepts **only** `{ withCredentials }`. Any `headers` key is dropped, the request arrives without `X-User-ID`, and the endpoint returns **401** before streaming anything. Use a fetch-based SSE client instead:

```typescript
import { fetchEventSource } from '@microsoft/fetch-event-source';

const ctrl = new AbortController();

await fetchEventSource(
  `${WORKER_URL}/consolidation/snapshot/stream/${workspaceId}?job_id=${jobId}`,
  {
    headers: { 'X-User-ID': userId },
    signal: ctrl.signal,
    onmessage(ev) {
      const data = JSON.parse(ev.data);

      switch (data.status) {
        case 'not_started':
          // The POST didn't take. Surface the error — don't show a spinner.
          showError('Consolidation never started. Check the start request.');
          ctrl.abort();
          break;
        case 'running':
          updateProgress(data);            // counters below
          break;
        case 'clustering':
          setPhase('Organizing topics…');  // indexing done, building the map
          break;
        case 'failed':
          showError(data.error);
          ctrl.abort();
          break;
        case 'done':
          if (data.type === 'complete') {  // final event, carries mcp_url
            showComplete(data.mcp_url);
            ctrl.abort();
          }
          break;
      }
    },
    onerror(err) { ctrl.abort(); throw err; },  // throw = stop retrying
  },
);
```

### Event shapes

Every event is `data: {json}`. Two `type`s:

| `type` | when | notable fields |
|---|---|---|
| `progress` | any state change | `status`, counters, `error` |
| `complete` | once, immediately after the final `progress` | `mcp_url` + everything from the final state |

`status` is the field to switch on:

| `status` | meaning |
|---|---|
| `not_started` | **No job exists.** Sent immediately, then the stream closes. Your POST failed — read its response. |
| `running` | Indexing. Counters update as batches flush. |
| `clustering` | Indexing finished; building the topic hierarchy. Counters are final. |
| `done` | Finished. A `type: "complete"` event follows with `mcp_url`. |
| `failed` | Run failed; `error` holds the reason. |

Counters on `running` / `clustering` / `done`:

| field | meaning |
|---|---|
| `docs_processed` | documents successfully indexed |
| `docs_skipped` | unchanged since last run (already indexed) |
| `docs_orphaned` | deliberately skipped — oversized or unparseable |
| `vectors_indexed` | chunks embedded and stored |

On `done` you also get `leaf_topics`, `total_topics`, `hierarchy_depth`, `iterations`, `errors` (up to 50 per-document failures) with `error_count`, and `clustering`:

| `clustering` | meaning |
|---|---|
| `done` | topic hierarchy rebuilt |
| `skipped` | nothing changed, so no re-cluster was needed |
| `skipped_by_request` | you passed `skip_clustering: true` — topics are stale until you run `POST /consolidation/cluster/{workspace_id}` |

### Two behaviours to code for

**Keepalive frames.** During long fetch phases that emit no progress, the server sends SSE comment lines (`: keepalive`) about every 15s to stop proxies dropping the connection. `EventSource` and `fetchEventSource` both ignore comment frames — you'll never see them in `onmessage`. Just don't treat silence between events as a hang.

**`not_started` is a real answer.** Previously a failed POST left the stream open and completely silent for 30 minutes, so "starting…" spun forever. Now you get an immediate answer and the connection closes. Show the error rather than a spinner.

**Duplicate-free by design:** events fire only when state *changes*, so an unchanged poll emits nothing. The stream closes on its own at `done`, `failed`, or `not_started`, and caps at 30 minutes.

---

## TL;DR checklist for the frontend
- [ ] **Chat:** handle the leading `__MODE__` marker (or pin `mode:"synthesis"` to defer). This is the only breaking change.
- [ ] **Playlists:** build the browse/import UI on the two new `/sources/youtube/playlists…` endpoints; use returned `topic_id`s as categories.
- [ ] **Transcripts:** nothing — just works now.
- [ ] **Snapshot/discover:** always send a non-empty `sources` array listing every connected source. Breaking — omitting it is now a 422.
- [ ] **Progress stream:** swap `EventSource` for a fetch-based SSE client (it can't send `X-User-ID` — you're getting 401s today), pass `?job_id=`, and handle `status: "not_started"` as an error instead of a spinner.
