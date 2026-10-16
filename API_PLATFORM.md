# Poysis API platform

Poysis supports server-to-server API keys bound to one workspace. Raw keys are
returned once, stored only as SHA-256 hashes, and can be scoped, expired, and
revoked. Keep keys in server-only configuration; never expose them to browsers.

## Dashboard API

These routes use the normal Poysis user bearer token and require the workspace
owner. Every request includes `workspace_id`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/platform/client` | Read the API client registered to the workspace |
| `PUT` | `/platform/client` | Create or rename the client |
| `GET` | `/platform/api-keys` | List key metadata; raw keys are never returned |
| `POST` | `/platform/api-keys` | Issue a key and return its raw value once |
| `DELETE` | `/platform/api-keys/{key_id}` | Revoke a key immediately |
| `GET` | `/platform/usage?days=30` | Daily request counts grouped by scope |

Create a key:

```http
POST /platform/api-keys?workspace_id=<workspace>
Authorization: Bearer <user-jwt>
Content-Type: application/json

{
  "name": "Bezalel production",
  "scopes": ["marketing:read"],
  "expires_at": null
}
```

The response contains `api_key` once. Copy it directly into the consuming
service's secret configuration.

## External API

```http
GET /v1/marketing/connection
Authorization: Bearer poysis_live_<prefix>.<secret>
```

The connection response identifies the client, derived workspace, and scopes.
The initial external scope is `marketing:read`. It supports the existing read
routes without a `workspace_id` query parameter:

- `GET /marketing/rules`
- `GET /marketing/rules/{rule_id}`
- `GET /marketing/opportunities?rule_id=<uuid>`
- `GET /marketing/data?...`
- `GET /marketing/keyword-planner/reports?...`
- `GET /marketing/keyword-planner/reports/{snapshot_id}?...`

Write operations remain user-session and owner authenticated. An API key cannot
select or override a workspace. Supplying a different `workspace_id` returns 403.

For the Bezalel server proxy:

```ini
POYSIS_API_URL=https://api.poysis.com
POYSIS_API_TOKEN=poysis_live_...
```

Keys are metered on successful authentication. Invalid, expired, revoked, or
insufficiently scoped keys return 401. Rate limiting hashes the authorization
header in memory so separate keys receive separate limits without logging or
retaining raw credentials.
