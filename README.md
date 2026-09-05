# Relay — employee IT intake and triage

A working local MVP based on `AI_TICKETING_BUILD_BRIEF.md`. Describe an IT issue, receive one approved procedure or one clarification, and retain a durable report whether it is resolved, associated with an advisory, or handed to support. The operator console exposes facts, sources, routing decisions, acknowledgements, correction jobs, and integration failures.

**Status:** local application and real Jira/OpenAI sandbox handoff verified. The configured machine can run `npm run live`; see [LOCAL_SETUP.md](LOCAL_SETUP.md) for login, credential locations, token expiry and the separate sandbox database. The deterministic demo router misses the requested evaluation targets, especially security recall. This is a portfolio MVP, not an unattended production triage system. See [BUILD_STATUS.md](BUILD_STATUS.md) and [measured results](evaluation/RESULTS.md).

## Run locally

Requires Node 22.13+ (tested on Node 24.16), npm, and PostgreSQL with pgvector. Dependencies are pinned in package-lock.json.

```sh
npm ci
cp .env.example .env
docker compose up -d --wait db
npm run demo
```

Open **http://127.0.0.1:3000**. `npm run demo` seeds the database and starts the Next.js app plus pg-boss worker. Ctrl-C stops the application processes; PostgreSQL data remains in the Docker volume. Do not run two web servers on port 3000.

On the build machine, Docker was installed but its daemon was stopped. A dedicated native PostgreSQL 14 cluster with pgvector was started on `127.0.0.1:55432` instead; the supplied local `.env` connects to it. The cluster lives in the parent task's `work/pgdata`, separate from the project source. Use Docker instructions above when moving the project. Native PostgreSQL is also supported: create a database named `relay`, install pgvector, and set `DATABASE_URL`.

Separate process commands:

```sh
npm run setup
npm run dev
# in a second terminal
npm run worker
```

The demo is deliberately loopback-only. A signed demo persona selector includes Maya (San Francisco), Jordan (London), and Alex (operator). Anyone with access to the local demo may switch to the demo operator; this is an explicit simulation, not production authentication.

## Repeatable demonstration

1. **Approved help:** as Maya, enter “My Wi-Fi keeps disconnecting.” One approved procedure appears. Waiting does not resolve it. Choose **Fixed it** and find the saved resolution in My requests; no provider ticket exists.
2. **Zero-question handoff:** enter “VPN broke after I changed my password.” Catalog policy routes it to Identity & Access. The worker creates a `DEMO-...` request; impact and urgency stay unknown, cached credentials stay a hypothesis.
3. **Shared advisory:** enter “Atlas is loading slowly.” Maya can follow the San Francisco advisory. Her report stays separate; this is an in-app follow, not a Jira subscription. Jordan cannot see that advisory. Fixture advisories expire after 24 hours; to intentionally refresh the demo advisory, update its timestamp in the demo database or recreate the disposable demo database and run setup.
4. **Ambiguity:** enter “I cannot get in,” then “Still no luck.” Only one question is asked before general intake. Send to support bypasses the question.
5. **Lost response:** stop the worker, set `DEMO_LOST_CREATE_RESPONSE=1`, restart it, and submit “VPN cannot reach the server.” The mock provider persists one ticket, then throws. The operation is unknown; later reconciliation recovers that same ticket. Restore the variable to `0` afterward. Automated tests cover immediate empty searches and duplicate delivery too.
6. **Restricted review:** enter “I approved an unexpected MFA prompt.” It bypasses troubleshooting and stays local for restricted review. As Alex, open Operations to inspect the decision and failure reason. No external security request is created because restricted provider permissions have not been verified.
7. **Operator correction:** select an ordinary created request, enter a team, priority and reason, then queue the correction. The UI shows provider values only after worker read-back confirms them. Acknowledge the case to stop its review timer.

## Architecture and persistence

- Next.js App Router UI and server routes; TypeScript and Zod shared schemas.
- PostgreSQL owns reports, messages, evidence snapshots, decisions, sources, sessions, action receipts, provider-operation outbox, and operator events. `reports` is also the conversation aggregate: messages attach to its ID rather than an additional conversations table.
- pgvector stores 256-dimensional source embeddings in live mode. Demo retrieval uses PostgreSQL full-text ordering and seeded deterministic fixtures. Approved semantic results join lexical results in live mode.
- A separate Node worker uses pg-boss and periodically dispatches persisted outbox operations. The report and outbox insert share a transaction; queue delivery may repeat safely. Processing states also resume after restart.
- Report interaction state is separate from connector operation state. A failed routing update preserves the created request and retries only the update.
- Durable advisory locks serialize operations across worker instances. A create is committed as `unknown` before HTTP dispatch. Lost responses reconcile an exact correlation marker; empty results never automatically authorize a new create. Multiple results or exhausted reconciliation go to review. This is tested deduplication, not a universal exactly-once guarantee.
- Jira is authoritative for status and assignment. Polling mirrors status every 60 seconds; observed human changes do not trigger automatic rerouting. The ten-minute application reminder is deduplicated and is not a Jira SLA.

Configuration: [config/policy.json](config/policy.json), [Jira mapping template](config/jira.example.json), [migration](migrations/001_initial.sql). The demo has 20 approved article fixtures, 100 templated sanitized historical cases, three profiles, and three incident fixtures. These are synthetic and intentionally small.

## Jira sandbox setup

Use a dedicated test service project with synthetic data. The configured HELP project contains retained synthetic verification tickets HELP-1 and HELP-2.

1. Copy `config/jira.example.json` to `config/jira.json`. Replace the site, project, desk, ordinary/fallback request types, all team option IDs, and priority IDs with discovered sandbox values. The example numbers are placeholders, not valid configuration for your organization.
2. Configure a single-select Support team field and queues whose JQL filters use its options. Setting this field routes a request to a queue; it does not assign a human or prove a notification occurred.
3. Provide server-only `JIRA_EMAIL` and `JIRA_API_TOKEN`. The API token account needs JSM request creation, Browse Projects, Edit Issues, applicable issue visibility, and editable support-team/priority fields.
4. General intake must require only truthful supplied fields or explicitly configured administrative defaults. Discovery validates ordinary and fallback fields. Missing required fields preserve a local review state.
5. `reporterMode: integration-account` keeps the integration account as the real Jira reporter. The employee is **not** silently impersonated. For on-behalf-of mode, discovery must allow it and the user's `external_account` mapping must be populated.
6. `npm run preflight` discovers request fields and permissions. With live mode enabled it also performs small real structured-output and embedding requests, so it uses API quota. Request-specific editable team/priority options are checked before every update.
7. After authorizing the dedicated sandbox, set `ALLOW_LIVE_SMOKE=1` and run `npm run smoke:live`. This creates exactly one synthetic request and confirms its team field. It does not modify unrelated requests or delete the test ticket. If its create response is lost, inspect the emitted correlation marker/provider before another run; the standalone smoke command is intentionally not the application's durable workflow.

Basic API-token authentication is a local sandbox choice; OAuth installation is deferred. Security requests are always blocked from external delivery in this MVP. Enabling them requires an implemented, verified issue-security destination, not toggling a boolean.

The adapter supports field discovery, truthful payload validation/fallback, JSM customer request create/read, allowlisted field updates with option validation and read-back, and paginated correlation reconciliation. In-app incident follows remain local; provider linking and configured mandatory individual-request logging for related incidents are **not implemented**. See [official API research](config/API-REFERENCES.md).

## Live model and sessions

For the Railway web/worker/PostgreSQL deployment, runtime variables, data import and rollback procedure, see [DEPLOYMENT.md](DEPLOYMENT.md). Relay currently uses one model extraction step with application-controlled orchestration; it is not a multi-agent system.

Use a **separate database** for live mode. `npm run setup` migrates only the schema in live mode and does not install demo fixtures. Persisted demo jobs are rejected by live dispatch and vice versa. Set `APP_MODE=live`, a fresh random `SESSION_SECRET`, `APP_ORIGIN`, `OPENAI_API_KEY`, `OPENAI_MODEL`, and `OPENAI_EMBEDDING_MODEL`. No model name is invented or silently substituted. Preflight verifies actual configured account access, structured output, and 256-dimension embedding support. Unknown/refused/incomplete model results preserve known facts for human intake; there is no mock fallback.

Live user records must be provisioned by the operator in `users`, with appropriate role, scope and external account mapping. Demo persona switching disappears. `npm run session -- USER_ID` issues a revocable, eight-hour server session token for the login form. Session rows can be deleted by an operator to revoke access. This is minimal local sandbox authentication; SSO and self-service user administration are deferred.

`npm run embed:sources` sends approved article/case text to the configured embedding model and writes vectors. Extraction uses the OpenAI Responses API with strict Zod output, exact quote validation, allowlisted evidence IDs, one bounded retry, and no tools. Live extraction may flag conflicts or security evidence; deterministic catalog policy still controls actions. This MVP has no second LLM reranker call or calibrated confidence model. Do not treat constrained JSON as proof of factual correctness.

Current local configuration is `OPENAI_MODEL=gpt-5.6-terra`, `OPENAI_REASONING_EFFORT=high`, and `OPENAI_MAX_OUTPUT_TOKENS=8192`. Embeddings remain `text-embedding-3-small`. The versioned Relay prompt in `server/intake-prompt.ts` feeds extracted facts into the workflow; no Codex SDK is embedded. Known impact/urgency and reported attempts are preserved, direct support requests skip troubleshooting, and Jira receives a concise summary and readable description. Existing tickets are not rewritten. Extraction is bounded to 60 seconds per attempt. Changing models requires restarting both web and worker processes; use `npm run live`.

## Data handling and limits

Known API-key, bearer-token, password-assignment, and MFA-code patterns are redacted before report storage/model calls. This is best-effort sanitation, not comprehensive DLP; users must avoid secrets and confidential records. Messages and retrieved text are treated as untrusted data. React escapes output, queries are parameterized, source visibility is filtered server-side, and embedded sources are re-resolved against current permissions. Historical conversation messages remain part of the originally authorized report record. Operators can inspect all reports; employees only their own reports and authorized shared summaries.

Live message text and approved sources may go to OpenAI. Model calls use `store:false`; local conversations and decisions persist in PostgreSQL. No regulatory certification is claimed. Raw credentials are environment-only and never sent to the model or returned in health responses. Source deletion/revocation does not retroactively remove previously displayed text from conversation history.

`npm run retention` previews resolved local reports older than the configured 30 days. `npm run retention -- --apply` removes those local records and child audit data; it preserves active requests and does not delete Jira requests. This is a manually invoked retention tool, not a scheduled production compliance system.

## Verification and evaluation

```sh
npm test
npm run typecheck
npm run build
npm run eval
npm run preflight
# with app + worker running; retains two synthetic demo records
npm run smoke:http
```

The verified suite contains 51 passing tests. The original demo HTTP smoke passed seven end-to-end checks; live browser handoff created HELP-2 and confirmed Identity & Access routing. Dependency audit at the original build reported zero known vulnerabilities. Tests need a seeded demo PostgreSQL database and APP_MODE=demo; do not point tests at relay_sandbox. Stop the demo worker while running tests because tests intentionally control operation delivery timing. Tests remove their own generated reports afterward. The suite covers policy facts, confirmation semantics, one-question fallback, authentication/origin boundaries, visibility revocation, mode isolation, source injection, persistence, duplicate submissions, lost create responses, transient validation retries, rate limiting, Jira payloads, routing failures, human updates and review timers. The quality regression tests inject model/retrieval results in an isolated test process and verify the actual workflow/outbox contract without calling live providers. Focused live Terra checks are documented in LOCAL_SETUP.md and do not replace representative evaluation.

The evaluation runner compares keyword/catalog and retrieval-plus-deterministic-policy methods on 60 development and 60 held-out synthetic scenarios, with families separated and held-out content excluded from retrieval. The independent scenario author did not inspect the router. Labels remain agent-authored and **not human reviewed**. See [results](evaluation/RESULTS.md), [full counts and failures](evaluation/results.json), and [dataset limitations](evaluation/DATASET.md). It records the frozen source hash; rerunning does not justify a new untouched-holdout claim after tuning against these failures.

Initial heldout results: keyword accuracy 44/60; proposed accuracy 43/60; accepted-route precision 34/43 (79.1%); eligible coverage 42/50 (84%); security recall 3/10. The proposed demo system did not beat the keyword baseline on overall heldout routing. This is a documented failed target, not hidden behind a polished interface. Live LLM-only baseline, live routing evaluation, human label review, true related-incident precision/recall, model cost and real-world resolution effectiveness remain unmeasured. Browser interaction/accessibility testing is also pending; HTTP/API journeys and the production build were exercised.

## Implementation choices and remaining scope

The initial Sites starter was used for UI components, then the runtime was changed to the brief's requested **Next.js + PostgreSQL + Node worker**. Unused Worker/Vinext runtime packages were removed after dependency audit findings. Sites hosting does not support this PostgreSQL TCP connection plus independent persistent worker architecture. This delivery is local; deployment needs a Node-capable web/worker host and managed PostgreSQL, or a separately scoped architecture adaptation. No nonfunctional hosted facade was published.

The browser includes an optional WebMCP `start_it_report` staging tool (not submission). It is feature-detected; a supported WebMCP verification context was unavailable, so this optional surface is unverified.

The September 5 setup connected the user's OpenAI key and a new Jira Free test workspace, created synthetic test tickets, and enabled local live-mode operation. No payment details were added or cloud hosting provisioned. OpenAI requests consume metered API usage. See LOCAL_SETUP.md for the configured environment. Production projects were not modified.
