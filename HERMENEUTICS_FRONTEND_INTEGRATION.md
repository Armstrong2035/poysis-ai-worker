# Frontend integration: marketing signals and evidence explanations

This guide describes the production APIs now available to Poysis clients and the
recommended frontend flow. The service explains evidence; it does not make decisions
for the user or return recommendations unless a future endpoint explicitly asks for
them.

## Production locations

- Backend: `https://api.poysis.com`
- Studio: `https://studio.poysis.com`
- Existing Studio page: `/workspace/interpretations`
- Existing Studio proxy: `/api/interpretations`

The browser should use the Studio proxy for interpretation requests. It forwards the
current bearer token and keeps the backend URL server-side. Other clients may call the
backend directly.

All backend requests require:

```http
Authorization: Bearer <access-token>
```

Every route also requires `workspace_id` as a query parameter. The API verifies that
the authenticated user belongs to that workspace. Do not derive workspace identity
from free-form chat text.

## What is available

The deployed system has two related API groups:

1. `/marketing` exposes structured observations, configurable opportunity rules,
   natural-language rule previews, detected opportunities, and imported Keyword
   Planner snapshots.
2. `/interpretations` runs the hermeneutic engine. It retrieves structured metrics,
   document chunks, and prior interpretations, then returns an explanation, competing
   readings, missing context, confidence, and evidence links.

GA4 and Search Console observations must already exist in the structured marketing
store before they appear as selectable datasets. Google account connection and
scheduled synchronization are separate integration work. Keyword Planner currently
accepts imported report snapshots; it does not call Google Ads live.

## Recommended interpretation flow

### 1. Load selectable metric datasets

Through the existing Studio proxy:

```http
GET /api/interpretations?workspace_id=<workspace>&kind=datasets
Authorization: Bearer <access-token>
```

Direct backend equivalent:

```http
GET https://api.poysis.com/interpretations/datasets?workspace_id=<workspace>
```

Response:

```json
{
  "data": [
    {
      "source": "search_console",
      "property_id": "sc-domain:example.com",
      "dataset": "query-page-web-v1",
      "metrics": ["clicks", "impressions", "position"],
      "start": "2026-06-01",
      "end": "2026-09-15"
    }
  ]
}
```

Only additive or safely aggregatable metrics are offered. Let the user select up to
four datasets. A document-only interpretation is valid, so an empty dataset list
should not disable the form.

### 2. Frame the question

The form or chat experience must turn the user's request into an explicit frame. The
question remains natural language, while dates and dataset identities remain typed.

```json
{
  "question": "Why are organic signups falling?",
  "frame": {
    "domain": "marketing",
    "objective": "Understand customer acquisition performance",
    "period": {
      "start": "2026-08-18",
      "end": "2026-09-14"
    },
    "comparison": {
      "start": "2026-07-21",
      "end": "2026-08-17"
    },
    "datasets": [
      {
        "source": "search_console",
        "property_id": "sc-domain:example.com",
        "dataset": "query-page-web-v1",
        "metrics": ["clicks", "impressions", "position"],
        "dimensions": {}
      }
    ],
    "definitions": {
      "signup": "A completed account registration"
    },
    "assumptions": [],
    "user_assertions": [],
    "include_documents": true,
    "include_previous_interpretations": true
  }
}
```

Rules enforced by the API:

- `question` and `objective` are required and limited to 4,000 characters.
- A period may span at most 366 days.
- The comparison period is optional. When supplied, it must immediately precede or
  otherwise end before the analysis period and have exactly the same duration.
- At most four datasets and eight metrics per dataset are allowed.
- Dataset sources are currently `ga4` and `search_console`.
- Unknown fields are rejected.

The current Studio form omits `comparison`; the engine still retrieves the preceding
equal-length period for structured comparisons. A richer client may submit it
explicitly when the user chooses a comparison window.

### 3. Submit an asynchronous interpretation

Generate one UUID for each logical submission and reuse it when retrying the same
request.

```http
POST /api/interpretations?workspace_id=<workspace>
Authorization: Bearer <access-token>
Content-Type: application/json
Idempotency-Key: <uuid>
```

The backend returns `202 Accepted`:

```json
{
  "id": "4f0d60a9-b7fd-4f66-a0e3-c1ee617acd81",
  "status": "queued",
  "created_at": 1789552800.25,
  "attempts": 0,
  "error_code": null,
  "interpretation": null
}
```

Reusing the key with the identical body returns the same job. Reusing it with a
different body returns `409`. Keep the key with the draft request until the user
materially edits the question or frame.

### 4. Poll for completion

```http
GET /api/interpretations?workspace_id=<workspace>&id=<interpretation-id>
Authorization: Bearer <access-token>
```

Poll approximately every two seconds while the page is active. Stop on `completed`
or `failed`. A ten-minute UI timeout is reasonable, but retain the interpretation ID
so the user can resume polling. The durable backend job continues if the browser
closes.

Possible statuses are:

- `queued`
- `running`
- `completed`
- `failed`

Failure codes currently include `evidence_access_denied`, `invalid_model_output`,
`model_timeout`, `interpretation_failed`, and `retry_limit`. Present a useful retry
message rather than raw provider details.

### 5. Render explanations with evidence

On completion, `interpretation.synthesis` is the user-facing result:

```json
{
  "outcome": "explained",
  "primary": {
    "thesis": "Organic acquisition weakened primarily because search visibility fell.",
    "claims": [
      {
        "statement": "Impressions declined in the selected query cluster.",
        "evidence": [
          {
            "evidence_id": "evidence-hash",
            "relationship": "supports",
            "rationale": "The current period is below its comparison period.",
            "weight": 0.9
          }
        ],
        "limitations": ["Search Console coverage is not a complete traffic census."]
      }
    ]
  },
  "alternatives": [],
  "missing_context": ["Current indexation coverage was not available."],
  "confidence": {
    "level": "moderate",
    "rationale": "The trend is measured, but indexation evidence is missing."
  }
}
```

Render these sections in order:

1. Outcome: explanation or insufficient evidence.
2. Primary thesis and its claims.
3. Confidence and its rationale.
4. Alternative interpretations.
5. Missing context.
6. Source evidence.

Each claim contains evidence links. Resolve `evidence_id` against the objects in:

- `interpretation.context.facts`
- `interpretation.context.signals`
- `interpretation.context.relevant_documents`
- `interpretation.context.previous_interpretations`

Supported relationships are `supports`, `contradicts`, `qualifies`, `explains`,
`correlates_with`, `precedes`, and `depends_on`. Show the relationship and rationale
beside the claim, and link to the corresponding evidence card. Always show evidence
limitations and the source locator.

When `outcome` is `insufficient_evidence`, `primary` is `null`. Treat this as a valid
answer and emphasize `missing_context`; do not turn it into a generic error.

The full normalized provenance view is also available directly from the backend:

```http
GET https://api.poysis.com/interpretations/<id>/evidence?workspace_id=<workspace>
```

It returns `{ "objects": [...], "links": [...] }`. The current Studio proxy does not
yet forward the `/evidence` suffix because the completed interpretation already
contains its context and claim links.

## Existing Studio implementation

The deployed implementation already provides:

- A sidebar entry named **Explanations**.
- Dataset discovery.
- A question, objective, date range, and dataset selection form.
- Idempotent submission and two-second polling.
- Resume and retry controls.
- Primary and alternative readings.
- Confidence, missing context, evidence cards, and claim-to-evidence anchors.

Use it as the reference implementation:

- `apps/studio/app/(workspace)/workspace/interpretations/page.tsx`
- `apps/studio/app/(workspace)/workspace/interpretations/InterpretationPanel.tsx`
- `apps/studio/app/api/interpretations/route.ts`

The proxy uses `HERMENEUTICS_WORKER_URL` when configured and otherwise defaults to
`https://api.poysis.com`. It requires a bearer token, limits request bodies to 64 KB,
uses a 30-second upstream timeout, disables caching, and converts upstream 5xx errors
into a generic 503 response.

## Structured marketing APIs

Clients that need the underlying data and deterministic opportunity controls can use
the backend directly. All routes require bearer authentication and `workspace_id`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/marketing/data` | Paginated structured observations for a date window and exact dimension filters |
| `GET` | `/marketing/rules` | List saved opportunity rules |
| `GET` | `/marketing/rules/{rule_id}` | Read one rule and its human-readable summary |
| `POST` | `/marketing/rules/preview` | Convert a natural-language target into a proposed rule without saving it |
| `PUT` | `/marketing/rules/{rule_id}` | Save a rule using optimistic revision control; workspace owner only |
| `GET` | `/marketing/opportunities` | Evaluate one saved rule and return matching evidence |
| `POST` | `/marketing/keyword-planner/reports` | Import a Keyword Planner snapshot; workspace owner only |
| `GET` | `/marketing/keyword-planner/reports` | List snapshots for a customer and dataset |
| `GET` | `/marketing/keyword-planner/reports/{snapshot_id}` | Filter and paginate keywords in a snapshot |

Natural-language targets follow a preview-then-save flow:

```json
POST /marketing/rules/preview?workspace_id=<workspace>

{
  "text": "Find invoice automation queries with at least 200 impressions, position 1 to 8, and CTR below 3%",
  "property_id": "sc-domain:example.com",
  "dataset": "query-page-web-v1"
}
```

Display the proposed rule and summary for human review. Saving is a separate owner-only
request. The preview does not change configuration. Rule saves require
`expected_revision`; a `409` means another editor changed or removed the rule, so
reload before presenting another save.

Opportunity results are explanations of why rows matched configured thresholds. They
are not recommendations. Keyword filters can target exact or containing phrases.
Keyword Planner competition describes paid-ad competition, not organic ranking
difficulty, and bid values are integer micros in the report currency.

See `MARKETING_SERVICE.md` for the complete rule and Keyword Planner payloads.

## Error handling

| Status | Meaning | Client behavior |
|---|---|---|
| `400` | Invalid proxy parameter or request shape | Keep the form and identify the invalid field |
| `401` | Missing or expired bearer token | Refresh authentication or send the user to login |
| `403` | User lacks workspace access or owner permission | Explain that access or owner rights are required |
| `404` | Job, rule, report, or workspace-scoped object was not found | Return to the relevant list; do not leak cross-workspace existence |
| `409` | Idempotency or optimistic-revision conflict | Reuse the original body or reload the latest rule |
| `413` | Studio proxy body exceeds 64 KB | Reduce submitted context |
| `422` | Frame, date, filter, or typed contract is invalid | Display the API detail near the form |
| `502` | Natural-language target could not be parsed reliably | Ask the user to rephrase or use manual controls |
| `503` | Model, target interpreter, or upstream service unavailable | Preserve the draft and offer retry |

Do not display raw provider responses, prompts, access tokens, or internal exception
text. Do not cache workspace responses in shared browser or CDN caches.

## Chat entry point

Chat can be the best way to collect the question and interpretive frame, but it should
still submit the typed `InterpretationRequest` contract shown above. The chat layer may
ask for missing dates, objective, definitions, and datasets. It must not invent
workspace scope or silently save opportunity rules.

A useful chat handoff is:

1. Detect that the user is asking for an explanation.
2. Collect or infer a draft frame and show editable dates and sources.
3. Ask the user to run the analysis.
4. Submit through `/api/interpretations` with an idempotency key.
5. Render the same evidence-linked result component used by the Explanations page.

Keep recommendations as a separate, explicit user request. The interpretation result
should remain an explanation with evidence, alternatives, gaps, and confidence.
