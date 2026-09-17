# Hermeneutics engine service

Independent Python service core for evidence-backed explanations. A Poysis API
gateway now exposes it without coupling the core to chat or synthesis routing.
The core supports local SQLite and shared Postgres repositories. The configured
runtime uses direct DeepSeek inference and the existing Poysis evidence stores.

## Running the integrated service

1. Apply `migrations_create_interpretations.sql` to the same Postgres database as
   the backend. Also apply `migrations_create_marketing_observations.sql` if absent.
2. Keep the existing server-injected `DEEPSEEK_API_KEY`. No key is returned to the
   browser. `HERMENEUTICS_DEEPSEEK_MODEL` defaults to `deepseek-v4-pro`.
3. Set `HERMENEUTICS_ENABLED=true` to run a leased worker in the backend process,
   or run `python -m hermeneutics --factory hermeneutics.runtime:build_engine` as a
   separate worker. Postgres coordinates claims across backend/worker processes.
4. Studio uses its existing server-side `WORKER_URL` (or `LOCAL_WORKER_URL`). The
   Explanations navigation entry opens `/workspace/interpretations`.

The authenticated API requires `workspace_id` on every request:

| Route | Behavior |
| --- | --- |
| `GET /interpretations/datasets` | Available additive metrics from imported workspace reports |
| `POST /interpretations` | Submit question/frame with required `Idempotency-Key` header; returns 202 |
| `GET /interpretations/{id}` | Poll queued/running/completed/failed status and interpretation |
| `GET /interpretations/{id}/evidence` | Inspect evidence snapshots and claim links |

The gateway derives `client_id=poysis` server-side and verifies workspace
membership. That integration grants workspace-wide evidence access. Other clients
must supply their own authenticated gateway and grant policy; no arbitrary client
ID is accepted from the browser.

DeepSeek calls use its fixed HTTPS endpoint, JSON mode, an explicit output budget,
bounded timeouts and sanitized errors. There is no Bedrock fallback. Document
retrieval continues using the existing OpenAI `text-embedding-3-small` embedding
space, so its existing configuration is also needed. The DeepSeek key does not
replace the embedding provider's configuration.

Run `python scripts/test_hermeneutics_live.py` explicitly for a two-call live
DeepSeek smoke test using synthetic evidence and a temporary SQLite database.
This incurs provider usage but sends no customer documents. Passing this test
does not establish Postgres reachability, applied migrations, deployment, or
authenticated browser access to real workspace data.

## Pipeline

1. Validate an explicit marketing frame with objective, resolved date range,
   metric definitions, selected datasets, assumptions and user assertions.
2. Plan bounded structured queries for current/comparison periods, semantic
   document retrieval and recent prior interpretations. No generated SQL.
3. Assemble a versioned ContextPacket with Fact, Signal and DocumentChunk objects,
   source operations/status, coverage limitations, temporal context and gaps.
4. Make one strong-model call for an initial explanation and rival hypotheses.
5. Retrieve documents aimed at distinguishing those rivals, then make a second
   model call to challenge and synthesize the explanation with alternatives.
6. Validate citations and store both passes, the final packet, context hash,
   model/prompt versions and claim-level evidence relationships atomically.

An insufficient-evidence result is completed work, not a fabricated explanation.
If all attempted primary evidence sources fail, the job fails operationally.
Empty primary evidence skips model calls. No recommendations field is provided;
both model prompts explicitly require explanations and evidence only.

## Service interface

`await engine.submit(scope, request, idempotency_key)` returns a durable queued job.
`await engine.run_next()` claims and executes one queued job.
`await engine.get(scope, id)` returns scoped status and the completed result.
`await engine.evidence(scope, id)` returns immutable evidence snapshots and links.

Run a separate worker process with a host-owned factory that returns a configured
engine sharing the submission store:
`python -m hermeneutics --factory your_host.hermeneutics_config:build_engine`.
Add `--once` to process at most one job and exit. No chat/synthesis routing is wired.

The calling host must authenticate the client/user and authorize the Scope before
calling these methods. Scope is separate from question/frame data. It contains
both `client_id` and `workspace_id`, and both participate in storage isolation.
The trusted adapter factory must resolve grants for that pair. Constructing a
Scope object alone is not authentication. This core must not be exposed directly
to untrusted callers without that host boundary.

```python
from hermeneutics import HermeneuticsEngine, SQLiteRepository, Scope, InterpretationRequest
from hermeneutics.adapters import PoysisEvidenceAdapter
from hermeneutics.model import ConfiguredModel

scope = Scope(client_id="poysis", workspace_id="bezalel")  # authorized by host
request = InterpretationRequest.model_validate({
    "question": "Why are organic signups falling?",
    "frame": {
        "objective": "Understand changes in customer acquisition",
        "period": {"start": "2026-08-01", "end": "2026-08-28"},
        "comparison": {"start": "2026-07-04", "end": "2026-07-31"},
        "datasets": [{
            "source": "ga4", "property_id": "123456", "dataset": "organic-signups-v1",
            "metrics": ["eventCount"], "dimensions": {"eventName": "sign_up"}
        }],
        "definitions": {"organic-signups-v1": "Report filtered to organic acquisition; sign_up event count"}
    }
})
# All these are existing, host-configured clients; no setup occurs in the engine.
adapter = PoysisEvidenceAdapter(
    scope, marketing_store, dataset_grants=authorized_datasets,
    embedder=embedder, vector_store=vector_store, connection_ids=authorized_connections,
)
def adapter_factory(request_scope):
    if request_scope != scope:
        raise PermissionError("Unknown client/workspace")
    return adapter

engine = HermeneuticsEngine(
    SQLiteRepository("interpretations.sqlite3"), adapter_factory,
    ConfiguredModel(strong_model_client, model_version="your-configured-model-version"),
)
job = await engine.submit(scope, request, "client-request-123")
await engine.run_next()  # separate worker loop in a hosted deployment
result = await engine.get(scope, job["id"])
```

Configure the strong model's output budget explicitly (for example 6000 tokens),
temperature and provider before passing the client. It must implement async
`acomplete(prompt)`. Model calls have a 90-second timeout and responses are size
bounded and schema validated. This package does not select a weak utility model
as a silent fallback. Embedding calls are additional retrieval operations, not
part of the two interpretation calls. Frame formation from chat is deferred;
clients must currently supply a resolved frame.

## Evidence and retrieval behavior

Poysis metric retrieval uses the existing marketing store's workspace-scoped fetch.
Only explicitly granted properties, datasets, metrics and dimension restrictions
are accessible. The adapter aggregates registered additive metrics and rejects
distinct-user/rate aggregation. Datasets must have fixed, non-overlapping report
grains; completeness is unverified. Signals contain absolute/percentage change
and links to the two facts. Percentage change from zero is unknown, not infinity.
No cross-source attribution or keyword-level signup attribution is inferred.

Document retrieval uses `consolidation_{workspace_id}` with connection allowlists.
Full-workspace document access requires an explicit trusted-host grant. Chunks
preserve IDs, content-derived versions and source locators. Keyword Planner
research snapshots are not automatically treated as observed marketing facts.

The planner executes operations implied by the explicit frame rather than using
another model call. It retrieves the selected metrics, not an autonomous guess at
which event means signup. Initial/current and comparison operations run together.
The default comparison is the preceding equal-length period, disclosed as an
assumption. Documents are retrieved by relevance rather than filtered to the
analysis period; their timestamps and limitations remain visible.

Rival retrieval is currently semantic document retrieval. It cannot fetch new
external reports or select ungranted datasets. Structured evidence already covers
both periods; graph traversal and authoritative decision records are future
adapters, explicitly recorded as gaps. Previous interpretations are selected by
recency within the same client/workspace and labeled as hypotheses. They may
postdate the question's period. A claim cannot cite only a previous interpretation.

Context is capped at 60,000 characters, including provenance. Documents are at
most 4000 characters each. Budget omissions and retrieval failures are disclosed.
IDs in both model outputs must be present in the packet; fabricated citations,
cross-scope evidence and malformed JSON fail the job. Semantic correctness still
requires real-model evaluation: a valid citation ID does not prove entailment.
Qualitative confidence is not statistical calibration; incomplete context caps
high confidence at moderate.

## Durability and operations

SQLite initializes its own schema in a host-supplied persistent file. No marketing
database migration is required for interpretation storage. Completion and evidence
links are committed in one transaction. Idempotency keys are scoped to client and
workspace; reuse for a different request raises ConflictError.
`frame.period` identifies the time being interpreted; `valid_from` is the UTC
creation date of the interpretation, not a retroactive claim of historical validity.
`valid_until` remains null until a future explicit supersession policy is added.

Workers claim jobs with a 300-second lease, renew every 20 seconds, and fence
completion by lease ownership. A crashed worker's job can be reclaimed after
expiry, up to three attempts. A model call can repeat after a crash; only the
winning lease can persist a result. Explicit execution errors are terminal and
require a new submission/key after correction. Public errors contain categories,
not raw provider responses. Cancellation leaves the lease to expire for recovery.

The SQLite repository targets a single service host on local persistent disk.
The integrated runtime uses Postgres with transactional advisory locking for
short queue claims and lease ownership checks for completion. Apply the host's
retention and access policies before deployment. No cloud resource provisioning
or production deployment has been performed as part of the local implementation.

## Verification

Run `python -m unittest discover -s tests -p "test_hermeneutics*.py"`.
Tests run against real temporary SQLite files with deterministic model/evidence
fixtures; they do not invoke live providers or read credential material.
