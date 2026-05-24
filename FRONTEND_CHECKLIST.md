# Frontend → Backend Integration Checklist

## What the Backend Now Has ✅

| Component | Status | Notes |
|-----------|--------|-------|
| `POST /sources/gdrive/connect` | ✅ Ready | Validates tokens, counts files, syncs to snapshot pipeline |
| `GET /sources/drive/connections` | ✅ Ready | Lists user's connected Google accounts |
| `POST /sources/gdrive/disconnect` | ✅ Ready | Removes a Google account connection |
| `drive_connections` table | ✅ Created | Stores OAuth tokens with RLS |
| Token → `consolidation_workspaces` sync | ✅ Built-in | `/gdrive/connect` copies tokens for snapshot |
| Google Drive file counting | ✅ Implemented | Calls Drive API to verify + count |
| User authentication | ✅ Integrated | Uses JWT from frontend for authorization |

## What the Frontend Needs to Do

### 1. Call `/sources/gdrive/connect` After OAuth

In your callback route after exchanging the auth code:

```typescript
// app/api/auth/google-drive/callback/route.ts

// After exchanging code for tokens:
const tokens = await exchangeGoogleCode(code);  // access_token, refresh_token, expiry

// Save to Supabase (frontend does this)
await supabase.from('drive_connections').insert({
  user_id: user.id,
  google_account_email: tokens.email,
  access_token: tokens.access_token,
  refresh_token: tokens.refresh_token,
  token_expiry: tokens.expiry_date,
});

// Notify worker to validate and sync
const response = await fetch('http://your-worker-url/sources/gdrive/connect', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${session.accessToken}`,
    'Content-Type': 'application/x-www-form-urlencoded',
  },
  body: new URLSearchParams({
    workspace_id: workspaceId,
    google_account_email: tokens.email,
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token || '',
    token_expiry: tokens.expiry_date || '',
  }).toString(),
});

const result = await response.json();
// result = { status: "connected", doc_count: 247 }

// Update UI with doc_count, then redirect
redirect('/dashboard?drive=connected&docs=' + result.doc_count);
```

### 2. Update Your SourcesModal

When user clicks "Connect Drive", you now get back:
- `status`: "connected"
- `doc_count`: number of Google Drive files found
- `google_account_email`: which account connected

Display this info in the modal.

### 3. Update Your LeftRail

Fetch connections to show:
- Email address
- File count (from `doc_count`)
- Last synced timestamp
- Disconnect button

```typescript
const { data: connections } = await fetch(
  '/sources/drive/connections',
  { headers: { 'Authorization': `Bearer ${token}` } }
).then(r => r.json());

// connections[0] = {
//   id, google_account_email, doc_count, last_synced_at, created_at
// }
```

### 4. Implement Disconnect

```typescript
await fetch('/sources/gdrive/disconnect', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}` },
  body: new URLSearchParams({
    google_account_email: 'user@gmail.com'
  }).toString(),
});
// returns { status: "disconnected" }
```

## Testing Sequence

### Local Testing (Frontend Dev)

1. **Start worker**:
   ```bash
   cd ~/poysis-ai-worker
   uvicorn main:app --reload --port 8000
   ```

2. **Test OAuth flow in browser**:
   - Navigate to `http://localhost:3000/dashboard`
   - Click "Connect Google Drive"
   - Complete OAuth approval
   - Should redirect to `/dashboard?drive=connected&docs=XXX`

3. **Check dashboard shows connection**:
   - LeftRail should show "Google Drive connected (XXX docs)"
   - SourcesModal should show email + doc count

4. **Verify database sync**:
   ```sql
   -- In Supabase SQL Editor
   SELECT * FROM drive_connections 
   WHERE google_account_email = 'your-test@gmail.com';
   
   SELECT google_access_token, google_refresh_token 
   FROM consolidation_workspaces 
   WHERE workspace_id = 'test123';
   ```
   Should match (tokens synced).

5. **Test snapshot pipeline**:
   ```bash
   curl -X POST http://localhost:8000/consolidation/snapshot \
     -H "Authorization: Bearer test-token" \
     -H "Content-Type: application/json" \
     -d '{
       "workspace_id": "test123",
       "sources": ["google_drive"],
       "doc_limit": 50,
       "time_window_days": 30
     }'
   ```
   Should start fetching files from the connected Drive.

### Production Testing (After Deploy)

1. Ensure worker URL points to prod (not localhost)
2. Ensure Google OAuth client has prod callback URI registered
3. Run through OAuth flow again
4. Verify connection shows in dashboard

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Callback returns 401 | Is Authorization header being sent? User JWT valid? |
| `/sources/gdrive/connect` returns 500 | Check worker logs. Is drive_connections table created? |
| Doc count shows 0 | Try re-running OAuth with a fresh token. Check Drive permissions. |
| Tokens not syncing to consolidation_workspaces | Check worker logs for sync errors. Verify both tables exist. |

## API Worker URL

Frontend needs to know where the worker is:
- **Dev**: `http://localhost:8000`
- **Staging**: `https://poysis-worker-staging.railway.app`
- **Prod**: `https://poysis-worker.railway.app`

Add to `.env.local`:
```
NEXT_PUBLIC_WORKER_URL=http://localhost:8000
```

Then use in callback:
```typescript
const workerUrl = process.env.NEXT_PUBLIC_WORKER_URL || 'http://localhost:8000';
const response = await fetch(`${workerUrl}/sources/gdrive/connect`, { ... });
```

---

**Questions?** Check [GOOGLE_DRIVE_INTEGRATION.md](./GOOGLE_DRIVE_INTEGRATION.md) for full architecture details.
