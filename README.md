# Relay

Turn an IT problem into a reviewed Jira support request. Describe the issue in chat, attach a screenshot, check the AI-prepared ticket, edit its fields, and send it to Jira when it looks right.

**Live:** [Open Relay](https://relay-web-production-6f5f.up.railway.app) · Sign in with an operator-issued session token.

## How it works

1. **Describe your issue.** Attach a screenshot if it helps. The intake agent reads both, extracts the details it can quote, and either suggests an approved fix or prepares a support request.
2. **Review the draft.** An independent verifier checks the ticket against your conversation and Jira's own fields. A Jira-style form appears beside your issue with a calibrated confidence indicator and a **Why this team?** explanation of the routing.
3. **Edit and send.** Save and recheck changes, add attachments, then select **Send**. Sending approves the ticket and creates the Jira request; only the requester can send it.
4. **Track the request.** Relay shows the Jira reference, status, and attachment delivery. Operators inspect the full decision record — candidate teams, confidence signals, and the agent timeline — and can correct routing.

## What's in it

- **Three selectable intake pipelines** behind `RELAY_PIPELINE`: deterministic rules, a single agent, and a multi-agent arm that runs triage with read-only tools plus an independent reviewer. All three compose through the same deterministic policy.
- **Bounded model authority.** A model may break a routing tie toward a service named in the message, abstain, or cite quoted evidence for impact, urgency, and security. Priority, escalation, restricted visibility, and every Jira write stay with application code.
- **Calibrated confidence** on every request, fitted with isotonic regression on a development split, with plain-language reasons behind it.
- **Persisted agent traces.** Every model and tool call is recorded with its prompt version, token usage, latency, and estimated cost, and shown to operators as a timeline.
- **Screenshot intake.** Paste, drag, or pick a PNG or JPEG in chat. The intake agent sees it, and it is staged as a ticket attachment for delivery after approval.
- **An evaluation harness.** `npm run eval` compares every intake arm on the 120-case corpus, caches each result so re-runs cost nothing, and publishes a table the Operations tab reads back.
- **Requester approval throughout.** Nothing reaches Jira before the requester sends it, enforced at the API, the outbox, a database trigger, and the worker.

Attachments: up to **3 files, 5 MB each** — PDF, PNG, JPEG, TXT, or LOG. A chat screenshot goes to the intake agent and rides along as an attachment.

## Measured results

Four evaluation arms on the 120-case corpus with `gpt-5.6-terra` at reasoning effort medium. Held-out split, 60 cases:

| Arm | Routing accuracy | Security recall | Escalation recall | Calibration (ECE) | p50 latency |
| --- | --- | --- | --- | --- | --- |
| Rules only | 71.7% | 3/10 | 6/20 | 0.123 | 0 ms |
| Single agent | **98.3%** | **10/10** | 19/20 | **0.002** | 2.4 s |
| Multi-agent | 96.7% | **10/10** | **20/20** | 0.017 | 5.5 s |

The single agent matches the multi-agent arm on routing and security recall with the best calibration, 2.3× lower latency, and 2.4× lower cost, so **`RELAY_PIPELINE=single` is the shipped default**. The multi-agent arm stays selectable for its reviewer verdicts. Full table, per-case results, and the caveats that bound these numbers: [arm comparison results](evaluation/ARMS-RESULTS.md).

## Stack

- **Frontend:** React / Next.js with TypeScript, built as static files.
- **Backend:** Python / FastAPI with a budgeted agent runtime, persisted traces, and an independent verifier model call, all through OpenAI.
- **Storage:** PostgreSQL with pgvector for reports, source retrieval, agent traces, and queued work.
- **Integration:** Jira Service Management, delivered by a Python background worker after approval.
- **Hosting:** Railway web, worker, and PostgreSQL services.

## Run locally

Requires Python 3.13, uv, Node.js 22.13+, npm, and Docker for the demo database.

```sh
npm ci
uv sync --frozen
cp -n .env.example .env
docker compose up -d --wait db
npm run demo
```

Open [localhost:3000](http://127.0.0.1:3000). Demo mode runs on synthetic data and simulated tickets, so no API keys are needed. The command builds the UI and starts both Python processes. Relay ships with 20 synthetic knowledge articles and 100 historical cases for demos and evaluation.

The demo requires `APP_MODE=demo`. The copy command preserves an existing `.env`; if this checkout is already configured for live use, follow [local setup](LOCAL_SETUP.md) and run `npm run live` instead. Keep credentials in the ignored `.env` file.

## Development

The latest verified release passes **316 Python tests** and **52 web tests**, TypeScript checks, the production build, and the Docker image build.

```sh
# Set TEST_DATABASE_URL to a separate pgvector database ending in _test.
uv run python -m pytest
npm run test:web
npm run typecheck
npm run build
# Compare the intake arms; model arms need live mode, a key and a seeded database.
npm run eval -- --arm all --split all --effort medium
```

- [Local setup](LOCAL_SETUP.md) — live configuration and sign-in
- [Ticket workflow](TICKET_REVIEW.md) — verification, approval, and attachment behavior
- [Multi-agent intake design](MULTI_AGENT_PLAN.md) — the arms, the lanes a model may use, confidence and traces
- [Frontend review](FRONTEND_REVIEW.md) — the Jira-style interface
- [Deployment](DEPLOYMENT.md) — Railway configuration and current release
- [Build status](BUILD_STATUS.md) · [Arm comparison](evaluation/ARMS-RESULTS.md) · [Policy regression](evaluation/python-RESULTS.md)
