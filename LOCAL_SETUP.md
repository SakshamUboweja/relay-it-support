# Configured local sandbox

Project folder: `/Users/saksham/Desktop/relay`.

Jira site: https://relay-saksham.atlassian.net

Service project: **Relay Test (HELP)**, company-managed, created through the Free signup. Customer request submission is restricted to invited users. No payment details were added.

Credentials are stored only in the local, Git-ignored `.env` file, with owner-only filesystem permissions. `JIRA_API_TOKEN` is the **Relay local MVP** token, expiring **September 12, 2026**. Replace it before expiry. The OpenAI key is `OPENAI_API_KEY`; the models are `gpt-5.6-terra` with `OPENAI_REASONING_EFFORT=high` and `text-embedding-3-small`. `OPENAI_MAX_OUTPUT_TOKENS=8192` bounds each extraction response, including reasoning. Extraction has a 60-second timeout and at most one retry. Live OpenAI calls consume metered API usage.

Provider mappings are in the local, Git-ignored `config/jira.json`: desk 2, Get IT help 9, General intake 18, Support team `customfield_10056`. Team/priority IDs were discovered from Jira and tested with a real synthetic request.

## Run

```sh
npm run live
```

Open http://127.0.0.1:3000. This builds the static React interface and starts the Python API server and background worker together. Install Node 22 and uv, then run `npm ci` and `uv sync --frozen` first. Stop another Relay server on port 3000 before running it. Ctrl-C stops the processes. PostgreSQL must be running on the existing local port 55432.

After pulling this release, apply the additive migrations to an existing local live database before
running `npm run live`: `relay.local` does not migrate on startup.

```sh
uv run python -c "import asyncio; from relay import db; asyncio.run(db.migrate())"
```

Use `npm run setup` for the demo database instead. Migrations `003_agent_traces.sql` (agent traces)
and `004_intake_images.sql` (chat screenshots) are idempotent and safe to re-run; neither touches
existing records.

Live data uses the separate `relay_sandbox` database. It contains the 20 synthetic articles and 100 synthetic historical cases from the original demo, plus operator `saksham`. These are demonstration data, not real Jira history or reviewed company procedures. The original `relay` demo database is preserved. No synthetic incident advisories were copied into the live database.

To obtain a new eight-hour login token, run:

```sh
npm run session -- saksham
```

Paste that token into Relay's sign-in form. It is a credential; do not commit or share it.

The Python runtime does not use the former Node DNS option. Verify the Python OpenAI and Jira adapters without creating a ticket:

```sh
npm run preflight
```

The Jira smoke test created **HELP-1**, confirmed Network routing and Medium priority, and retained the test ticket. The live browser-to-worker test created **HELP-2**, confirmed Identity & Access routing, and appeared in both Relay and the matching Jira queue. All six team queue filters were verified through Jira. OpenAI structured-output and 256-dimensional embedding preflight passed; all 120 synthetic sources are indexed.

Setup testing exposed an overly broad model security extraction and a transient network validation failure. The extraction instructions now distinguish routine password changes from threats. Network validation errors now retry, while uncertain creates remain in reconciliation. The original suite had 49 passing tests; the Python migration now has 78 passing tests. The initial false-positive synthetic conversation remains in local operator review as a record of the test; it created no external ticket. These checks do not replace representative live evaluation.

The September 5 model upgrade uses Relay's own workflow, not the Codex SDK. The versioned prompt is `relay/intake_prompt.py`, now at `relay-intake-v3`. Validated quotes populate impact, urgency, device, start time, workaround and attempted steps. An explicit support request or an already-tried approved procedure bypasses repeated troubleshooting. Jira descriptions are readable sections and generated summaries are complete sentences/phrases capped at 120 characters. Routing, priority, security restrictions and idempotent connector writes remain application-controlled. Historical tickets are preserved.

Terra account access and structured extraction were verified. The original monitor scenario and five additional live checks passed: previously attempted monitor steps without a handoff request; a new monitor issue without invented attempts; routine VPN/password change; threat evidence despite instructions to ignore it; and instructions to fabricate impact/attempts. These are focused development regressions, not a new holdout score or evidence that Terra outperforms the old model on representative data.

The upgraded authenticated HTTP-to-worker-to-Jira regression created **HELP-4**: "External monitor flickers when connected through USB-C dock". Jira read-back confirmed Endpoint, Medium, readable facts and attempted steps. Relay offered no repeated procedure; replaying the same submission returned the same report with one create operation. Routing arrives as a separate update after creation, so verification waited for both operations to succeed. HELP-3 is retained as the before-upgrade example. All 49 automated tests, TypeScript and the optimized production build passed after the implementation change.

A live local multi-arm check on September 7, 2026 sent a chat screenshot through the worker intake
path. The report reached review with a calibrated confidence value and a four-step agent trace. It was
not approved, so no Jira request was created.

Security-related reports remain blocked from external delivery; a queue named Security Review does not implement issue-security permissions.

## Hosting

The ticket review feature is live locally and on Railway. New support requests now wait for independent verification and your approval. Review/edit the real Jira fields, add files, then select **Approve and submit**. HELP-10 (local) and HELP-11 (hosted) verified exact field and attachment delivery. See TICKET_REVIEW.md for supported files and limits.

The Python migration is deployed locally and on Railway. The API, OpenAI integration, Jira connector and durable worker run Python; the React interface remains TypeScript. All 78 tests and the 120-case policy parity check pass. Cloud regression HELP-9 verified Identity & Access routing, Medium priority and duplicate protection. Existing local and cloud data and session formats were retained.

Relay is also live at https://relay-web-production-6f5f.up.railway.app on the existing Railway Hobby plan, with one web service, worker and PostgreSQL database. Spending was authorized; no plan upgrade was made. Usage beyond the workspace's included allowance and OpenAI API calls are billed separately. See DEPLOYMENT.md for release verification, cloud sign-in and the remaining GitHub authentication step. The local environment remains separate.
