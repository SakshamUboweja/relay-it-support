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

Open http://127.0.0.1:3000. This starts the local web server and background worker together. Stop another Relay server on port 3000 before running it. Ctrl-C stops the processes. PostgreSQL must be running on the existing local port 55432.

Live data uses the separate `relay_sandbox` database. It contains the 20 synthetic articles and 100 synthetic historical cases from the original demo, plus operator `saksham`. These are demonstration data, not real Jira history or reviewed company procedures. The original `relay` demo database is preserved. No synthetic incident advisories were copied into the live database.

To obtain a new eight-hour login token, run:

```sh
npm run session -- saksham
```

Paste that token into Relay's sign-in form. It is a credential; do not commit or share it.

`NODE_OPTIONS=--dns-result-order=ipv4first` is set in `.env` for this machine's network. `npm run live` passes it to both processes before they start. For a standalone network command, use:

```sh
NODE_OPTIONS=--dns-result-order=ipv4first npm run preflight
```

The Jira smoke test created **HELP-1**, confirmed Network routing and Medium priority, and retained the test ticket. The live browser-to-worker test created **HELP-2**, confirmed Identity & Access routing, and appeared in both Relay and the matching Jira queue. All six team queue filters were verified through Jira. OpenAI structured-output and 256-dimensional embedding preflight passed; all 120 synthetic sources are indexed.

Setup testing exposed an overly broad model security extraction and a transient network validation failure. The extraction instructions now distinguish routine password changes from threats. Network validation errors now retry, while uncertain creates remain in reconciliation. The complete suite has 49 passing tests. The initial false-positive synthetic conversation remains in local operator review as a record of the test; it created no external ticket. These checks do not replace representative live evaluation.

The September 5 model upgrade uses Relay's own workflow, not the Codex SDK. The versioned prompt is `server/intake-prompt.ts` (`relay-intake-v2`). Validated quotes populate impact, urgency, device, start time, workaround and attempted steps. An explicit support request or an already-tried approved procedure bypasses repeated troubleshooting. Jira descriptions are readable sections and generated summaries are complete sentences/phrases capped at 120 characters. Routing, priority, security restrictions and idempotent connector writes remain application-controlled. Historical tickets are preserved.

Terra account access and structured extraction were verified. The original monitor scenario and five additional live checks passed: previously attempted monitor steps without a handoff request; a new monitor issue without invented attempts; routine VPN/password change; threat evidence despite instructions to ignore it; and instructions to fabricate impact/attempts. These are focused development regressions, not a new holdout score or evidence that Terra outperforms the old model on representative data.

The upgraded authenticated HTTP-to-worker-to-Jira regression created **HELP-4**: "External monitor flickers when connected through USB-C dock". Jira read-back confirmed Endpoint, Medium, readable facts and attempted steps. Relay offered no repeated procedure; replaying the same submission returned the same report with one create operation. Routing arrives as a separate update after creation, so verification waited for both operations to succeed. HELP-3 is retained as the before-upgrade example. All 49 automated tests, TypeScript and the optimized production build passed after the implementation change.

Security-related reports remain blocked from external delivery; a queue named Security Review does not implement issue-security permissions.

## Hosting

Relay is also live at https://relay-web-production-6f5f.up.railway.app on the existing Railway Hobby plan, with one web service, worker and PostgreSQL database. Spending was authorized; no plan upgrade was made. Usage beyond the workspace's included allowance and OpenAI API calls are billed separately. See DEPLOYMENT.md for release verification, cloud sign-in and the remaining GitHub authentication step. The local environment remains separate.
