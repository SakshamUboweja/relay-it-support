# Railway deployment

Status: Jira-style request workspace deployed September 5, 2026 on the existing Hobby workspace, with no plan upgrade. Open https://relay-web-production-6f5f.up.railway.app. Web runs committed release `e8d659d`; worker remains on `d6af9d6` because this release changes only the frontend. Both releases were uploaded through the Railway CLI after GitHub CI passed. GitHub automatic deployment still requires the owner's authentication and granting Railway access to this private repository.

Railway project: `8e0945be-9448-4bdc-9c76-0967baf67442`, production environment `49371d2c-2cd1-464e-8eaa-44c72c2cda08`. Web deployment `bce712e0-e2bd-4dbb-b600-fdc7e000bab8`; worker deployment `6f07b1d8-8f99-4753-b52e-8f4d050bc13a`. All three services are online in US West, one replica each.

## Architecture

Relay uses two model roles: intake extracts grounded facts, and an independent verifier checks the prepared Jira draft. Application code retrieves scoped sources, applies routing/security policy and validates fields. Only requester approval releases the durable Jira operation. The worker handles delivery and attachment reconciliation; it is not an AI agent. See TICKET_REVIEW.md for the implemented boundaries.

Use one Railway project with three services in the same region:

| Service | Source | Configuration | Public exposure |
| --- | --- | --- | --- |
| relay-web | GitHub `SakshamUboweja/relay-it-support`, main | `.railway/railway.ts` | Railway HTTPS domain |
| relay-worker | Same repository and commit | `.railway/railway.ts` | None |
| Postgres | `pgvector/pgvector:pg16` | Persistent volume `/var/lib/postgresql/data`; database `relay` | Private networking; temporary proxy only for import if needed |

The shared Docker image builds the static Next.js interface with Node 22, then runs Python 3.13 as the unprivileged `relay` user. Node and npm are absent from the runtime. FastAPI serves the interface and API; a separate Python process drains the existing PostgreSQL outbox. Local credentials, mapping files and build output are excluded from the build context. The web service listens on `0.0.0.0` and Railway's `PORT`. `/api/health` checks database/schema readiness without revealing configuration. Web and worker validate live configuration before starting.

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
5. Provision the owner with `python -m relay.cli provision-user saksham 'Saksham Uboweja' operator` in the cloud runtime. Existing users are never promoted or overwritten by this command.
6. Run `npm run sources:copy-sandbox` locally with `SOURCE_DATABASE_URL` set privately to the local live database and `DATABASE_URL` privately set to the new target. It copies only approved, public, explicitly synthetic articles/cases with their embeddings. It does not copy reports, tickets, sessions, users or pending operations. Re-running skips existing source IDs. Verify 120 sources and populated embeddings. Remove any temporary public database proxy afterward.
7. Issue a fresh cloud session with `python -m relay.cli session saksham` and sign in over HTTPS. Share the token only with its owner; it expires after eight hours.
8. Verify authenticated intake, one synthetic Jira ticket with read-back, duplicate submission protection, My requests after reload, and worker heartbeat/status synchronization. Confirm unauthenticated API access is rejected.

GitHub CI runs Ruff, the Python suite, TypeScript checks, static UI compilation and the Python Docker build against disposable PostgreSQL with pgvector. It uses no live API keys and sends no Jira requests.

Python migration verification passed: 78 automated tests against isolated PostgreSQL schemas, all 120 deterministic policy outputs matching the original snapshot, TypeScript checks, static frontend build, and Linux Docker build. A disposable PostgreSQL 16/pgvector container verified schema setup, static UI, API intake, duplicate submission protection, worker completion and heartbeat. The runtime contains Python 3.13.15 and excludes Node, node_modules, local `.env` and private Jira mapping files. Live Python preflight verified Jira field discovery, Terra/high structured extraction and 256-dimensional embeddings.

The Python Railway release passed HTTPS health, unauthorized access rejection, legacy session sign-in with Secure/HttpOnly cookie, authenticated bootstrap, existing HELP-7/HELP-8 read-back and worker heartbeat. Both processes report Python 3.13.15. No schema, source, credential or session migration was required. The existing 120 indexed sources were retained. Infrastructure planning reports no drift and no resources added or destroyed.

The migration test created [HELP-9](https://relay-saksham.atlassian.net/servicedesk/customer/portal/2/HELP-9). A VPN/password-change report routed to Identity & Access with Medium priority and Waiting for support. Jira read-back verified the summary and preserved password-change context, restarted-client step and browser-app workaround. Both create and routing update succeeded; replaying the submission returned the same report and a single create operation. This is a focused migration check, not a new accuracy benchmark.

The original Node deployment also passed cloud health, authentication and worker checks. Its initial 120-source import used SSH; the temporary public PostgreSQL proxy was removed. The Python cutover preserved all records created since that import.

The hosted test created [HELP-7](https://relay-saksham.atlassian.net/servicedesk/customer/portal/2/HELP-7). Replaying its submission returned the same report; one create operation and its routing update succeeded. Jira read-back confirmed readable facts, completed troubleshooting, Medium priority and Waiting for support. Routing quality failed the intended Network expectation: laptop and Wi-Fi evidence tied and the conflict gate selected Service Desk. This is a retained development regression, not a successful routing evaluation.

For local operator access to the cloud, the ignored `.local/railway-deploy-key` is a dedicated SSH key registered as “Relay deployment.” The CLI is installed under `.local/railway-cli/`. Issue a fresh eight-hour owner session with:

```sh
.local/railway-cli/node_modules/.bin/railway ssh --service relay-web -i "$PWD/.local/railway-deploy-key" -- python -m relay.cli session saksham
```

The initial cloud token is in the owner-only ignored `.local/railway-session.token`. Paste it into the hosted sign-in form; it expires after eight hours. Browser sign-in remains for the owner to complete after browser control was interrupted. Do not commit tokens or SSH private keys.

Until GitHub source access is connected, deploy a clean archive of a CI-verified commit with `railway up PATH --path-as-root --service relay-web --detach`, then the same archive with `--service relay-worker`. Keep credentials exclusively in Railway variables. The checked-in infrastructure definition currently matches the CLI-managed service configuration; after connecting GitHub, run `railway config pull` without `--include-variables` to record that source change.

## Operations and limits

The ticket-review release adds migration `002_ticket_review.sql`, which creates versioned reviews, bounded attachment storage and an approval trigger. Retain that trigger on rollback: an older worker must not send a new unapproved report. Older releases cannot render pending review forms, so prefer a forward fix when reviews are active. No migration removes historical records, and no object-storage or additional Railway service is required.

Deploy only after CI passes. For rollback to the former Node release, first restore its web/worker start commands and web pre-deploy command from commit `d5a93f9`, then restore that deployment while retaining the database volume; do not reseed, delete the volume, or replay old connector operations. The initial migration is additive/idempotent; future destructive migrations need their own recovery plan.

The hosted MVP still uses expiring session tokens, synthetic knowledge sources and local-only security review. It has no enterprise SSO, production security destination, or representative live accuracy guarantee. Broad public employee rollout is a separate hardening task.

Railway bills actual resource usage; current credits do not guarantee a free ongoing deployment. OpenAI API usage is billed separately. See [Railway pricing](https://docs.railway.com/pricing/plans), [cost controls](https://docs.railway.com/pricing/cost-control), and [pgvector Docker images](https://github.com/pgvector/pgvector#docker).

The frontend release passed CI run `34005836712` (144 Python tests, typecheck, static build and Docker build). Hosted verification confirmed HTTP 200 health, unauthorized access rejection, authenticated loading of the new UI assets, existing HELP-11 approval and attachment preservation, and a healthy worker. No Jira request was created for the frontend deployment check. See FRONTEND_REVIEW.md for visual and interaction checks and their limits.
