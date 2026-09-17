# Studio integration

Installed in the sibling `poysis-client/apps/studio` application:

- `app/(workspace)/workspace/interpretations/page.tsx`
- `app/(workspace)/workspace/interpretations/InterpretationPanel.tsx`
- `app/api/interpretations/route.ts`
- Explanations link in `WorkspaceSidebar.tsx`
- `scripts/test-interpretations-proxy.cjs`

These files here are the integration source copies. The installed client copies
have been checked with TypeScript, ESLint, and the proxy test. The authenticated
page uses the current user's workspace. Client session authorization is forwarded
to the backend, which rechecks workspace membership for every operation. No model
key is sent to the client.

The proxy uses the existing `WORKER_URL` setting before `LOCAL_WORKER_URL`.
The inspected local Studio configuration currently names the Railway deployment,
not an AWS endpoint. Select the intended backend URL before deployment. Model
provider URLs and the Poysis worker URL are separate: the server calls DeepSeek;
the client calls the Poysis worker through its own proxy.

Backend rollout prerequisites:

1. Deploy the updated backend including `hermeneutics` and the new router.
2. Apply `migrations_create_interpretations.sql` and the marketing observations
   migration where needed, using the backend database role.
3. Ensure the runtime receives its existing `DEEPSEEK_API_KEY`. Enable
   `HERMENEUTICS_ENABLED=true` or run the separate leased worker.
4. Preserve OpenAI embedding configuration used for the existing document vectors.
5. Set the Studio server-side `WORKER_URL` to that backend and deploy the client.

Validation completed locally: 22 backend hermeneutics tests, client TypeScript,
lint of the three new client files, proxy checks, and a live DeepSeek two-pass
test using synthetic evidence. The configured database could not be reached, so
Postgres migrations, real workspace retrieval, and authenticated browser-to-cloud
testing have not been verified. No production deployment was performed.
