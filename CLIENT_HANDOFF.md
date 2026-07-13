# Client Session Handoff — Canvas rebuild, ceiling enforcement, document browsing

Written from the Next.js client repo (`poysis-client`) at the end of a long session, for whoever (human or agent) picks up worker-side work next. Covers: what changed on the client, what was already changed here in the worker repo (needs redeploy + verification), and what's still open.

---

## 1. What changed on the client, briefly

- **Cluster exposure ceilings are now real.** A new `cluster_ceilings` table lives in the *client's* Supabase (not this worker's DB) — `private` / `team` / `public`, keyed by `(topic_id, owner_user_id)`. Team is UI-locked (Teams & Permissions isn't built). This table has **not yet been confirmed applied** to the live client DB — same class of "migration file exists but was never run" issue described in §4.
- **Real enforcement added** in the client's `notebook/[id]/chat` proxy route: a non-owner's requested topic scope is recomputed server-side against `cluster_ceilings`, never trusted as-is from the request body. Also fixed a bug where a client-supplied `notebook_id` in the body (distinct from the URL id) could redirect a query to a notebook that was never ownership-checked.
- **Canvas was rebuilt from a localStorage mock onto the real `notebooks` table** — it now uses the exact same Zustand store (`app/store/notebookStore.ts`) and Supabase row as the old block-based builder. One notebook = one row = one block (chat or search). Canvas-only fields (ceiling, memory, onboarding progress, model tier) live under a new `config.canvas` JSON key that both the old builder's autosave and Canvas's autosave now preserve (neither clobbers the other's unknown keys).
- **Publish is real**: a notebook gets an auto-generated `slug` and a working `/p/<slug>` link, protected by the ceiling enforcement above.
- **A chat-driven onboarding wizard** (add cluster → chat vs. search → persona/model/result-shape) drives all of this conversationally, backed by a new `docs/ouroboros.md` philosophy doc the client can inject as system instructions (see §3).

None of the above required worker changes **except** the two things below, which do.

---

## 2. Worker changes already made in this repo — need redeploy

| Change | File | Status |
|---|---|---|
| `list_documents_with_snippets` accepts optional `topic_id` filter (`metadata->>'topic_id' = %s`) | `app/primitives/knowledge/vector_store.py` | Written, not deployed |
| `/list_documents` route accepts `topic_id` query param, passes through | `app/blocks/retrieval/router.py` | Written, not deployed |
| **Real bug fix**: `/list_documents` was querying `namespace=workspace_id` directly — wrong. Every other real retrieval path (`/search`, ingestion) uses `namespace=f"consolidation_{workspace_id}"`. This meant `/list_documents` silently returned nothing, always, regardless of the `topic_id` work above — a pre-existing bug, unrelated to this session's changes, just never noticed because nothing in the client called this endpoint before now. | `app/blocks/retrieval/router.py` (same route) | Fixed, not deployed |

**Both syntax-checked (`python -m py_compile`), not tested against a live DB, not deployed.** Redeploy, then verify `/retrieval/list_documents?workspace_id=X&topic_id=Y` actually returns a filtered subset for a real workspace with a real topic.

---

## 3. Open questions for the worker side — please verify, don't assume

### 3a. Does `topic_id` in `consolidation_topics` actually match `metadata.topic_id` in `vectors`?

I got this wrong once already mid-session by reading the wrong module (`bertopic_handler.py`, which turns out **not** to be the active clustering path — I was corrected: it's `ClusteringEngine` → `CategorizerEngine` in `app/primitives/consolidation/clustering.py` / `categorizer.py`). I never actually read `categorizer.py` to confirm how it allocates/writes `topic_id`. Before trusting the `topic_id` filter added in §2, someone should:
- Read `app/primitives/consolidation/categorizer.py` to see exactly how topic ids are assigned and whether they're the same value written into `consolidation_topics.topic_id` (via `database.py::save_topics`) and into each chunk's `metadata.topic_id` in the `vectors` table.
- Run one real query joining the two on a live workspace to confirm the id spaces actually line up (`->>'topic_id'` returns text regardless of underlying JSON type, so a format mismatch — e.g. int vs. string, or a completely different id scheme — would silently return zero rows, which looks identical to "the topic has no documents").

### 3b. Does `/chat` honor `instructions` the same way `/retrieval/ask` does?

The client's dashboard chat proxy (`worker/chat` route, calls this worker's `/chat`) now optionally sends an `instructions` field (system-prompt content from `docs/ouroboros.md`, client-side) when a client flag (`useOuroboros`) is set — used during Canvas's onboarding chat to ground the model's guidance in a real philosophy doc instead of unguided free response. This mirrors how `/retrieval/ask` already accepts `instructions`. **Never verified that `/chat` (as opposed to `/retrieval/ask`) actually reads and applies it.** If it silently ignores unknown fields, onboarding guidance just won't be grounded — not a crash, just quietly wrong.

### 3c. `allowed_topic_ids` / `allowed_connection_ids` must be a strict allowlist, not a soft hint

This is the one that actually matters for the "private cluster" security guarantee described in §1. The client-side ceiling enforcement filters the topic ids it sends down to only what a given caller is allowed to see — but that's only meaningful if this worker's `/chat` and `/retrieval/ask` **never fall back to a broader scope** when the list is empty, missing, or looks unexpected. If there's any code path where "no topic filter" is interpreted as "search everything" for a request that should have been scoped, that's a real data exposure, independent of anything the client does. Worth an explicit read-through of both endpoints' retrieval-scoping logic with this specific question in mind, not just a general review.

---

## 4. A pattern worth knowing about, since it bit us once already

`docs/ouroboros.md`-style "migration exists in the repo but was never applied to the live database" bugs happened twice on the client side this session (once for a `cluster_ceilings` table, once for a `notebooks.slug` column that had been sitting unapplied since before this session started). If this worker repo has an equivalent — schema migrations, alembic scripts, whatever — that aren't part of an automatic deploy step, it's worth double-checking anything relevant here (e.g., does `consolidation_topics` actually have the columns `database.py::save_topics` expects, live, right now?) rather than assuming the code and the live schema agree.

---

## 5. Deferred / not started — mentioned in case it changes worker priorities

- **Retrieval-driven cluster creation**: product direction is "describe a cluster in natural language → a new endpoint runs semantic search and creates a real cluster from the matches" (explicitly *not* a manual document-checkbox picker — that was considered and rejected as going back to a form-filling paradigm the rest of the product has moved away from). Not built. Would need: a new endpoint (workspace_id + free-text description in, a new `topic_id` + set of tagged documents out), reusing the same retrieval/embedding path `/search` already uses. This depends on resolving §3a first — you can't allocate a coherent new topic id without knowing the real id-allocation scheme.
- **Advanced tuning sliders** (creativity, response length, results-per-search) are now persisted in Canvas's notebook config (`stateSettings.creativity` / `maxTokens` / `limit` — same fields the old builder already used) but **not yet wired into either chat request path** (Canvas's dashboard chat, the `/p/<slug>` playground chat both currently send fixed values). This is a client-side wiring gap, not a worker gap — flagging only so it's not mistaken for "the worker ignores these."
