# Marketing data service: client integration guide

This guide covers the HTTP API for the structured marketing service. It shows
the endpoints, the request shapes, and the UI flows the client must build.

The service stores daily marketing facts and turns a saved rule into a list of
opportunities. It does not connect to Google accounts by itself. The worker must
ingest the reports first. This guide does not cover ingestion.

## What the service gives you

- Read daily observations from Search Console or GA4 for one workspace.
- Create, preview, and edit opportunity rules.
- Evaluate a rule to get matching query/page segments.
- Import and filter Google Ads Keyword Planner snapshots.

The service does not forecast revenue, match topics by meaning, or compare
periods. Use it for the deterministic data it stores.

## Authentication

Every marketing request needs authentication. Use one of these two forms.

### User session (browser app)

Send the normal Poysis bearer token and a `workspace_id` query parameter.

```http
GET /marketing/rules?workspace_id=ws_123
Authorization: Bearer <user-jwt>
```

- Workspace members can read data, list rules, and preview rules.
- Only the workspace owner can save rules or import Keyword Planner reports.

### API key (server to server)

Send a scoped API key. The key carries its own workspace, so do not send
`workspace_id`.

```http
GET /marketing/rules
Authorization: Bearer poysis_live_<prefix>.<secret>
```

- API keys use the `marketing:read` scope.
- They can read only. They cannot save rules or import reports.
- A `workspace_id` that does not match the key returns 403.

## Pagination

List endpoints share one shape:

```jsonc
{
  "data": [ /* rows */ ],
  "limit": 100,
  "offset": 0,
  "next_offset": 100        // null when there are no more rows
}
```

- `limit` defaults to 100 and cannot exceed 1000.
- Pass `next_offset` back as `offset` to read the next page.

## Read observations

Get daily marketing facts for a date window.

```http
GET /marketing/data?workspace_id=ws_123
  &source=search_console
  &property_id=sc-domain%3Aexample.com
  &dataset=query-page-web-v1
  &start=2026-08-01
  &end=2026-08-28
  &dimensions=%7B%22device%22%3A%22DESKTOP%22%7D
```

| Parameter | Required | Meaning |
|---|---|---|
| `source` | yes | `search_console` or `ga4` |
| `property_id` | yes | Property or site identity, for example `sc-domain:example.com` |
| `dataset` | yes | Versioned report definition name |
| `start`, `end` | yes | `YYYY-MM-DD`; the window cannot exceed 366 days |
| `dimensions` | no | JSON object of exact string filters, URL encoded |
| `limit`, `offset` | no | Pagination |

`dimensions` filters rows at an exact dimension value. This example returns only
desktop rows:

```jsonc
{"device": "DESKTOP"}
```

The response:

```jsonc
{
  "data": [
    {
      "day": "2026-08-01",
      "dimensions": { "query": "invoice automation", "page": "/pricing" },
      "metrics": { "clicks": 12, "impressions": 640, "position": 6.3 },
      "ingested_at": "2026-08-02T01:00:00+00:00"
    }
  ],
  "limit": 100, "offset": 0, "next_offset": null,
  "workspace_id": "ws_123", "source": "search_console",
  "property_id": "sc-domain:example.com", "dataset": "query-page-web-v1",
  "start": "2026-08-01", "end": "2026-08-28"
}
```

**Important:** rows are daily facts, not totals. Do not sum non-additive metrics
in the frontend. Distinct users and rates are not additive. Compute a rate from
counts only, for example `clicks / impressions`.

## Rules

A rule is an editable, versioned detector configuration. It turns daily
observations into opportunities.

### List rules

```http
GET /marketing/rules?workspace_id=ws_123
```

Each row:

```jsonc
{
  "rule_id": "5d1f6d7e-...",
  "config": { /* the OpportunityRule object below */ },
  "revision": 3,
  "updated_at": "2026-09-01T10:00:00+00:00",
  "updated_by": "user-id"
}
```

### Rule fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Short label |
| `property_id` | string | required | Property or site identity |
| `dataset` | string | required | Versioned report definition |
| `enabled` | bool | `true` | `false` disables evaluation |
| `min_impressions` | int | `100` | Minimum impressions to include |
| `ctr_threshold` | float | `0.02` | Include only CTR below this fraction |
| `position_min` / `position_max` | float | `1` / `10` | Inclusive average position range |
| `window_days` | int | `28` | Evaluation window length |
| `keywords` | object | `{}` | Include/exclude keyword filter |
| `target_text` | string | `null` | Original natural-language target |
| `schema_version`, `kind` | — | `1`, `search_snippet_review` | Fixed; send as shown |

`keywords` shape:

```jsonc
{
  "mode": "contains",           // "exact" or "contains"
  "include": ["invoice"],       // OR terms; empty = all queries
  "exclude": ["acme"]           // any match wins over include
}
```

- `ctr_threshold` is a fraction. 3% is `0.03`.
- Thresholds are `impressions >= min`, `CTR < threshold`, and inclusive position.
- `mode` applies to both lists. Wildcards and regex have no meaning.

### Preview a natural-language target

Turn a human sentence into a rule draft. This never saves.

```http
POST /marketing/rules/preview?workspace_id=ws_123
Content-Type: application/json

{
  "text": "Find queries containing invoice, excluding Acme, with at least 500 impressions, CTR below 3%, positions 1 to 15, over 28 days.",
  "property_id": "sc-domain:example.com",
  "dataset": "query-page-web-v1"
}
```

Success response:

```jsonc
{
  "status": "ready",
  "rule": { /* complete OpportunityRule */ },
  "summary": "For each query/page segment over 28 days: ...",
  "assumptions": ["100 impressions default", "..."],
  "questions": [],
  "saved": false
}
```

Ambiguous or unsupported target response:

```jsonc
{
  "status": "needs_clarification",
  "rule": null,
  "summary": null,
  "assumptions": [],
  "questions": ["Which branded terms do you mean?"],
  "saved": false
}
```

- Show `summary` and `assumptions` and let the user edit the rule fields.
- The model can make mistakes. The displayed rule is the executable meaning.
- Previews are stateless. Re-submit the full clarified target after a question.
- Unsupported goals return `needs_clarification`. Growth and revenue goals are
  not implemented.

### Save a rule

Use `PUT`. The client supplies the rule UUID.

```http
PUT /marketing/rules/<client-generated-uuid>?workspace_id=ws_123
Content-Type: application/json

{
  "expected_revision": 0,
  "rule": { /* complete OpportunityRule */ }
}
```

- `expected_revision: 0` creates a rule.
- To update, send the `revision` the last GET or PUT returned. It increments
  atomically. A stale revision returns 409; reload before editing again.
- Send the whole rule object. Use the preview's `rule` object to keep
  `target_text`.
- Disable a rule by sending `enabled: false` through the same endpoint.

Response:

```jsonc
{
  "rule_id": "5d1f6d7e-...",
  "config": { /* saved OpportunityRule */ },
  "revision": 1,
  "updated_at": "2026-09-02T09:00:00+00:00",
  "updated_by": "user-id",
  "summary": "For each query/page segment over 28 days: ..."
}
```

### Read one rule

```http
GET /marketing/rules/<rule_id>?workspace_id=ws_123
```

Same shape as the save response. Use it to get the current `revision` before
editing.

## Opportunities

Evaluate one saved rule and get the matching segments.

```http
GET /marketing/opportunities?workspace_id=ws_123&rule_id=5d1f6d7e-...
```

| Parameter | Required | Meaning |
|---|---|---|
| `rule_id` | yes | Saved rule to evaluate |
| `end` | no | Inclusive end date; defaults to yesterday UTC |
| `limit`, `offset` | no | Pagination |

The window uses the rule's `window_days`, ending at `end`. The response:

```jsonc
{
  "data": [
    {
      "kind": "search_snippet_review",
      "dimensions": { "query": "invoice automation", "page": "/pricing" },
      "evidence": {
        "clicks": 12, "impressions": 640,
        "ctr": 0.01875, "average_position": 6.3
      },
      "window": { "start": "2026-08-01", "end": "2026-08-28" },
      "source": "search_console",
      "property_id": "sc-domain:example.com",
      "dataset": "query-page-web-v1",
      "rule": { /* the evaluated rule */ },
      "recommendation": "Review search intent, title and description for this page and query.",
      "confidence": "heuristic; compare against position, brand and device baselines"
    }
  ],
  "total": 14,
  "rule_id": "5d1f6d7e-...",
  "revision": 1,
  "enabled": true,
  "start": "2026-08-01", "end": "2026-08-28",
  "coverage": "unverified",
  "summary": "For each query/page segment over 28 days: ..."
}
```

- Results sort by impressions, highest first.
- `coverage` is always `"unverified"`. The API does not claim fresh or complete
  source data.
- Disabled rules return no opportunities.

## Keyword Planner

Import and filter Google Ads Keyword Planner snapshots. These are market-demand
estimates, not website measurements.

### Import (owner only)

```http
POST /marketing/keyword-planner/reports?workspace_id=ws_123
Content-Type: application/json

{
  "snapshot_id": "cc34f196-9001-4a27-8d5b-83ae9a7aaac1",
  "captured_at": "2026-09-01T12:00:00Z",
  "report_kind": "historical_metrics",
  "context": {
    "customer_id": "1234567890",
    "dataset": "invoice-keyword-research-v1",
    "language": "languageConstants/1000",
    "geo_target_constants": [],
    "network": "GOOGLE_SEARCH",
    "currency_code": "USD"
  },
  "report": {
    "results": [{
      "text": "invoice automation",
      "closeVariants": ["automate invoices"],
      "keywordMetrics": {
        "avgMonthlySearches": "1000",
        "competition": "LOW",
        "competitionIndex": "20",
        "lowTopOfPageBidMicros": "1200000",
        "highTopOfPageBidMicros": "3000000"
      }
    }]
  }
}
```

Response:

```jsonc
{
  "snapshot_id": "cc34f196-9001-4a27-8d5b-83ae9a7aaac1",
  "source": "google_ads_keyword_planner",
  "keyword_count": 1,
  "captured_at": "2026-09-01T12:00:00Z"
}
```

- The client must generate the `snapshot_id` UUID.
- Retry with the same ID and identical content succeeds. Different content under
  the same ID returns 409; use a new ID.
- Reports are bounded to 10,000 keywords and 8 MB.

### List snapshots

```http
GET /marketing/keyword-planner/reports?workspace_id=ws_123&customer_id=1234567890&dataset=invoice-keyword-research-v1
```

`customer_id` and `dataset` are required.

### Read and filter keywords

```http
GET /marketing/keyword-planner/reports/<snapshot_id>?workspace_id=ws_123
  &include=invoice
  &exclude=free
  &match=contains
  &min_monthly_searches=500
  &max_competition_index=60
```

- `include` and `exclude` are repeatable query parameters.
- `match` is `exact` or `contains` (default).
- `min_monthly_searches` and `max_competition_index` are inclusive thresholds.
  A missing estimate does not satisfy a threshold.
- Results sort by average monthly searches, highest first. Unknown volume sorts
  last.

Bid and CPC values are integer micros. Divide by 1,000,000 to display, using
the snapshot's currency. `competition` is paid-ad competition, not organic
ranking difficulty.

## Errors

| Code | Meaning |
|---|---|
| 401 | Missing or invalid credentials |
| 403 | Denied access, or not the workspace owner for a write |
| 404 | Missing workspace or rule |
| 409 | Edit conflict (stale revision, or snapshot ID reuse) |
| 422 | Invalid input, or a too-large evaluation |
| 502 | The model returned an invalid interpretation |
| 503 | Language interpretation unavailable; manual rules still work |

## UI flow

Build this sequence for the rules screen:

1. `GET /marketing/rules` to list saved rules.
2. `POST /marketing/rules/preview` when the user types a target.
3. Show the preview `summary` and `assumptions` with editable rule fields.
4. `PUT /marketing/rules/{uuid}` to save. Use `expected_revision` from the last
   read to avoid 409.
5. `GET /marketing/opportunities?rule_id=...` to show the matching segments.

## Checklist

- [ ] Send the bearer token and `workspace_id` on every user-session request.
- [ ] Show CTR as a percentage by multiplying the fraction by 100.
- [ ] Do not sum distinct users or rates; show them per row.
- [ ] Pass `expected_revision` from the last read to avoid 409 on save.
- [ ] Use the preview's full `rule` object when saving to keep `target_text`.
- [ ] Handle `needs_clarification` by showing the questions and re-submitting.
- [ ] Handle `coverage: "unverified"` by not claiming fresh or complete data.
- [ ] Divide Keyword Planner bids by 1,000,000 and show the snapshot currency.
