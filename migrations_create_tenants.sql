-- Tenant (organization) layer, added above workspaces.
--
-- A tenant is a reseller/org that owns many workspaces. Users (Cognito `sub`)
-- belong to tenants via tenant_members. Self-serve: a sign-up provisions a
-- personal tenant automatically (see TenantStore.ensure_personal_tenant).
--
-- Idempotent: every statement is safe to re-run. The workspaces.tenant_id
-- column is nullable on purpose — the worker provisions tenants lazily, and
-- nothing breaks while a workspace still has NULL tenant_id (the tenant
-- boundary check in security.py is skipped when tenant_id is absent).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS tenants (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug       TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL DEFAULT 'My Organization',
    branding   JSONB NOT NULL DEFAULT '{}',
    plan       TEXT NOT NULL DEFAULT 'personal',
    status     TEXT NOT NULL DEFAULT 'active',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_members (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member'
               CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS tenant_members_user_idx ON tenant_members (user_id);

ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS workspaces_tenant_idx ON workspaces (tenant_id);
