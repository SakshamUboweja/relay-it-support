# Build status

## Implemented MVP

- [x] Independent verifier agent, actual Jira field preview, editable drafts and required requester approval. Versioned approval guards extend through the database and worker. Attachments are staged until approval with bounded storage, per-file delivery status and uncertain-upload reconciliation. See TICKET_REVIEW.md.

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

## Multi-agent comparison

- [x] Three selectable intake arms behind `RELAY_PIPELINE` — `deterministic`, `single`, `multi` — sharing one evidence packet and one deterministic policy. Budgets per arm in `config/policy.json`; a run over budget stops as `budget_exhausted` and the deterministic decision stands. Prompts `relay-intake-v3`, `relay-single-v2`, `relay-triage-v1`, `relay-reviewer-v1`.
- [x] Bounded proposal lanes in `relay/intake_evidence.py`: a model may tie-break to a service named in the message or in screenshot text, abstain, or cite a validated blocked-work, broad-impact or security quote. It never writes a priority, escalation or visibility string, never selects Security Review, and is ignored on an already restricted report. The reviewer may request human review or restrict on a validated quote only.
- [x] Calibrated confidence with five weighted signals, per-arm isotonic breakpoints in `config/calibration.json`, and a plain-language "why" shown to the requester. The calibration table was fitted after the restricted-confidence change, on the dev split only.
- [x] Persisted agent traces in `agent_runs` and `agent_steps` (migration `003_agent_traces.sql`, additive), with per-step usage, estimated cost from `config/pricing.json` and latency; shown as an operator timeline.
- [x] Screenshot intake: multipart `POST /api/intake` accepts one PNG or JPEG up to 5 MB (migration `004_intake_images.sql`, additive). It is shown to the intake role only, staged as an attachment, and deleted when the Jira request type does not accept attachments. `python-multipart` added.
- [x] Asynchronous intake for the multi arm: in live mode the API answers `{"state":"processing"}` and the worker's `resume_intakes` completes the pipeline and prepares the review. `RELAY_INTAKE_INLINE=1` forces inline execution locally.
- [x] Arm comparison harness (`npm run eval -- --arm all --split all --effort medium`) with a resumable cache under `evaluation/cache/`, calibration fitted on dev only, results in `evaluation/arms-results.json` / `evaluation/ARMS-RESULTS.md`, and `GET /api/evaluation` for operators. `--arm rules-v1` runs the legacy runner, pinned to scoring v1.
- [x] Frontend: confidence badge and "Why this team?" in the review form, pipeline chip, operator decision record with candidate teams and the agent timeline, Operations pipeline-comparison panel, chat screenshot attach, and polling that pauses on hidden tabs.
- [x] 316 Python tests and 52 web tests pass. `npm run preflight` prints `{"vision": true}` for the image path.
- [x] Live comparison run on 2026-09-07 with `gpt-5.6-terra` at reasoning effort medium; about $3 of live spend across all runs. Held-out routing accuracy: rules-v1 43/60, rules-v2 43/60, single 59/60, multi 58/60; security recall 3/10, 3/10, 10/10, 10/10. `RELAY_PIPELINE=single` and scoring v2 shipped as the defaults. The multi arm made one tool call (`lookup_catalog` on heldout-029) and cited no sources across its 120 cases, so its routing was not tool-grounded; requiring a tool call on the first triage turn is a follow-up, not shipped. Numbers and caveats in `evaluation/ARMS-RESULTS.md`; design in `MULTI_AGENT_PLAN.md`.
- [x] Resolved the retained HELP-7 routing regression: laptop + Wi-Fi evidence no longer ties into a Service Desk fallback. Scoring v2 routes it to Network, and so do both model arms.

## Verified this session

- [x] Ticket-review release `d6af9d6` deployed after 144 tests and GitHub CI passed. Local browser/API HELP-10 and hosted HELP-11 verified two model roles, exact approved Jira fields, zero pre-approval writes, stale/repeated approval protection, and one attachment per ticket. Existing cloud records remain intact.

- [x] Python migration: API, authentication, extraction, retrieval, routing, Jira connector, durable worker and operator commands ported. React UI and Railway infrastructure definition remain TypeScript. Existing database schema and session formats retained.
- [x] All 120 deterministic policy outputs match the original TypeScript snapshot. Historical evaluation results remain unchanged; Python results are recorded separately in `evaluation/python-RESULTS.md`.
- [x] Python live preflight: Jira field discovery, Terra/high structured extraction and 256-dimensional embeddings passed.
- [x] Python release `bb71361` deployed to both existing Railway application services after GitHub CI passed. HELP-9 verified authenticated intake, Identity & Access/Medium Jira read-back, fact preservation and duplicate protection. Prior HELP-7/HELP-8 reports and existing login token remain valid. Local port 3000 also runs the Python web/worker.

- [x] Railway deployment verified: HTTPS web, persistent worker, PostgreSQL 16/pgvector, 120 synthetic sources/embeddings, owner provisioning, secure session API, worker heartbeat and Jira HELP-7. Existing Hobby plan; one replica each. GitHub auto-deployment and browser sign-in await owner authentication; see DEPLOYMENT.md.

- [x] Real native PostgreSQL 14 with pgvector migration/seed and transaction tests.
- [x] At the ticket-review release `d6af9d6`: 144 passing Python unit and integration/adapter tests, including verifier grounding, requester approval, field/schema validation, durable attachments and the existing connector regressions. Dependency audit at the original build reported zero known vulnerabilities.
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

- [ ] Representative routing evaluation on human-reviewed labels and a freshly authored holdout. The measured arm comparison below used agent-authored labels on a split already seen during development.
- [ ] Human review of evaluation truth and a fresh holdout before any post-evaluation tuning claims.
- [ ] Approved semantic reranking with a second model call and broader fact-fidelity evaluation.
- [ ] Provider-enforced restricted security destination; all security handoffs currently remain local.
- [ ] Live related-request linking and configurable mandatory provider logging for shared-incident reports.
- [ ] Broad browser, keyboard, screen-reader and responsive interaction QA.
- [ ] Supported WebMCP contract verification.
- [x] Web/worker + persistent PostgreSQL deployment on Railway; this architecture is not Sites Worker-compatible.
- [ ] Production SSO, operational hardening, comprehensive DLP, production incident policies (outside MVP scope).

The local and hosted MVPs and ordinary Jira handoff are verified. Human-reviewed evaluation labels, remaining provider features, GitHub automatic deployment and production readiness remain incomplete. Independent verification, requester approval and the three intake arms are implemented; MULTI_AGENT_PLAN.md is now the design record of what shipped and holds the measured comparison.
