# Test Suite

Test scripts for Poysis backend. Each test file focuses on a specific subsystem.

## Setup

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Ensure backend is running
python main.py
```

## Test Files

### 1. test_auth_isolation.py
**Tests user isolation and authorization boundaries.**

```bash
pytest tests/test_auth_isolation.py -v
```

Verifies:
- Missing X-User-ID header returns 401
- User cannot access other user's workspaces (403)
- User can access own workspace
- Query/list endpoints require authentication
- Consolidation endpoints verify ownership

### 2. test_job_persistence.py
**Tests job state persistence across server restarts.**

```bash
pytest tests/test_job_persistence.py -v
```

Verifies:
- Job status survives server restart (stored in DB)
- Job metadata includes job_id, status, timestamps
- Clustering jobs persist
- Multiple jobs in same workspace tracked independently

### 3. test_rate_limiting.py
**Tests rate limiting middleware (100 req/min per user).**

```bash
pytest tests/test_rate_limiting.py -v
```

Verifies:
- First 100 requests/minute succeed
- Request 101+ returns 429
- Rate limit is per-user (User 2 not affected by User 1's limit)

### 4. test_error_handling.py
**Tests error handling middleware and exception responses.**

```bash
pytest tests/test_error_handling.py -v
```

Verifies:
- Invalid workspace_id returns 400
- Missing required fields return 422
- Malformed JSON returns 400
- Unhandled exceptions return 500
- Missing Google token returns 401

### 5. test_input_validation.py
**Tests input validation middleware.**

```bash
pytest tests/test_input_validation.py -v
```

Verifies:
- workspace_id validation (format, length)
- Payload size limit (10MB max) returns 413
- XSS attempts in workspace_id rejected
- Negative doc_limit/time_window rejected
- Invalid source types handled
- drive_folder_ids must be a list

### 6. test_end_to_end.py
**Tests complete flow: consolidation → query → results.**

```bash
pytest tests/test_end_to_end.py -v
```

Verifies:
- Consolidation snapshot can be started
- Job status is queryable
- Clustering can be run
- Query returns results with source metadata
- List documents supports search/filter

### 7. test_mcp_tools.py
**Tests MCP tool endpoints and decision logic.**

```bash
pytest tests/test_mcp_tools.py -v
```

Verifies:
- query_knowledge_base performs semantic search
- list_documents filters by title/metadata
- Both tools are callable on borderline queries
- Tool endpoints exist and don't crash

### 8. test_google_oauth.py
**Tests Google OAuth flow and token management.**

```bash
pytest tests/test_google_oauth.py -v
```

Verifies:
- OAuth endpoint exists
- workspace_id required
- Token persists after OAuth
- Token refresh on expiry
- Multiple workspaces can have different tokens
- User denial handled gracefully
- CSRF protection (state validation)

## Run All Tests

```bash
# Run all tests
pytest tests/ -v

# Run with output
pytest tests/ -v -s

# Run specific test file
pytest tests/test_auth_isolation.py -v

# Run specific test
pytest tests/test_auth_isolation.py::test_missing_user_id_header -v
```

## Environment Setup

Tests require:
- **Backend running** on http://localhost:8000 (or set `WORKER_URL` env var)
- **X-User-ID header** on authenticated endpoints (injected by `make_request()` helper)
- **Test database** with tables for jobs, workspaces, documents

Set `WORKER_URL` to override default:

```bash
export WORKER_URL=http://localhost:8000
pytest tests/
```

## Test Fixtures (conftest.py)

Shared pytest fixtures:

```python
client          # httpx.Client with base_url
async_client    # httpx.AsyncClient
test_user_1     # TEST_USER_1_ID
test_user_2     # TEST_USER_2_ID
workspace_id    # TEST_WORKSPACE_ID
health_check    # Verifies backend is running (skips if not)
```

Helper functions:

```python
make_request(method, path, user_id, **kwargs)
make_async_request(method, path, user_id, **kwargs)
```

Both automatically inject `X-User-ID: {user_id}` header.

## Manual Tests

For endpoints requiring actual Google tokens or interactive flows:

### OAuth Flow

```bash
# Start local backend
python main.py

# Open browser to initiate OAuth
# Callback will be /auth/google/callback?code=...&state=...
```

### Consolidation with Real Data

```bash
# In Python shell
from app.primitives.consolidation.scope import ScopeConfig
from app.primitives.consolidation.snapshot import SnapshotRunner

scope = ScopeConfig(
    workspace_id="my-workspace",
    google_access_token="<real_token>",
    # ... other params
)

runner = SnapshotRunner(scope=scope)
result = await runner.discover()
```

## Troubleshooting

**"Backend not running"**
```bash
python main.py
```

**"X-User-ID header missing"**
All authenticated endpoints require the header. `make_request()` injects it automatically.

**"Workspace not found"**
Tests use TEST_WORKSPACE_ID. Create it first or adjust workspace_id in tests.

**"Google token not found"**
Tests that require Google token will skip if not present. Run `test_google_oauth.py` to test OAuth flow.
