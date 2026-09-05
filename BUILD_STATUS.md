# Build status

## Implemented MVP

- [x] Static Next.js/React employee chat served by Python FastAPI, My requests, operator console, responsive styles.
- [x] PostgreSQL migration and synthetic seed data; persistent reports/messages/facts/decisions/context/outbox/events/sessions.
- [x] Explicit demo/live interfaces; no silent mock fallback; worker mode isolation.
- [x] Approved one-procedure flow, confirmed resolution, one clarification maximum, immediate support bypass.
- [x] Deterministic routing gates, unknowns/provenance, restricted local review, individual advisory reports.
- [x] Python worker polling the PostgreSQL outbox, atomic outbox, repeat-delivery locks, correlation recovery, status sync, one-time review reminder.
- [x] Jira customer-request adapter, discovery/fallback, controlled updates/read-back, rate-limit/error handling and contract tests.
- [x] OpenAI Responses extraction and embeddings interfaces, quote/ID validation, bounded calls, model preflight command.
- [x] Operator acknowledgement/correction/retry/refresh and server authorization boundaries.
- [x] 120-scenario dataset, frozen policy hash, measured baseline comparisons and failed examples.
- [x] README, sample mapping/environment, local startup, data handling and opt-in sandbox smoke command.

## Verified this session

- [x] Python migration: API, authentication, extraction, retrieval, routing, Jira connector, durable worker and operator commands ported. React UI and Railway infrastructure definition remain TypeScript. Existing database schema and session formats retained.
- [x] All 120 deterministic policy outputs match the original TypeScript snapshot. Historical evaluation results remain unchanged; Python results are recorded separately in `evaluation/python-RESULTS.md`.
- [x] Python live preflight: Jira field discovery, Terra/high structured extraction and 256-dimensional embeddings passed.

- [x] Railway deployment verified: HTTPS web, persistent worker, PostgreSQL 16/pgvector, 120 synthetic sources/embeddings, owner provisioning, secure session API, worker heartbeat and Jira HELP-7. Existing Hobby plan; one replica each. GitHub auto-deployment and browser sign-in await owner authentication; see DEPLOYMENT.md.

- [x] Real native PostgreSQL 14 with pgvector migration/seed and transaction tests.
- [x] 78 passing Python unit and integration/adapter tests, including fact handoff, readable ticket descriptions, support-request/attempt handling, transient validation retries and uncertain-create preservation. Dependency audit at the original build reported zero known vulnerabilities.
- [x] TypeScript and optimized Next.js build.
- [x] Local HTTP employee/session/operator workflows and persisted demo provider request.
- [x] Heldout synthetic results published honestly; accuracy/precision/security targets not met.
- [x] Free Jira workspace and restricted-customer HELP test project configured; six team queues verified.
- [x] Real Jira smoke HELP-1 and authenticated browser-to-worker handoff HELP-2, with team/priority read-back.
- [x] OpenAI extraction and 256-dimensional embeddings verified; 120 synthetic sources indexed in separate relay_sandbox database.
- [x] GPT-5.6 Terra/high configured and live extraction verified; versioned Relay prompt, grounded fact handoff, complete summaries, readable descriptions and repeated-troubleshooting bypass. Original monitor extraction plus five focused live regressions passed; broader model comparison remains pending.
- [x] Upgraded authenticated HTTP-to-worker test created HELP-4; Jira read-back verified summary, description, Endpoint and Medium. Submission replay produced no duplicate create operation. HELP-3 retained for before/after comparison.
- [x] Live operator sign-in and My requests verified in Chrome. Setup and credential locations documented in LOCAL_SETUP.md.

## Pending or incomplete compared with the full brief

- [ ] Live LLM-only comparison and representative routing evaluation; the small setup checks are not an evaluation benchmark.
- [ ] Human review of evaluation truth and a fresh holdout before any post-evaluation tuning claims.
- [ ] Improve security recognition and routing on development data; current heldout security recall is 3/10.
- [ ] Approved semantic reranking with a second model call and broader fact-fidelity evaluation.
- [ ] Provider-enforced restricted security destination; all security handoffs currently remain local.
- [ ] Live related-request linking and configurable mandatory provider logging for shared-incident reports.
- [ ] Broad browser, keyboard, screen-reader and responsive interaction QA.
- [ ] Supported WebMCP contract verification.
- [x] Web/worker + persistent PostgreSQL deployment on Railway; this architecture is not Sites Worker-compatible.
- [ ] Resolve the retained HELP-7 routing regression: laptop + Wi-Fi evidence produces a conflict and Service Desk fallback instead of Network.
- [ ] Production SSO, operational hardening, comprehensive DLP, production incident policies (outside MVP scope).

The local and hosted MVPs and ordinary Jira handoff are verified. Complete evaluation, remaining provider features, GitHub automatic deployment and production readiness remain incomplete. MULTI_AGENT_PLAN.md describes a proposed next step; it is not implemented.
