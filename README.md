# Relay

Turn an IT problem into a reviewed Jira support request. Describe the issue in chat, attach a screenshot, check the AI-prepared ticket, edit its fields, and send it to Jira when it looks right.

**Live:** [Open Relay](https://relay-web-production-6f5f.up.railway.app) · Sign in with an operator-issued session token.

## How it works

1. **Describe your issue.** Attach a screenshot if it helps. The intake agent reads both, extracts the details it can quote, and either suggests an approved fix or prepares a support request.
2. **Agents triage it.** A triage agent looks up the service catalog, similar resolved cases, and the knowledge base, then proposes a route. An independent reviewer checks that proposal against your own words before anything is written down.
3. **Review the draft.** A Jira-style form appears beside your issue with a calibrated confidence indicator and a **Why this team?** explanation of the routing. A verifier checks the ticket against your conversation and Jira's own fields.
4. **Edit and send.** Save and recheck changes, add attachments, then select **Send**. Sending approves the ticket and creates the Jira request; only the requester can send it.
5. **Track the request.** Relay shows the Jira reference, status, and attachment delivery. Operators inspect the full decision record — candidate teams, confidence signals, and the agent timeline — and can correct routing.

## What's in it

- **A multi-agent intake pipeline.** Intake reads the message and any screenshot, triage runs read-only tools over the catalog, past cases, and knowledge articles, and a reviewer agent accepts, sends the proposal back for one revision, or escalates to a human. Each stage runs under a budget of model calls, tool calls, tokens, and seconds; a run that hits its ceiling stops cleanly and the deterministic decision stands.
- **Bounded model authority.** A model may break a routing tie toward a service named in the message, abstain, or cite quoted evidence for impact, urgency, and security. Priority, escalation, restricted visibility, and every Jira write stay with application code.
- **Calibrated confidence** on every request, fitted with isotonic regression on a development split, with plain-language reasons behind it.
- **Persisted agent traces.** Every model and tool call is recorded with its prompt version, token usage, latency, and estimated cost, and shown to operators as a timeline.
- **Screenshot intake.** Paste, drag, or pick a PNG or JPEG in chat. The intake agent sees it, and it is staged as a ticket attachment for delivery after approval.
- **Selectable arms.** `RELAY_PIPELINE` also offers `single` (one agent) and `deterministic` (rules plus extraction). All three compose through the same deterministic policy, so an arm can be swapped without changing what the application is allowed to do.
- **An evaluation harness.** `npm run eval` scores every arm on the 120-case corpus for routing, security recall, calibration, latency, and cost, and caches each result so re-runs are free. Results: [arm comparison](evaluation/ARMS-RESULTS.md).
- **Requester approval throughout.** Nothing reaches Jira before the requester sends it, enforced at the API, the outbox, a database trigger, and the worker.

Attachments: up to **3 files, 5 MB each** — PDF, PNG, JPEG, TXT, or LOG. A chat screenshot goes to the intake agent and rides along as an attachment.

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

## Signing in

Live mode has no password form: an operator provisions the account and issues a short-lived session token that the person pastes into Relay's sign-in box.

```sh
# once per person — role is employee or operator
uv run python -m relay.cli provision-user alex 'Alex Rivera' employee
# each time they sign in — the token lasts eight hours
npm run session -- alex
```

On the hosted app, run the same commands inside the Railway service; [deployment](DEPLOYMENT.md) has the exact invocation. Tokens are credentials: hand them to their owner only, and never commit them.

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
