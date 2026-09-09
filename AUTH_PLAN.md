# Authentication Plan

## 1. What is wrong today

**The identity is not verified.** [security.py:11](app/api/security.py#L11) is the whole check:

```python
async def get_user_id(x_user_id: Optional[str] = Header(None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, ...)
    return x_user_id
```

It reads the `X-User-ID` header and returns it. Any caller can send any user ID.

`verify_workspace_access` then checks the `workspace_members` table correctly. But it checks membership for an identity that the caller invented. A person who knows a `user_id` and a `workspace_id` reads that whole workspace.

**The MCP endpoint has no check at all.** A `tools/list` call to `/mcp/{workspace_id}` succeeds with no header. The workspace ID in the URL is the only secret. That value:

- travels in a URL, so it appears in ALB access logs and in browser history
- travels over HTTP today, so it is cleartext on the network
- cannot be revoked or rotated without breaking the connector

**The transport is HTTP.** The ALB has no TLS. Every header and every URL is readable on the path.

These three faults combine. The header is guessable, the MCP URL is loggable, and neither is encrypted.

## 2. Constraints that shape the design

Read these before you choose a scheme. Each one has already caused a bug.

- **The browser `EventSource` API cannot send a custom header.** [CLIENT_INTEGRATION_GUIDE.md:257](CLIENT_INTEGRATION_GUIDE.md#L257) records this. The consolidation progress stream returns 401 today for that reason. Any header-only scheme repeats this bug.
- **Claude.ai controls the MCP client.** You cannot add a custom header to it. The MCP endpoint must authenticate through the URL, through a standard `Authorization` header, or through OAuth.
- **The frontend is a separate Next.js application.** A change to the token format needs a matching release there.
- **`user_id` is a Supabase Auth subject today.** The database moved to RDS, but the identity provider did not move with it.

## 3. The options

### Option A — Verify the Supabase JWT (recommended first step)

The frontend already holds a Supabase session. Send the access token as `Authorization: Bearer <jwt>`. The worker verifies the signature against the Supabase JWKS, and reads `sub` as the user ID.

- **Work:** small. One dependency, one function, one cache for the JWKS.
- **Frontend change:** send the token it already has.
- **User migration:** none. The subject is the same value that `X-User-ID` carries now.
- **Cost:** $0.
- **Weakness:** it keeps a Supabase dependency that you plan to remove.

### Option B — Move to Amazon Cognito

Cognito becomes the identity provider. The worker verifies Cognito JWTs. This fits the AWS migration and gives the WAFR review a real answer on the Security pillar.

- **Work:** large. A user pool, a hosted login page, a frontend rewrite of every auth call, and a migration of existing users.
- **Cost:** free below 10,000 monthly active users.
- **Weakness:** it is a project, not a patch. It blocks nothing else, so it does not need to happen first.

### Option C — The worker issues its own tokens

The worker signs its own JWTs and holds its own user table.

- **Weakness:** you then own password reset, email verification, and social login. Do not choose this.

**Recommendation: do Option A now, and treat Option B as the destination.** Option A closes the hole this week and does not block Option B later, because both verify a JWT and both read `sub`. The verification function changes; nothing else does.

## 4. The phased plan

### Phase 1 — Add TLS first

Every scheme below sends a token. A token over HTTP is not safer than the header you have now. Finish the ACM certificate and the HTTPS listener before you start Phase 2.

### Phase 2 — Verify the token, accept both schemes

1. Add a `verify_token` function beside `get_user_id`. It reads `Authorization: Bearer <jwt>`, verifies the signature against the Supabase JWKS, and returns `sub`.
2. Cache the JWKS in memory. Refresh it when a key ID is absent.
3. Change `get_user_id` to try the bearer token first, and to fall back to the `X-User-ID` header.
4. Log every request that uses the fallback, with the route name.

The fallback keeps the current frontend working. The log tells you when it is safe to remove.

### Phase 3 — Move the clients

1. Update the Next.js frontend to send the token on every call.
2. Replace the `EventSource` call with a fetch-based SSE client, so the stream can carry the header. The client guide already asks for this change.
3. Watch the fallback log until it is empty.

### Phase 4 — Remove the fallback

Delete the `X-User-ID` branch. Return 401 when the bearer token is absent or invalid.

**Warning: this step breaks any client you did not move in Phase 3.** Do not start it while the fallback log still shows traffic.

### Phase 5 — Close the MCP endpoint

The MCP endpoint needs its own answer, because Claude.ai cannot send your header. Choose one:

- **A per-workspace token in the path.** Issue a random secret, store its hash, and put it in the connector URL. The user can revoke and reissue it. This is small work and it fits the current design.
- **OAuth on the MCP server.** The MCP specification supports it, and Claude.ai supports it. This is the correct long-term answer, and it is more work.

Start with the token. Add a `mcp_tokens` table, an endpoint that creates and revokes a token, and a check in `mcp_http.py`. Change `/consolidation/mcp_url/{workspace_id}` to return the URL that carries the token.

### Phase 6 — Add the edge controls

1. Put AWS WAF in front of the ALB. Use the managed common rule set and a rate rule.
2. Move the in-memory rate limit to the edge, or keep both. The in-memory limit resets on every deployment and does not work across tasks.

## 5. What to test

Write these as integration tests in `tests/`, beside `test_auth_isolation.py`.

1. A request with no credential returns 401.
2. A request with a token signed by the wrong key returns 401.
3. A request with an expired token returns 401.
4. A token for user A cannot read a workspace that belongs to user B. This is the test that matters most.
5. An MCP call with no token returns 401.
6. An MCP call with a revoked token returns 401.
7. The SSE stream authenticates the same way as the other routes.

Test 4 is the one that fails today.

## 6. Effort

| Phase | Work | Blocks |
|---|---|---|
| 1. TLS | Half a day | Everything |
| 2. Verify the token | One day | Phase 3 |
| 3. Move the clients | One day, in the frontend repo | Phase 4 |
| 4. Remove the fallback | One hour | — |
| 5. MCP token | One day | — |
| 6. WAF and rate limit | Half a day | — |

Phases 1, 2, and 5 close the three holes in section 1. Phases 3 and 4 remove the old path. Phase 6 adds depth.
