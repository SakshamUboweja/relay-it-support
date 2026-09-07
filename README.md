# Relay

Turn an IT problem into a reviewed Jira support request. Describe the issue in chat, check the AI-prepared ticket, edit its fields, attach files, and approve it before anything is sent to Jira.

**Live MVP:** [Open Relay](https://relay-web-production-6f5f.up.railway.app) · Sign in with an operator-issued session token.

## How it works

1. **Describe your issue.** You can attach one screenshot to the message. The intake agent extracts details and uses approved sources to suggest help or prepare a support request. Intake runs as one of three selectable pipelines — deterministic rules, a single agent, or a triage agent with an independent reviewer.
2. **Review the draft.** A separate verifier checks it against your conversation and Jira's fields. A Jira-style form appears beside your original issue, with a calibrated confidence indicator and a **Why this team?** explanation of the routing.
3. **Edit and approve.** Save and recheck changes, add attachments, then select **Approve and send to Jira**. Only the requester can approve.
4. **Track the request.** Relay shows the Jira reference, status, and attachment delivery. Operators can inspect the decision record — candidate teams, confidence signals and the agent timeline — and correct routing.

Attachments: up to **3 files, 5 MB each** — PDF, PNG, JPEG, TXT, or LOG. The verifier checks ticket text, not file contents. A chat screenshot (PNG or JPEG, up to 5 MB) is shown to the intake agent only and is staged as an attachment; it is dropped if the Jira request type does not accept attachments.

## Stack

- **Frontend:** React / Next.js with TypeScript, built as static files.
- **Backend:** Python / FastAPI with a budgeted multi-agent intake pipeline, persisted agent traces and a separate verifier model call, all through OpenAI.
- **Storage:** PostgreSQL with pgvector for reports, source retrieval, and queued work.
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

Open [localhost:3000](http://127.0.0.1:3000). Demo mode uses synthetic data and simulated tickets; no API keys are needed. The command builds the UI and starts both Python processes.

The demo requires `APP_MODE=demo`. The copy command preserves an existing `.env`; if this checkout is already configured for live use, follow [local setup](LOCAL_SETUP.md) and run `npm run live` instead. Keep credentials in the ignored `.env` file.

## Current status

The Python backend and Jira-style review flow are deployed. The latest verified release passed **310 Python tests** and **52 web tests**, TypeScript checks, the production build, and hosted checks. Real Jira tests verified approved fields, attachments, and duplicate-submission protection.

The four intake arms were compared on the 120-case synthetic corpus with `gpt-5.6-terra` at reasoning effort medium. On the held-out split, routing accuracy was 71.7% for the deterministic rules and 98.3% for the single agent against 96.7% for the multi-agent arm; both model arms reached 10/10 security recall against 3/10 for the rules. The single agent had the best calibration (ECE 0.002) at 2.3× lower latency and 2.4× lower estimated cost than the multi-agent arm, so **`RELAY_PIPELINE=single` is the shipped default** and the multi arm stays selectable for its reviewer verdicts and cited sources. Labels are agent-authored and not human reviewed; the held-out split was already inspected during development, so this is a holdout-informed regression comparison, not a clean holdout claim; costs are estimates, not billing records; the model arms ran at medium effort while production uses high; and the dev-split ECE of 0.000 is in-sample because calibration is fitted on dev. Full table and caveats: [arm comparison results](evaluation/ARMS-RESULTS.md).

This is an MVP, not a production-ready enterprise help desk:

- Knowledge sources are **20 synthetic articles and 100 synthetic cases**, not company data or imported Jira history.
- The arm comparison used agent-authored labels on a split already seen during development. A human-reviewed, freshly authored holdout is still needed before any claim about real routing quality.
- Security-related reports stay in restricted local review instead of being sent to Jira.
- Enterprise SSO, broader accessibility/mobile testing, and automatic GitHub-to-Railway deployment remain pending.

## Development and details

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
- [Frontend review](FRONTEND_REVIEW.md) — Jira-style interface and testing limits
- [Deployment](DEPLOYMENT.md) — Railway configuration and current release
- [Multi-agent intake design](MULTI_AGENT_PLAN.md) — the arms, the lanes a model may use, confidence and traces
- [Build status](BUILD_STATUS.md) · [Evaluation results](evaluation/python-RESULTS.md) · [Arm comparison](evaluation/ARMS-RESULTS.md)
