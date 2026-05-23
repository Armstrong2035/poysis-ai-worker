# Waitlist endpoint — frontend integration

## Endpoint

```
POST https://poysis-ai-worker-production.up.railway.app/waitlist
Content-Type: application/json
```

(Use `http://localhost:8000/waitlist` against a local worker.)

## Request body

```json
{
  "email": "user@example.com",
  "source": "landing"
}
```

- `email` (required) — validated server-side; invalid emails get `422`
- `source` (optional) — free-form string. Use it to tag where the signup came from (`"landing"`, `"hero-cta"`, `"footer"`, a referrer, etc.)

## Responses

| Status | Body | Meaning |
|---|---|---|
| `200` | `{ "status": "ok" }` | Saved (or already on the list — idempotent on email) |
| `422` | `{ "detail": [...] }` | Email failed validation or body malformed |
| `500` | `{ "detail": "Failed to save waitlist signup" }` | DB write failed |

Duplicate submissions return `200` — show the same success state, don't error.

## Minimal fetch call

```ts
async function joinWaitlist(email: string, source = "landing") {
  const res = await fetch(`${process.env.NEXT_PUBLIC_WORKER_URL}/waitlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source }),
  });
  if (!res.ok) throw new Error(res.status === 422 ? "Invalid email" : "Signup failed");
}
```

## Notes

- CORS is wide open on the worker (`allow_origins=["*"]`), so calling directly from the browser is fine.
- No auth required.
- Lowercase + trim happens server-side; send the email as-typed.
