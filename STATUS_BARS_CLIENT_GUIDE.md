# Status Bars — Client Guide

How to build the two progress indicators for a notebook, straight from Supabase (no worker endpoint needed — this sidesteps the SSE/EventSource auth problem).

- **Live bar** — "we're building your notebook right now." Updates every few seconds while a channel is being processed.
- **Stable bar** — "here's what's in your notebook." The settled summary once processing is done.

Both read tables you can query with the normal Supabase client. `:ws` below is the notebook's `workspace_id`.

---

## First, the words (plain English)

Everything a notebook is made of, simplest first:

- **Document** (aka *source*) — **one whole piece of content.** For a YouTube channel, one document = **one video's transcript.** "247 documents" = 247 videos' worth of text.
- **Chunk** (aka *passage*) — **a short slice of a document**, a few sentences long, tagged with a timestamp. We cut each video into chunks so the AI can pull out the *exact* relevant moment instead of a whole hour. One video becomes many chunks.
- **Vector / embedding** — the **numeric "meaning fingerprint" of a chunk.** It's what lets us search by meaning instead of keywords. You don't show this to users; just know "1 chunk = 1 vector." So a chunk count and a vector count are the same number.
- **Topic / Category** — **a bucket of related documents.** Either the AI groups similar videos automatically, or they come from a YouTube playlist. This is what the user sees as the notebook's sections.
- **Snapshot / Job** — **one run of "go fetch and process this channel."** It's the thing the live bar watches. A job is `running`, then `done` (or `failed`).

The counters a job reports as it works:

| Field | Plain meaning |
|-------|---------------|
| `docs_processed` | Videos handled so far. |
| `vectors_indexed` | Chunks (passages) created so far. |
| `docs_skipped` | Videos passed over (too short, or already done in a previous run). |
| `docs_orphaned` | Videos already known to be unusable, skipped without retrying. |
| `docs_failed` | Videos that failed *on this run* (usually no captions). Rising `docs_failed` with a flat `docs_processed` still means the job is alive — it's working, just not finding anything usable. |

---

## Live bar — while it's processing

**What to read:** the newest row in `consolidation_jobs` for this workspace. Poll it every ~3–5 seconds, or use a Supabase Realtime subscription (below).

```sql
select status, result, started_at, updated_at
from consolidation_jobs
where workspace_id = :ws
order by created_at desc
limit 1;
```

Supabase JS:
```ts
const { data: job } = await supabase
  .from('consolidation_jobs')
  .select('status, result, started_at, updated_at')
  .eq('workspace_id', ws)
  .order('created_at', { ascending: false })
  .limit(1)
  .maybeSingle();
```

**What each field tells you:**
- `status` — `"running"` | `"done"` | `"failed"`. (If no row at all → nothing has started yet; show "Not started.")
- `result.docs_processed` / `result.vectors_indexed` — the live counters to display.
- `updated_at` — **freshness / stall check.** A run heartbeats on every document it resolves, success or failure, and at most once every 5 seconds. YouTube fetches are deliberately serial with a 3s throttle, and a video that gets rate-limited burns up to ~3.5 minutes of backoff before giving up — so the honest stall threshold is **~5 minutes**, not 2–3. Past that, show a gentle "taking longer than usual" rather than a frozen bar.

**What to show:**
- `running` → spinner + `Processing… {docs_processed} videos, {vectors_indexed} passages so far.`
- `done` → hand off to the stable bar.
- `failed` → error state (the `error` column has the reason if you select it).

**Realtime (nicer than polling):**
```ts
supabase.channel(`job-${ws}`)
  .on('postgres_changes',
    { event: '*', schema: 'public', table: 'consolidation_jobs', filter: `workspace_id=eq.${ws}` },
    ({ new: job }) => updateLiveBar(job))
  .subscribe();
```

**About a percentage:** the job doesn't know the channel's total video count up front, so the honest default is a **counter + spinner** (indeterminate), not a strict %. If you want a real percentage, fetch the channel's total video count separately — sum `item_count` from `GET /sources/youtube/playlists`, or the channel's uploads count — and compute `docs_processed / total`. Treat it as approximate.

---

## Stable bar — the settled notebook

Once processing is done, stop reading the live counters and show a **summary of what's actually in the notebook.** Two reads:

**1. Final totals — from the last completed job:**
```sql
select result, completed_at
from consolidation_jobs
where workspace_id = :ws and status = 'done'
order by completed_at desc
limit 1;
```
From `result`: `vectors_indexed` (total chunks/passages), `docs_processed` (documents), `total_topics` (categories). `completed_at` = "last updated" timestamp.

**2. The categories themselves — from `consolidation_topics`:**
```sql
select topic_id, label, doc_count, parent_topic_id, semantic_summary
from consolidation_topics
where workspace_id = :ws
order by doc_count desc;
```
- Top-level categories are the rows where `parent_topic_id is null`; the rest are sub-categories.
- `doc_count` = how many documents (videos) are in that category.
- **Summing `doc_count` across the top-level rows is a reliable "documents in this notebook" number** — and it doesn't depend on a job row existing.

**What to show:**
- `Ready · {documents} videos · {chunks} passages · {categories} categories`
- Optionally "Last updated {completed_at}."
- The category list (labels + counts) for the notebook's sections.

---

## One setup note (RLS)

You already read `consolidation_topics` for the notebook UI, so that query works today. If the `consolidation_jobs` query comes back **empty** for a workspace you own, it's almost certainly missing a Row-Level-Security read policy for that table — add an anon read policy scoped to workspace ownership, the same shape as whatever lets the owner read `consolidation_topics`. (See the anon-key RLS pattern the rest of the app uses.)

---

## TL;DR
- **Live bar** → newest `consolidation_jobs` row; show `status` + `docs_processed`/`vectors_indexed`; use `updated_at` to detect a stall. Poll or subscribe.
- **Stable bar** → last `status='done'` job `result` for totals + `consolidation_topics` for the categories.
- **Document = one video. Chunk/passage = a slice of it. Category = a bucket of related videos.**
