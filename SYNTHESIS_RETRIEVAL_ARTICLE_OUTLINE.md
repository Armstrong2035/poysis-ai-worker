# Synthesis & Retrieval: How Poysis Turns Transcripts Into Answers

**Working outline for a technical article — pick a direction before drafting.**

This traces the pipeline from raw transcript → semantic chunk → vector → retrieval → synthesized answer, and the decisions made at each fork. Sourced from the code (`transcript.py`, `knowledge/engine.py`, `vector_store.py`, `chat.py`) and the build history.

---

## Pick a direction first

The same material can be told three ways. Choose one so the draft has a spine:

- **A — "The decisions" (recommended).** Chronological by problem. Each section is a fork in the road: what broke, the options, why we chose one. Reads like an engineering diary. Best fit for "trace all the decisions that were made."
- **B — "The pipeline."** Follow one transcript through the system, stage by stage. Cleaner as a reference doc / onboarding piece, but flattens the *why*.
- **C — "One principle."** Frame everything around a thesis — e.g. *"code answers when it can; the model answers only judgment calls"* — and use each stage as evidence. Punchiest, most opinionated, most publishable. Riskier: forces the story into one shape.

Outline below is written for **A**, with the section content reusable for B or C.

---

## Working titles

- *When, Not Just What: Building Timestamped Retrieval Over 500 Sermons*
- *From 60-Second Windows to Topic Boundaries: A Semantic Chunking Story*
- *Retrieval vs. Synthesis: Letting the Question Decide*

---

## 0. Cold open

- The problem in one sentence: a creator's 500-video body of work is unsearchable; we want a bot that answers *in their voice*, cites the exact moment, and never hallucinates.
- The two jobs that fall out of that: **retrieval** (point me to the source) and **synthesis** (interpret across the whole body of work). The article is how each got built.

## 1. Chunking transcripts — the first fork

- **Decision: capture *when*, not just *what*.** Original driver ("the point of this pipeline is to capture not just what was said, but when — so retrieval can give the user timestamps"). Every chunk carries `timestamp_start_ms`/`end_ms` and a `?t=` deep-link.
- **Decision: a source-agnostic `TranscriptProcessor`.** YouTube isn't the only source with timed segments (Drive recordings, Zoom). Refactored into a module that consumes `{start, text, duration}` from *any* connector. (`transcript.py`)
- **Decision: separate namespace / treatment for transcripts.** Transcripts behave differently from text docs — kept distinct so they don't "mix with other data."
- **Decision: do we even need LlamaIndex here?** For text docs we chunk with `SentenceSplitter(512, overlap 50)`. For transcripts we deliberately *skip* it — sentence splitting would shred timestamp boundaries.

## 2. Semantic chunking — the core idea

- **The naive baseline: fixed 60-second windows.** `_WINDOW_SECONDS = 60`. Simple, but a topic boundary rarely lands on a clean minute — you get chunks that straddle two ideas.
- **The turn: "I like topic segmentation. How do we implement that?"** Move from clock-based to *meaning*-based boundaries.
- **The two-pass design** (`embed_and_store`):
  - Pass 1 — embed the 60s pre-chunks, walk the **cosine-similarity curve** between adjacent chunks, smooth it (moving average), and cut at **valleys below threshold (0.75)** = topic shifts.
  - Merge adjacent same-topic pre-chunks into one **topic chunk**; re-embed *those* for storage.
- **Why 60s windows still exist:** they're the raw material for segmentation, not the final unit. Worth explaining — it's the non-obvious bit.
- **Decisions to surface:** why cosine on OpenAI embeddings = dot product (L2-normalized); why smoothing (noise in the curve); the 0.75 threshold as a tuning knob; "how accurate is the similarity measurement?" as an honest sidebar.

## 3. Embedding & storage

- `text-embedding-3-small`, batched (512), sub-batched (200) with 429 back-off.
- Storage: Supabase **pgvector**, HNSW index, `_text` stored alongside the vector so retrieval needs no second store.
- **Decision worth a callout: the HNSW iterative-scan fix.** Namespace is a *post-filter* on the index, so a workspace holding a fraction of a shared table returned only ~5 rows for any `top_k`. `SET LOCAL hnsw.iterative_scan = relaxed_order` walks the graph until enough in-namespace rows pass (5 → 48). Great "the index lied to us" war story.

## 4. Clustering — themes as a byproduct

- BERTopic over document centroids assigns each source a `category_id` + `key_themes`.
- Where it pays off later: synthesis answers surface the *recurring themes* the excerpts belong to, and owners can **scope** a bot to approved topics. (Keep this section short — it's supporting cast, not the lead.)

## 5. Retrieval — the shared core

- **Over-fetch, then narrow.** Fetch `top_k × 6` candidates so diversity has material to work with.
- **`min_score` floor** guards the "nothing relevant" case.
- **Decision: score-gap detector over a fixed cutoff.** ("Gap detector seems better.") Find the biggest drop in the sorted score list and cut at the natural relevance cliff instead of an arbitrary N.
- **Decision: source diversification.** Round-robin across per-source buckets so one video can't dominate the answer.
- **Scoping:** `connection_id` (which channel) and `topic_ids` (owner-approved categories); the empty-list-is-a-real-allowlist subtlety.

## 6. Retrieval vs. synthesis — letting the question decide

- **The realization:** chat started synthesis-only. Not every question wants an essay — "find the sermon titled X" wants a *pointer*.
- **Decision: an intent classifier, not embedding proximity.** The distinction is verb/intent-driven ("find/list" vs "what does he teach about"), so a cheap LLM classifies it; embeddings key on topic, which is the wrong signal.
- **Decision: run the classifier *concurrently* with retrieval** so its latency hides behind the network-bound fetch. Fails toward synthesis — the classifier can never make a query *worse*.
- **The MCP framing:** smart clients (Claude, ChatGPT) can synthesize themselves and just want retrieval; dumb clients (Poysis dashboard, Slack, Telegram) need the server to synthesize. That's *why* both are exposed as tools.

## 7. Synthesis — making it sound like the creator

- **The failure that forced this:** the bot kept returning *"I couldn't find relevant information"* to real questions ("how do I improve my prayer life"). An extraction-style prompt refuses anything not stated verbatim — but chunks are *excerpts*, so that's most questions.
- **Decision: a synthesis contract prompt** that permits inference *across* excerpts while staying grounded. Voice = "interpreter of a body of work," creator-centered ("he teaches…" not "the documents say…").
- **Decision: persona is layered *on top of* the contract, not swapped in.** Bot branding used to replace the system prompt and silently drop the grounding rules; now the contract binds last/hardest.
- **Grounding guardrails as code, not vibes:** `_quote_grounded` (a "key quote" must actually appear in a source — word-overlap check); "never fabricate quantities like 'dozens of sermons.'"
- **Model tiers** (`quick`/`thinking`/`expert`) — client sends a tier name, not a model ID, so we swap models without a client release. (Touch on the OpenRouter/Gemini/billing saga only if the article wants a "reliability & cost" aside — probably a sidebar, not a section.)

## 8. The response contract

- One streaming response, two modes: synthesis streams prose then `__SOURCES__` then `__META__`; retrieval leads with `__MODE__`, emits sources + leaner meta, **no LLM call at all**.
- `__META__` = scale (sources/excerpts) + themes (from clustering) + one grounded key quote. Each degrades independently so a slow part never blocks the rest.
- Every turn logged as a `topic_query_event` — graph + training data.

## 9. Closing — the throughline

- Candidate thesis: **deterministic where possible, model only for judgment.** Code decides *where* to cut (score gap), *which* sources (diversify), *whether* it's grounded (quote check); the model is used only for the two genuine judgment calls — classify the intent, write the prose.
- Honest "what's still rough": threshold tuning is hand-set; classifier is a single cheap call; no short-term memory in chat yet.

---

## Open questions for you

1. **Direction A, B, or C?**
2. **Audience** — engineers who'd build this, or a broader "how Poysis works under the hood" post? Changes how deep sections 3 & 5 go.
3. **How much of the messy reality** (YouTube IP bans, Apify vs yt-dlp, the billing/OpenRouter scramble) makes the cut? It's great texture but off the main thread — sidebar or omit?
4. **Include clustering (§4)** as its own beat, or fold it into synthesis as "where themes come from"?
