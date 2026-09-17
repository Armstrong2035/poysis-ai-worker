# Google Drive OAuth Integration — Backend Setup

This document explains the backend flow for Google Drive OAuth and how the frontend callback integrates with the worker.

## Architecture Overview

```
Frontend                                Backend
═══════════════════════════════════════════════════════════════════

User clicks "Connect Drive"
    ↓
Generate OAuth URL (Google)
    ↓
User approves access
    ↓
Google redirects to /callback with auth code
    ↓
POST /api/auth/google-drive/callback
    │
    ├─ Exchange code → access_token + refresh_token
    │
    ├─ POST /sources/gdrive/connect (to WORKER)
    │    ├─ Save tokens → drive_connections table
    │    ├─ Verify token works (call Google Drive API)
    │    ├─ Count files
    │    └─ Sync tokens → consolidation_workspaces (for snapshot pipeline)
    │
    └─ Redirect to /dashboard?drive=connected
```

## Backend Endpoints

### POST `/sources/gdrive/connect`

Called by frontend callback after OAuth exchange. Handles all token management and workspace linkage.

**Parameters (Query):**
- `workspace_id` (required): Which workspace this connection is for
  - User can connect different Drive accounts to different workspaces
  - Must be a workspace the user owns (ownership verified server-side)
- `google_account_email` (required): Email of the Google account
- `access_token` (required): OAuth access token from Google
- `refresh_token` (optional): OAuth refresh token
- `token_expiry` (optional): Token expiration timestamp (ISO 8601)

**Response:**
```json
{
  "status": "connected",
  "workspace_id": "ws_abc123",
  "google_account_email": "user@gmail.com",
  "doc_count": 247
}
```

**What it does:**
1. ✅ **Security:** Verifies user owns the workspace (403 if not)
2. ✅ **Saves tokens** to `drive_connections` table (user + workspace specific, encrypted)
3. ✅ **Validates token** by calling Google Drive API
4. ✅ **Counts documents** in Drive
5. ✅ **Updates doc_count** in `drive_connections`
6. ✅ **Syncs tokens** to `consolidation_workspaces` so snapshot pipeline can use them
7. ✅ **Sets up sync callback** so worker updates `last_synced_at` after snapshot completes

**Error responses:**
- `403 Forbidden`: User doesn't own this workspace
- `404 Not Found`: Workspace doesn't exist
- `500 Internal Server Error`: Token validation failed or DB error

### GET `/sources/drive/connections`

List all Google Drive connections for the authenticated user.

**Response:**
```json
{
  "connections": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "google_account_email": "user@gmail.com",
      "doc_count": 247,
      "last_synced_at": "2026-05-24T10:30:00Z",
      "created_at": "2026-05-24T09:15:00Z"
    }
  ]
}
```

### POST `/sources/gdrive/disconnect`

Remove a Google Drive connection.

**Parameters (Query):**
- `google_account_email` (required): Which account to disconnect

**Response:**
```json
{
  "status": "disconnected",
  "google_account_email": "user@gmail.com"
}
```

## Database Tables

### `drive_connections` (User OAuth Storage)

```sql
CREATE TABLE public.drive_connections (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL,  -- auth.users.id
  google_account_email TEXT NOT NULL,
  access_token TEXT NOT NULL,  -- Encrypted in production
  refresh_token TEXT,
  token_expiry TIMESTAMPTZ,
  doc_count INTEGER,
  last_synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  UNIQUE(user_id, google_account_email)
);
```

**Purpose:** Stores OAuth tokens from frontend OAuth flow. One record per Google account the user connects.

### `consolidation_workspaces` (Snapshot Pipeline Configuration)

```sql
CREATE TABLE public.consolidation_workspaces (
  id UUID PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  user_id UUID,
  google_access_token TEXT,  -- Copied from drive_connections by /gdrive/connect
  google_refresh_token TEXT,
  google_token_expiry TIMESTAMPTZ,
  ...
);
```

**Purpose:** Configuration for the consolidation/snapshot pipeline. When `/sources/gdrive/connect` is called, it copies the tokens here so `POST /consolidation/snapshot` can use them.

## Flow Example

1. **Frontend OAuth Callback** receives:
   ```
   GET /api/auth/google-drive/callback
     ?code=4/0AE...
     &state=workspace:ws_123
   ```

2. **Frontend Callback Route**:
   ```typescript
   // Exchange code for tokens
   const { access_token, refresh_token, expiry } = await exchangeCodeForTokens(code);
   
   // Save to Supabase drive_connections
   await db.from('drive_connections').insert({
     user_id: user.id,
     google_account_email: userEmail,
     access_token,
     refresh_token,
     token_expiry: expiry,
   });
   
   // Notify worker
   await fetch('http://localhost:8000/sources/gdrive/connect', {
     method: 'POST',
     headers: { 'Authorization': `Bearer ${token}` },
     searchParams: {
       workspace_id: workspaceId,
       google_account_email: userEmail,
       access_token,
       refresh_token,
       token_expiry: expiry
     }
   });
   
   // Redirect
   window.location = '/dashboard?drive=connected';
   ```

3. **Worker `/sources/gdrive/connect`**:
   - Receives tokens + email
   - Saves to `drive_connections` (idempotent upsert)
   - Verifies token by calling Google Drive API
   - Counts files
   - Copies tokens to `consolidation_workspaces`
   - Returns `{ status: "connected", doc_count: 247 }`

4. **Frontend Dashboard**:
   - Shows "Google Drive connected (247 docs)"
   - User can now run `POST /consolidation/snapshot` to start consolidation

## Token Refresh Flow

When the snapshot pipeline runs and the token is expired:

1. `consolidation/snapshot` calls `get_valid_token(workspace_id)`
2. `get_valid_token()` checks if token is expired
3. If expired, it calls Google's refresh endpoint with the refresh_token
4. Updates `consolidation_workspaces` with new access_token
5. Continues with snapshot

This is handled transparently by the existing `google_auth.py` module.

## Security Notes

- `access_token` and `refresh_token` are stored in Supabase with RLS policies
  - Only the user who saved them can read/update
  - Should be encrypted at rest (Supabase Vault in production)
- Tokens never appear in logs or frontend code
- Frontend only handles OAuth URLs and redirects, not tokens directly

## Multi-User Workspace Architecture

The system is designed to support collaborative workspaces where multiple users can access the same knowledge base:

### Workspace Members Table

```sql
CREATE TABLE public.workspace_members (
  id UUID PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES auth.users(id),
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member', 'viewer')),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(workspace_id, user_id)
);
```

**Purpose:** Track which users have access to which workspaces. Future releases will allow workspace owners to invite collaborators.

### Access Control

- **Phase 1 (Current):** Each workspace is owned by one user. The `drive_connections` table stores workspace_id to support multiple Drive accounts per workspace.
- **Phase 2 (Upcoming):** Multiple users can be members of the same workspace. All members share access to the consolidated knowledge and Drive tokens.

### Verification Logic

When accessing endpoints like `/consolidation/snapshot`, the system:
1. Checks `workspace_members` table for user access (owner, member, or viewer role)
2. Falls back to checking if user owns the workspace (backward compatibility)
3. Auto-adds workspace owners to `workspace_members` for future consistency

This ensures the system works seamlessly before and after multi-user features are enabled.

## Environment Setup (Frontend Dev)

Frontend `.env.local` needs:
```
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret-key
```

And OAuth redirect URI registered in Google Cloud Console:
- Dev: `http://localhost:3000/api/auth/google-drive/callback`
- Prod: `https://poysis.app/api/auth/google-drive/callback`

## Environment Setup (Backend)

Backend already has:
- `GEMINI_API_KEY` (for AI analysis)
- `SUPABASE_PRODUCT_URL` + `SUPABASE_SERVICE_ROLE_KEY` (database)
- Google Drive API enabled (via google-auth-oauthlib)

No additional env vars needed for `/sources/gdrive/connect`.

## Testing the Flow

### 1. Create a test drive_connections record
```bash
curl -X POST http://localhost:8000/sources/gdrive/connect \
  -H "Authorization: Bearer test-token" \
  -d "workspace_id=test123" \
  -d "google_account_email=test@gmail.com" \
  -d "access_token=ya29.abc..." \
  -d "refresh_token=1//xyz..." \
  -d "token_expiry=2026-06-24T10:00:00Z"
```

### 2. Verify connection was saved
```bash
curl http://localhost:8000/sources/drive/connections \
  -H "Authorization: Bearer test-token"
```

### 3. List connections from Supabase
```sql
SELECT * FROM drive_connections WHERE google_account_email = 'test@gmail.com';
SELECT * FROM consolidation_workspaces WHERE workspace_id = 'test123';
```

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` on /sources/gdrive/connect | Missing/invalid JWT | Add Authorization header with user token |
| `400 Invalid token` | Token is expired or wrong | Re-run OAuth flow to get fresh token |
| `500 Failed to save connection` | Database error or RLS policy | Check Supabase logs; ensure user_id matches auth.uid() |
| `404 No Drive files found` | Token valid but no docs | Might be permission issue with Google account |

## Next Steps

- [ ] Frontend callback calls `/sources/gdrive/connect` with auth code exchanged for tokens
- [ ] Verify `GET /sources/drive/connections` returns connected accounts
- [ ] Run `POST /consolidation/snapshot` to start consolidation pipeline
- [ ] Check `consolidation_topics` to see extracted knowledge
