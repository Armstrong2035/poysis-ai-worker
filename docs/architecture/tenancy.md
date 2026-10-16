# Tenancy

A tenant is an organization that owns many workspaces. The platform sells the
architecture to tenants. Each tenant builds its own surfaces for different use
cases.

## Model

```
tenant (organization)
  └─ tenant_members (user_id, role: owner | admin | member)
  └─ workspaces (tenant_id)
       └─ notebooks, topics, ceilings, vectors, api_keys, mcp_tokens
```

- **tenant** — the platform customer. A reseller or organization. It owns many
  workspaces. It has a slug, a name, a branding JSON, and a plan.
- **workspace** — one project or use case inside a tenant. The existing
  `workspace_id` stays the isolation unit for all resources. Nothing below the
  workspace changes.
- **member** — a Cognito user. A user belongs to a tenant through
  `tenant_members`. One user can belong to many tenants.

## Decisions

- **One shared Cognito user pool.** Tenant identity and membership live in
  Postgres. The pool is not split per tenant.
- **Self-serve.** A sign-up provisions a personal tenant and one workspace in
  it. The account owner can add members and workspaces later.
- **Branding (v1).** A tenant stores `branding` JSON (name, logo, colors). The
  frontend resolves the tenant by subdomain (`acme.poysis.com`). Custom domains
  come later.

## Tables

| Table | Purpose |
|---|---|
| `tenants` | id, slug, name, branding (jsonb), plan, status, created_by |
| `tenant_members` | tenant_id, user_id, role (owner/admin/member), unique (tenant_id, user_id) |
| `workspaces.tenant_id` | nullable FK to tenants |

`workspaces.tenant_id` is nullable on purpose. Tenants are provisioned lazily.
A workspace with no tenant keeps working. The tenant check is skipped while the
value is null.

## Authorization

`app/api/security.py`:

- `verify_tenant_access(tenant_id, user_id)` — 403 unless the user is a member
  of the tenant.
- `verify_workspace_access` also enforces the tenant boundary. It runs the
  existing workspace check, then checks tenant membership when the workspace has
  a tenant.

## API

`app/api/tenant.py`, mounted at `/client/tenant`:

| Route | Purpose |
|---|---|
| `GET /client/tenant` | the caller's tenant and role (provisions it if missing) |
| `PUT /client/tenant/branding` | update the branding JSON (owner/admin) |
| `GET /client/tenant/workspaces` | list the tenant's workspaces |
| `GET /client/tenant/members` | list the tenant's members |

## Provisioning path

The frontend already calls `POST /client/workspace` (ensure workspace) on login
and sign-up. `ClientDataStore.ensure_workspace` now also provisions the personal
tenant and attaches the workspace. No frontend change is required for
self-serve.

## Backfill

`scripts/backfill_tenants.py` provisions a tenant for every existing user and
attaches their workspaces. Idempotent; run it after the migration.

## Not done yet (later phases)

- Subdomain → tenant resolution in the frontend middleware.
- Custom domains per tenant.
- Tenant plan and usage-based billing.

## Client apps (whitelabel)

Each client gets its own Next.js app (a clone of Studio), deployed separately and
bound to one tenant. The worker stays shared; tenant isolation is enforced by
the bearer token plus the tenant boundary check.

A client app sets two server-side env vars:

- `POYSIS_TENANT_ID` — the tenant the app serves.
- `POYSIS_TENANT_KEY` — a `poysis_live_` key scoped to that tenant with
  `tenant:member:write`.

On sign-up and sign-in the app auto-joins the user to the tenant via
`POST /client/tenant/join` (authenticated by the key). Without these vars the
app falls back to the personal-tenant flow, so the same Studio code runs both as
the main Poysis app and as a client app.

Spinning up a new client app is: clone Studio, set the two vars above plus the
Cognito/worker env, deploy, and skin it. The tenant's branding (name, logo,
colors) comes from `GET /client/tenant`.
