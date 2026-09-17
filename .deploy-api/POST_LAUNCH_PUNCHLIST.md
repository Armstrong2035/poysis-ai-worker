# Post-Launch Punch List

Deferred from the beta launch (10 users, 2026-06-05). None of these were required to
ship safely — recovery already works via snapshot resume (see "Why we shipped without them").
Do these *after* invites are out, with time to test, because all three touch the
background-job path (`_run_snapshot_job`).

---

## 1. Email the user when consolidation completes

**Provider:** Resend. `RESEND_API_KEY` already exists in `.env`. `httpx` is already a
dependency — call Resend's REST API directly, no new package.

**Hook point:** right after `await db.update_job(job_id, "done", result=final_result)`
in [`app/api/consolidation.py:116`](app/api/consolidation.py#L116). Fire-and-forget so a
mail failure never fails the job.

**Open decision — where does the email address come from?** The backend only knows the
user by the `X-User-ID` header; it has no email. Options:
- **Supabase `auth.users` via Admin API** (service-role key, already configured). Works for
  every signed-up user, no schema change. *Recommended.*
- **Stored Google email** — OAuth already calls `fetch_google_email`; persist it on
  `consolidation_workspaces`. Only covers users who connected Drive.
- **Frontend passes it** in the `/snapshot` body. Simplest server-side but trusts client input.

**Also need:** a verified Resend sender domain/from-address before this can send.

---

## 2. Clustering-phase heartbeat

**Problem:** the heartbeat (`touch_job`) only fires during indexing via `_on_progress`
([`consolidation.py:64`](app/api/consolidation.py#L64)). `run_clustering`
([`consolidation.py:97`](app/api/consolidation.py#L97)) runs for minutes with no heartbeat,
so a healthy clustering job looks stale (>300s) and a genuinely hung one has no timeout.

**Fix:** touch the job row periodically during clustering (either a callback from
`ClusteringEngine.run_clustering`, or a background `touch_job` ticker around the call).

---

## 3. Step logging / observability

**Problem:** [`engine.py`](app/primitives/consolidation/engine.py) and
[`clustering.py`](app/primitives/consolidation/clustering.py) have ~2 print lines each.
If a job hangs you see `[Snapshot] Iteration N` then silence — can't tell indexing-hang
from clustering-hang from logs alone.

**Fix:** ~5 targeted log lines at phase boundaries (fetch start/end, index start/end,
cluster start/end) with counts. Ties into the standing observability requirement to track
token usage + service calls at every pipeline step.

---

## 4. Admin remote-debug / resume endpoint

**Today:** status + resume already work, but every consolidation endpoint is gated by
`verify_workspace_ownership` — an operator who doesn't own the workspace can't inspect or
re-trigger another user's job. Recovery currently means hitting the API as that user.

**Fix:** an admin-only (service-role / allowlisted) endpoint to view any workspace's job
status + error and re-trigger its snapshot. Resume itself needs no new logic — re-running
`/snapshot` already skips indexed files by etag
([`snapshot.py:108-112`](app/primitives/consolidation/snapshot.py#L108-L112)).

---

## Known caveats (annoyances, not data loss)

- `_jobs` is in-memory ([`consolidation.py:27`](app/api/consolidation.py#L27)) — a redeploy
  mid-job loses live state. DB row + `consolidation_indexed_files` survive, so recovery is
  unaffected.
- A stuck job blocks re-trigger with a 409 for up to 5 min until the stale-reaper clears it
  ([`consolidation.py:176-182`](app/api/consolidation.py#L176-L182)).

## Why we shipped without them (the recovery story that already works)

- Progress is flushed per-batch, not at the end — `_flush_completed_files`
  ([`engine.py:84`](app/primitives/consolidation/engine.py#L84)). A crash at 80% keeps the 80%.
- Re-running `POST /consolidation/snapshot` resumes: indexed files are skipped by etag, then
  clustering re-runs (idempotent via `UNIQUE(workspace_id, topic_id/story_id)`).
- Stuck jobs auto-reap after 5 min so a re-trigger isn't blocked.

**Manual recovery runbook (no code needed):**
1. `GET /consolidation/snapshot/status/{workspace_id}` → status + error.
2. Check Railway logs for the `[SNAPSHOT ERROR]` traceback.
3. Wait out the 5-min stale window if needed.
4. Re-trigger `POST /consolidation/snapshot` → resumes from where it died.
