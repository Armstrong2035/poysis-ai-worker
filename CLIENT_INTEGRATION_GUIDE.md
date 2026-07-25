# Client Integration Guide — `feat/directory-seeding`

What changed on the worker that the frontend needs to know about. Three areas:

1. **Chat gained a retrieval mode** — a stream-contract change you must handle. ⚠️ Action required.
2. **YouTube playlists → categories** — two new endpoints to build the "organize by playlist" UI. New feature.
3. **Transcripts now work from production** — server-side only, no client action.

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

## TL;DR checklist for the frontend
- [ ] **Chat:** handle the leading `__MODE__` marker (or pin `mode:"synthesis"` to defer). This is the only breaking change.
- [ ] **Playlists:** build the browse/import UI on the two new `/sources/youtube/playlists…` endpoints; use returned `topic_id`s as categories.
- [ ] **Transcripts:** nothing — just works now.
