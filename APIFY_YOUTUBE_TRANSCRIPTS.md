# Apify YouTube Transcript Actor — Integration Assessment

**Actor:** [`karamelo/youtube-full-channel-transcripts-extractor`](https://console.apify.com/actors/YbmoVOUovb8Ixfv2z/input)
**Actor ID:** `YbmoVOUovb8Ixfv2z` (API alias: `karamelo~youtube-full-channel-transcripts-extractor`)
**Assessed:** 2026-07-23, against `feat/directory-seeding`.

> **What I verified vs. assumed.** Everything about the actor's identity, input schema, output formats, pricing history, and run stats is pulled live from Apify's public API (`api.apify.com/v2/acts/YbmoVOUovb8Ixfv2z`). I have **not** run the actor, so the **exact output item field names are inferred** — confirm them with one trial run before writing the mapping code. Pricing that applies to *your* account and whether Apify platform compute is billed on top of the rental are also **"verify on the actor page while logged in"** items.

---

## TL;DR / Verdict

- Our own transcript fetch is failing at the **captions** stage (`youtube-transcript-api` gets IP-blocked / 429'd from datacenter IPs). Video *listing* via the YouTube Data API is **not** the failing part.
- This actor solves exactly that stage: it pulls transcripts through Apify's proxy infrastructure, which is what our library can't do.
- It supports **timestamped output** (`json3`, `textWithTimestamps`, `srt`, `vtt`), so our `?t=` deep-links and `timestamp_start_ms` metadata **can be preserved**.
- **Recommended integration: hybrid.** Keep our YouTube Data API listing + 45-minute duration filter (it works, and the actor can't replicate that filter). Replace **only** the broken `fetch_segments` captions call with an Apify run.
- **Main costs of adopting it:** a third-party supply-chain dependency, an `APIFY_TOKEN` to manage, per-video spend, and actor-run latency that only fits our async snapshot pipeline (not a synchronous request).

Health signals (last 30 days): **457 succeeded / 2 failed / 16 aborted / 0 timed-out**; lifetime **29,676 runs, 2,874 users, 4.92★ (15 reviews)**; latest build 2026-07-05, last run the day of this assessment. Actively maintained and widely used.

---

## Why we need it — the failing path in our code

[app/primitives/consolidation/connectors/youtube.py](app/primitives/consolidation/connectors/youtube.py) has two stages:

1. **Listing** — `list_items()` → YouTube Data API v3 (`YOUTUBE_API_KEY`, quota-limited). Filters to videos ≥ `MIN_DURATION_SECONDS` (2700s = 45 min) and tags each with a `connection_id`. **This works.**
2. **Captions** — `fetch_segments()` → `youtube-transcript-api`, no credentials. Already carries a 429 retry ladder (30s/60s/120s) that ultimately raises `RuntimeError("No captions for video …")`. **This is what's failing** — YouTube blocks the library from cloud IPs.

The actor is a drop-in for stage 2 (and optionally stage 1).

---

## What it does

Give it a channel, playlist, or video URL and it returns transcripts for 1–1000s of videos, with optional per-video and per-channel metadata, in your choice of 13 caption formats. It handles YouTube blocking internally via proxy rotation + retries.

### Input schema (verified)

| Field | Type | Default | Notes |
|---|---|---|---|
| `urls` | array\<string\> | — (**required**) | Channel, playlist, `/videos`, `/shorts`, `/streams`, or direct video URLs. |
| `outputFormat` | string | `captions` | See formats below. **Use `json3` or `textWithTimestamps` for us.** |
| `maxVideos` | integer | `0` | `0` = all videos. |
| `maxRetries` | integer | `5` | Retries when blocked. Raise for stubborn videos. |
| `maxScrollRetries` | integer | — | Page-scroll retries to discover more videos. |
| `maxParallelRequests` | integer | `50` | Internal concurrency. Higher = faster, more proxy load. |
| `saveInBatches` / `videosPerBatch` | bool / int | — | Splits huge channels across datasets (100–500/batch) to dodge memory limits. |
| Metadata toggles | bool | — | `channelNameBoolean`, `channelIDBoolean`, `dateTextBoolean`, `datePublishedBoolean`, `viewCountBoolean`, `likesBoolean`, `commentsBoolean`, `keywordsBoolean`, `thumbnailBoolean`, `descriptionBoolean`, `channelHandleBoolean`, `subscriberCountBoolean`, `channelCreationDateBoolean`, `channelCountryBoolean`, `channelViewCountBoolean`, `channelVideoCountBoolean`. |

> **⚠️ No `duration` field.** The metadata toggles do **not** include video duration. So the actor's output alone **cannot** reproduce our 45-minute `MIN_DURATION_SECONDS` filter — a concrete reason to keep the Data API for listing (see Integration).

### Output formats

`captions` (text array), **`textWithTimestamps`**, `xmlWithoutTimestamps`, `xmlWithTimestamps`, `singleStringText`, `srt`, `ttml`, `vtt`, **`json3`**, `srv3`, `srv2`, `srv1`, `sbv`.

**For our pipeline pick `json3`.** It's YouTube's native timed format (`events[].tStartMs`, `dDurationMs`, `segs[].utf8`) and maps cleanly to our segment shape `{start, duration, text}`:

```
start    = event.tStartMs / 1000
duration = event.dDurationMs / 1000
text     = "".join(seg["utf8"] for seg in event["segs"])
```

`srt`/`vtt` are viable fallbacks but need timecode parsing. `captions` (the default) is **plain text with no timing** — do **not** use it, or we lose deep-links.

---

## Pricing

Pricing history (from the API) shows the model changed over time:

| Since | Model | Price |
|---|---|---|
| 2024-07 | per dataset item | $0.01 / video |
| 2024-09 | flat monthly | $20 / mo |
| **2024-10 (current)** | **flat monthly rental** | **$15 / mo**, 1440-min (24h) free trial |

- Current model appears to be a **$15/month rental** (unlimited results per run), not per-video. **Verify the live figure on the actor page while logged in** — pricing can change and the API only shows history.
- On Apify, a rental fee is typically **on top of** your account's platform usage (compute units + proxy/residential traffic). Confirm whether this actor's runs consume extra platform credits on your plan, or whether the rental covers it.
- Budget guardrails exist per run: `maxItems` and `maxTotalChargeUsd` in run options (both currently unset / null by default).

---

## Recommended integration — hybrid (surgical)

Keep stage 1 exactly as-is; replace only the failing captions call.

```
list_items()          → UNCHANGED (Data API: discovery, 45-min filter, connection_id tagging)
fetch_segments(item)  → NEW: call the Apify actor instead of youtube-transcript-api
```

### Why hybrid, not "point the actor at the whole channel"

- The channel-level bulk mode is cheaper/faster in raw scraping, but it **can't filter by duration** (no duration field), so we'd re-ingest shorts/clips the product deliberately excludes — or we'd have to re-derive durations from the Data API anyway. Hybrid keeps our filtering intact and changes the least code.

### Two sub-options for the captions call

**A. Per-video (smallest diff).** In `fetch_segments`, run the actor with `urls=[item.url]`, `outputFormat="json3"`, and map the single result. Fits the existing per-item interface with almost no restructuring. **Downside:** one actor run per video = cold-start overhead × N and more spend.

**B. Batch (recommended for real channels).** After listing + filtering, do **one** actor run with all surviving video URLs, then map dataset items back to segments by `video_id`. Far fewer runs, cheaper, faster. **Downside:** doesn't fit the per-item `fetch_segments` signature — needs a new "prefetch transcripts for these N videos" step in [SnapshotRunner](app/primitives/consolidation) that populates a `{video_id: segments}` cache the connector reads from.

Start with **A** to unblock, move to **B** if per-run overhead or cost bites.

### Calling the actor (API)

- **Sync (small runs):** `POST https://api.apify.com/v2/acts/karamelo~youtube-full-channel-transcripts-extractor/run-sync-get-dataset-items?token=$APIFY_TOKEN` with the input JSON → returns dataset items directly. Simplest, but blocks until the run finishes (fine inside our async snapshot job; **not** for a request handler).
- **Async (large runs):** `POST …/runs?token=…` → poll `…/runs/{id}` until `SUCCEEDED` → `GET …/datasets/{defaultDatasetId}/items`. Use for big channels / batch mode.

### Sketch (per-video, json3 → our segment shape)

```python
# fetch_segments replacement — verify field names against one real run first.
import os, httpx

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
_ACTOR = "karamelo~youtube-full-channel-transcripts-extractor"

async def fetch_segments(self, item) -> list[dict]:
    url = f"https://api.apify.com/v2/acts/{_ACTOR}/run-sync-get-dataset-items"
    payload = {"urls": [item.url], "outputFormat": "json3", "maxVideos": 1, "maxRetries": 5}
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(url, params={"token": APIFY_TOKEN}, json=payload)
        r.raise_for_status()
        items = r.json()
    if not items:
        raise RuntimeError(f"No captions for video {item.source_id}")
    events = _extract_json3_events(items[0])   # <-- confirm the actual key holding json3
    return [
        {"start": e["tStartMs"] / 1000,
         "duration": e.get("dDurationMs", 0) / 1000,
         "text": "".join(s.get("utf8", "") for s in e.get("segs", []))}
        for e in events if e.get("segs")
    ]
```

---

## Failure states

| State | Likelihood | Behaviour / handling |
|---|---|---|
| Run FAILED | Very low (2/475 in 30d) | Non-zero exit; treat as retriable. Wrap in our existing retry ladder. |
| Run ABORTED / timed-out | Low (16/475) | Usually resource/time caps on huge channels. Use `maxVideos`, batching, or per-video mode. |
| Video blocked despite `maxRetries` | Occasional | That video returns empty/absent transcript — same "no captions" outcome we already handle. Don't fail the whole snapshot. |
| Video genuinely has no captions | Common, expected | Empty result. Skip the video (current behaviour). |
| Empty dataset returned | Occasional | Bad/unavailable URL, or all videos blocked. Raise per-video, continue snapshot. |
| Actor changed/deprecated by author | Low but **out of our control** | Third-party supply-chain risk. Pin behaviour, monitor, keep `youtube-transcript-api` as a documented fallback. |
| Output field names change | Medium over time | Our mapping breaks silently → empty transcripts. Add a shape assertion + alert. |
| `APIFY_TOKEN` missing/invalid/over-quota | Deterministic | 401/403. Fail loud at startup like `YOUTUBE_API_KEY` does. |
| Latency (cold start + scraping) | Always | Seconds to minutes. **Only viable in the async snapshot pipeline**, never a sync endpoint. |

---

## Rate limiting & concurrency

- **YouTube-side blocking:** handled *inside* the actor via proxy rotation + `maxRetries`. This is the whole point — it's why it succeeds where our library fails.
- **Apify-side concurrency:** your Apify **account** has a max-concurrent-actor-runs cap (plan-dependent). Per-video mode (option A) can hit this if we fan out — throttle our side, or prefer batch mode.
- **Actor-internal concurrency:** `maxParallelRequests` (default 50) — higher is faster but uses more proxy resources and may raise cost/instability. Tune down if you see aborts.
- **Our YouTube Data API quota is unaffected** in the hybrid design — listing still costs the same handful of quota units; only captions move to Apify.

---

## Security & ops

- New secret: **`APIFY_TOKEN`** — store alongside `YOUTUBE_API_KEY`/`OPENAI_API_KEY`, never commit. Validate at startup (fail loud), matching how the connector already guards `YOUTUBE_API_KEY`.
- **Pin the actor build** (currently `0.0.187` / `TEZMcYN1cIakMKi8D`) rather than `latest`, so an upstream change can't silently alter output shape mid-flight. Upgrade deliberately after re-verifying the mapping.
- Data egress: transcripts flow through a third party's proxies. For public sermon content this is low-sensitivity, but note it for any future private sources.

---

## Cons / risks (the honest list)

1. **Supply-chain dependency** on a single community author. It could break, deprecate, re-price, or change output without notice. Mitigate: pin the build, keep our old path as a fallback, alert on empty-transcript spikes.
2. **Recurring cost** ($15/mo rental, plus possible platform compute) vs. the current $0 library.
3. **Latency** unfit for synchronous use — fine because ingestion is already async, but it constrains where the call can live.
4. **Output shape unverified** — exact field names need one trial run before the mapping is trustworthy.
5. **No duration filter** in the actor — forces the hybrid design (which is fine, but it means we can't fully retire the Data API).

## Pros

1. **Actually fetches transcripts from datacenter IPs** — solves the real blocker.
2. **Timestamps preserved** via `json3` → deep-links and `timestamp_start_ms` keep working.
3. **Batch-capable** — one run can cover an entire channel.
4. **Mature and reliable** — ~30k runs, 4.92★, ~99.6% non-failed over 30 days, maintained this month.
5. **Small, surgical diff** — hybrid touches only `fetch_segments`.

---

## Open questions to resolve before building

1. **Confirm live pricing** on the actor page while logged in, and whether Apify platform compute/proxy is billed on top of the $15/mo rental.
2. **Run one trial** (a single known video, `outputFormat=json3`) and capture the exact dataset item JSON so `_extract_json3_events` targets the real key.
3. Decide **per-video (A) vs. batch (B)** based on typical channel size and Apify concurrency limits on our plan.
4. Confirm our **Apify plan's max concurrent runs** to size throttling.
5. Decide the **fallback policy**: keep `youtube-transcript-api` as an automatic fallback, or fail the video and move on?

## Suggested rollout

1. Trial run → capture output shape (resolves Q2). 
2. Add `APIFY_TOKEN` (startup validation) + pin the actor build.
3. Implement option **A** in `fetch_segments` behind a flag (e.g. `YT_CAPTIONS_BACKEND=apify|library`), so we can flip back instantly.
4. Verify on the **Pastey Bot** workspace: re-ingest a few known-good sermons, confirm segments + `?t=` deep-links render.
5. If per-run overhead/cost is a problem, implement batch mode **B** in the snapshot runner.
