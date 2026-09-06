# Relay

Turn an IT problem into a reviewed Jira support request. Describe the issue in chat, check the AI-prepared ticket, edit its fields, attach files, and approve it before anything is sent to Jira.

**Live MVP:** [Open Relay](https://relay-web-production-6f5f.up.railway.app) · Sign in with an operator-issued session token.

## How it works

1. **Describe your issue.** The intake agent extracts details and uses approved sources to suggest help or prepare a support request.
2. **Review the draft.** A separate verifier checks it against your conversation and Jira's fields. A Jira-style form appears beside your original issue.
3. **Edit and approve.** Save and recheck changes, add attachments, then select **Approve and send to Jira**. Only the requester can approve.
4. **Track the request.** Relay shows the Jira reference, status, and attachment delivery. Operators can inspect decisions and correct routing.

Attachments: up to **3 files, 5 MB each** — PDF, PNG, JPEG, TXT, or LOG. The verifier checks ticket text, not file contents.

## Stack

- **Frontend:** React / Next.js with TypeScript, built as static files.
- **Backend:** Python / FastAPI with separate intake and verifier model calls through OpenAI.
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

The Python backend and Jira-style review flow are deployed. The latest verified release passed **144 automated tests**, TypeScript checks, the production build, and hosted checks. Real Jira tests verified approved fields, attachments, and duplicate-submission protection.

This is an MVP, not a production-ready enterprise help desk:

- Knowledge sources are **20 synthetic articles and 100 synthetic cases**, not company data or imported Jira history.
- Routing quality still needs improvement. The original synthetic evaluation missed its accuracy and security targets; representative live evaluation is pending.
- Security-related reports stay in restricted local review instead of being sent to Jira.
- Enterprise SSO, broader accessibility/mobile testing, and automatic GitHub-to-Railway deployment remain pending.

## Development and details

```sh
# Set TEST_DATABASE_URL to a separate pgvector database ending in _test.
uv run python -m pytest
npm run typecheck
npm run build
```

- [Local setup](LOCAL_SETUP.md) — live configuration and sign-in
- [Ticket workflow](TICKET_REVIEW.md) — verification, approval, and attachment behavior
- [Frontend review](FRONTEND_REVIEW.md) — Jira-style interface and testing limits
- [Deployment](DEPLOYMENT.md) — Railway configuration and current release
- [Build status](BUILD_STATUS.md) · [Evaluation results](evaluation/python-RESULTS.md)
