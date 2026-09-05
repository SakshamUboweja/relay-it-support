# Railway deployment

Status: Railway provisioning is underway on the existing Hobby workspace. The owner authorized necessary spending on the cheapest plan; no plan upgrade is required.

## Architecture

Relay is a single-model workflow application, not a multi-agent system. A Responses API extraction call supplies validated facts; application code retrieves scoped sources, applies routing/security policy, persists the report and queues idempotent Jira operations. The separate worker is a background process, not a second AI agent. Deploying these processes does not change that distinction.

Use one Railway project with three services in the same region:

| Service | Source | Configuration | Public exposure |
| --- | --- | --- | --- |
| relay-web | GitHub `SakshamUboweja/relay-it-support`, main | `.railway/railway.ts` | Railway HTTPS domain |
| relay-worker | Same repository and commit | `.railway/railway.ts` | None |
| Postgres | `pgvector/pgvector:pg16` | Persistent volume `/var/lib/postgresql/data`; database `relay` | Private networking; temporary proxy only for import if needed |

The shared Docker image runs Node 22 as the unprivileged `node` user. Local credentials, mapping files and build output are excluded from the build context. The web service listens on `0.0.0.0` and Railway's `PORT`. `/api/health` checks database/schema readiness without revealing configuration. Web and worker validate live configuration before starting.

Railway no longer allows new services to opt into `railway.json` / `railway.toml`. The infrastructure definition uses its current TypeScript SDK. Run `railway config plan` to review drift and `railway config apply` to apply intentional infrastructure changes. Existing secrets use `preserve()` and stay in Railway. See [Railway infrastructure configuration](https://docs.railway.com/infrastructure-as-code).

## Runtime variables

Set these on both application services through Railway variables, never in Git or Docker build arguments:

| Variable | Value |
| --- | --- |
| APP_MODE | `live` |
| DATABASE_URL | Private reference to the new Postgres service |
| APP_ORIGIN | Exact generated HTTPS origin, without trailing slash |
| SESSION_SECRET | Newly generated random secret, at least 32 characters |
| OPENAI_API_KEY | Existing authorized API key |
| OPENAI_MODEL | `gpt-5.6-terra` |
| OPENAI_REASONING_EFFORT | `high` |
| OPENAI_MAX_OUTPUT_TOKENS | `8192` |
| OPENAI_EMBEDDING_MODEL | `text-embedding-3-small` |
| JIRA_EMAIL | Existing integration account |
| JIRA_API_TOKEN | Existing authorized Jira token; currently expires September 12, 2026 |
| JIRA_CONFIG_JSON | JSON contents of the local ignored `config/jira.json` |

Do not copy the local `DATABASE_URL`, local `APP_ORIGIN`, session tokens, demo simulation flags, or local DNS workaround into the cloud configuration.

## Deployment order

1. Use the existing Hobby plan with one replica per service. Workspace hard limits can stop unrelated existing services, so leave those unchanged.
2. Provision Postgres with pgvector, a generated password, private database URL and persistent volume. Enable backups within the approved budget.
3. Connect the private GitHub repository to relay-web. Apply its infrastructure settings and runtime variables, allocate an HTTPS domain, then deploy. Its pre-deploy command runs the idempotent schema migration only; it does not seed demo identities.
4. After web/schema readiness, deploy relay-worker using its worker start command and the same runtime variables. Keep one replica of each application service for the MVP.
5. Provision the owner with `npm run user:provision -- saksham 'Saksham Uboweja' operator` in the cloud runtime. Existing users are never promoted or overwritten by this command.
6. Run `npm run sources:copy-sandbox` locally with `SOURCE_DATABASE_URL` set privately to the local live database and `DATABASE_URL` privately set to the new target. It copies only approved, public, explicitly synthetic articles/cases with their embeddings. It does not copy reports, tickets, sessions, users or pending operations. Re-running skips existing source IDs. Verify 120 sources and populated embeddings. Remove any temporary public database proxy afterward.
7. Issue a fresh cloud session with `npm run session -- saksham` and sign in over HTTPS. Share the token only with its owner; it expires after eight hours.
8. Verify authenticated intake, one synthetic Jira ticket with read-back, duplicate submission protection, My requests after reload, and worker heartbeat/status synchronization. Confirm unauthenticated API access is rejected.

GitHub CI runs schema setup, tests, TypeScript and the production build against disposable PostgreSQL with pgvector. It uses no live API keys and sends no Jira requests.

Local deployment verification passed: clean Linux Docker build on Node 22; PostgreSQL 16 with pgvector schema migration; 120 synthetic source/embedding imports; owner provisioning; web readiness and unauthenticated API rejection; live sign-in with Secure/HttpOnly cookie; authenticated bootstrap; and worker heartbeat. The built runtime contained neither the local `.env` nor `config/jira.json`. All 51 automated tests passed. This verifies the deployment artifact locally, not a Railway release.

## Operations and limits

Deploy only after CI passes. For rollback, restore the previous application deployment while retaining the database volume; do not reseed, delete the volume, or replay old connector operations. The initial migration is additive/idempotent; future destructive migrations need their own recovery plan.

The hosted MVP still uses expiring session tokens, synthetic knowledge sources and local-only security review. It has no enterprise SSO, production security destination, or representative live accuracy guarantee. Broad public employee rollout is a separate hardening task.

Railway bills actual resource usage; current credits do not guarantee a free ongoing deployment. OpenAI API usage is billed separately. See [Railway pricing](https://docs.railway.com/pricing/plans), [cost controls](https://docs.railway.com/pricing/cost-control), and [pgvector Docker images](https://github.com/pgvector/pgvector#docker).
