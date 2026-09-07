# Multi-agent intake: design record

This is what shipped, not a proposal. Relay's intake stage is one of three selectable arms behind
`RELAY_PIPELINE`. Every arm produces the same evidence packet and hands it to the same deterministic
policy; the arms differ only in how many model roles look at the message first. Ticket verification
before approval is a separate role and is documented in [TICKET_REVIEW.md](TICKET_REVIEW.md).

## The three arms

| Arm | `RELAY_PIPELINE` | Model roles | Budget (calls / tools / tokens / seconds) |
| --- | --- | --- | --- |
| Deterministic | `deterministic` | Intake extraction only | 2 / 0 / 20000 / 75 |
| Single | `single` | One role that extracts and proposes a route | 2 / 0 / 25000 / 75 |
| Multi | `multi` | Triage with read-only tools, then an independent reviewer | 8 / 4 / 60000 / 150 |

Budgets live in `config/policy.json` under `agentBudget`. A run that exceeds a budget stops and is
recorded as `budget_exhausted`; the deterministic decision still stands — the extraction's validated
facts apply, but neither the proposal nor its review is applied, and both are shown as evidence
only. Prompt versions are `relay-intake-v3` (extraction), `relay-single-v2`, `relay-triage-v1` and
`relay-reviewer-v1`.

The multi arm's triage role can call three read-only tools — `lookup_catalog`, `similar_cases` and
`search_knowledge` — over approved sources only. The reviewer has no tools and no write path. In
the measured run below the triage role made one tool call across 120 cases (`lookup_catalog` on
`heldout-029`) and no proposal cited a source, so its routing was not tool-grounded; making the
first triage turn require a tool call is a follow-up, not shipped.

## What a model may change

`apply_proposal` in `relay/intake_evidence.py` is the only place a model result touches a routing
decision, and its lanes are narrow:

- **Tie-break.** If the deterministic scorer did not accept a route, the proposal may select a
  service named in the message or in screenshot text (`imageText`), which is data, not a quote —
  one whose catalog aliases appear in that text, per `catalog_candidates` — provided its team
  matches the catalog's team for that service and the proposal clears `proposalMinConfidence`.
  Recorded as `model-tie-break`.
- **Abstain.** A proposal may withdraw an accepted non-security route back to Service Desk
  (`model-abstain`).
- **Cite a blocked-work quote.** A validated quote from the message raises priority and escalation to
  `elevated` (`model-cited-work-blocked`).
- **Cite a broad-impact quote.** A validated quote records impact, and raises a critical service to
  `urgent` (`user-reported-broad-critical-loss`).
- **Cite a security quote.** A validated quote restricts the report
  (`model-extracted-security-evidence`).

A model never writes a priority, escalation or visibility string of its own, never selects Security
Review as a destination, and is ignored entirely on a report the rules already restricted
(`proposal-ignored-restricted`). The reviewer is narrower still: it may request human review or
restrict on a validated security quote, and nothing else. Authorization, security gates, Jira field
validation and idempotent writes stay in deterministic code.

## Confidence

`relay/agents/confidence.py` scores five signals in [0, 1] and takes a weighted mean over the ones
that apply, so a missing signal does not drag the score down. Weights are in `config/confidence.json`:
agent probability 0.35, triage/reviewer agreement 0.25, retrieval support from reviewed similar cases
0.20, deterministic margin 0.15, evidence fidelity 0.05. On a security-restricted report the agent and
agreement signals are dropped, because the rules decided that route without them.

The raw score is then calibrated by piecewise-linear interpolation over isotonic breakpoints in
`config/calibration.json`, fitted per arm. **Calibration was fitted after the restricted-confidence
change, on the dev split only**, and the published numbers below come from a run made with routing
scoring v2, the shipped default. Bands are high ≥ 0.80 and medium ≥ 0.50. Each
signal carries a plain-language label, and `why()` joins them into the sentence the requester sees
under "Why this team?".

## Traces

Migration `003_agent_traces.sql` adds `agent_runs` and `agent_steps`. A run records the arm, scoring
version, model, reasoning effort, status, budget, token usage, estimated cost, pricing version,
latency and outcome. A step records its role, kind (`model_call`, `tool_call`, `policy`), prompt
version, bounded input/output summaries, tool name and arguments, usage, cost, latency and status.
Operators see this as a per-run timeline in the decision record. Cost estimates come from
`config/pricing.json` (OpenRouter listing, 2026-09-06); `OPENAI_PRICE_*` overrides the configured
model's rates, and a missing price yields no cost rather than a false zero.

## Screenshot intake

`POST /api/intake` accepts multipart with at most one PNG or JPEG up to 5 MB. Migration
`004_intake_images.sql` adds `origin` and `message_id` to `report_attachments`. The screenshot is
shown to the intake role only — never to the verifier — and is staged as an ordinary attachment for
approval. If Jira's request type does not accept attachments, the staged screenshot is deleted while
preparing the review and the requester is told it informed intake but will not be sent.
`RELAY_IMAGE_DETAIL` sets the detail level.

## Asynchronous intake

The multi arm makes several model calls, which is too slow for one request. In live mode
`POST /api/intake` returns `{"state": "processing"}` and the worker's `resume_intakes` runs the
pipeline and prepares the review. `RELAY_INTAKE_INLINE=1` forces inline execution for local
debugging.

## The harness

`npm run eval -- --arm all --split all --effort medium` runs every arm over the 120-case corpus.
Model results are cached under `evaluation/cache/` keyed on case, arm, effort and prompt versions, so
a resumed run does not re-spend. Calibration is fitted on the dev split only and refuses any other
split. `--arm rules-v1` with no other flags runs the legacy deterministic runner instead, which is
pinned to scoring v1 so its published baseline keeps its label. Results are written to
`evaluation/arms-results.json` and `evaluation/ARMS-RESULTS.md`, and served to operators by
`GET /api/evaluation`.

## Measured comparison

Run 2026-09-07, `gpt-5.6-terra` at reasoning effort medium, scoring v2 for the model arms.

The harness arms `rules-v1` and `rules-v2` run `decide()` on the raw message with no model call at
all. They are not the shipped `RELAY_PIPELINE=deterministic` pipeline, which makes one extraction call
before the same rules and has no row here; the rules rows are that pipeline's routing floor, what the
policy achieves on unextracted text.

| Arm | Split | Route acc | Accepted prec | Coverage | Security recall | Escalation recall | ECE | Brier | AUROC | p50 / p95 ms | Tokens in/out | Est. cost | Tool calls / cited |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules-v1 | dev | 49/60 (81.7%) | 32/37 (86.5%) | 34/40 (85.0%) | 3/5 (60.0%) | 7/10 (70.0%) | 0.000 | 0.142 | 0.661 | 0 / 0 | 0/0 | $0.0000 | 0 / 0 |
| rules-v1 | heldout | 43/60 (71.7%) | 34/43 (79.1%) | 42/50 (84.0%) | 3/10 (30.0%) | 6/20 (30.0%) | 0.123 | 0.211 | 0.608 | 0 / 0 | 0/0 | $0.0000 | 0 / 0 |
| rules-v2 | dev | 52/60 (86.7%) | 35/40 (87.5%) | 37/40 (92.5%) | 3/5 (60.0%) | 7/10 (70.0%) | 0.000 | 0.113 | 0.615 | 0 / 0 | 0/0 | $0.0000 | 0 / 0 |
| rules-v2 | heldout | 43/60 (71.7%) | 34/43 (79.1%) | 42/50 (84.0%) | 3/10 (30.0%) | 6/20 (30.0%) | 0.163 | 0.226 | 0.567 | 0 / 0 | 0/0 | $0.0000 | 0 / 0 |
| single | dev | 59/60 (98.3%) | 40/41 (97.6%) | 40/40 (100.0%) | 5/5 (100.0%) | 10/10 (100.0%) | 0.000 | 0.016 | 0.822 | 2450 / 4250 | 101780/11471 | $0.1644 | 0 / 28 |
| single | heldout | 59/60 (98.3%) | 49/50 (98.0%) | 50/50 (100.0%) | 10/10 (100.0%) | 19/20 (95.0%) | 0.002 | 0.016 | 0.839 | 2421 / 3854 | 101948/11163 | $0.1608 | 0 / 31 |
| multi | dev | 59/60 (98.3%) | 40/41 (97.6%) | 40/40 (100.0%) | 5/5 (100.0%) | 10/10 (100.0%) | 0.000 | 0.016 | 0.822 | 5371 / 7922 | 177847/15537 | $0.3796 | 0 / 0 |
| multi | heldout | 58/60 (96.7%) | 48/50 (96.0%) | 50/50 (100.0%) | 10/10 (100.0%) | 20/20 (100.0%) | 0.017 | 0.031 | 0.828 | 5488 / 7439 | 178542/15549 | $0.3929 | 1 / 0 |

Caveats:

1. Labels are agent-authored and not human reviewed.
2. The heldout split was already inspected during development (its failures are in python-results.json); this is a holdout-informed regression comparison, not a clean holdout claim.
3. Costs are estimates from config/pricing.json (version 2026-09-06-openrouter), not billing records.
4. Model arms ran at reasoning effort medium; production uses high.
5. The single arm saw the first eight seeded sources by id (no retrieval on this text-only corpus).
6. The multi arm made 1 tool call and cited sources in 0 of 120 cases; its routing was not tool-grounded in this run.

Held-out misses: single failed `heldout-037` (a routine password/MFA report restricted as Security
Review) and `heldout-017` (elevated priority not recognised); multi failed `heldout-037` and
`heldout-055`, both false security restrictions. Multi reviewer verdicts on held-out were 40 accept,
20 human_review, 0 revise, with no budget exhaustion. Fidelity was the same for both model arms:
quote validity 100%, invented attempts 0%, unknowns preserved 84%, summary bounded 100%. Total live
spend across all runs was about $3. Dev-split ECE of 0.000 is in-sample, because calibration was
fitted on dev.

## Decision

`RELAY_PIPELINE=multi` and scoring v2 are the live defaults. The multi arm is what Relay is: intake,
a triage agent with read-only tools, and an independent reviewer that can send a proposal back once
or hand it to a human. It matched the single arm on security recall (10/10), took every escalation
(20/20), and routed 58 of 60 held-out cases against the single arm's 59.

That accuracy parity is bought with latency and spend — about 2.3× the p50 and 2.4× the cost per
request — which the async worker path absorbs, and the reviewer verdict and the tool timeline are
what operators read on the decision record. The `single` arm stays a one-variable switch for anyone
who wants the cheaper route, as does `deterministic`. One caveat travels with the multi arm: in the
published run it made one `lookup_catalog` call (on `heldout-029`) and cited no sources across its
120 cases, so its routing was not tool-grounded. Requiring a tool call on the first triage turn is a
follow-up, not shipped.

The two rules arms are not pipelines: they exist only as harness `--arm` values, kept as no-model
baselines.

The original HELP-7 regression — a laptop reporting Wi-Fi failure tying Network against Endpoint and
falling back to Service Desk — is resolved. Scoring v2 routes it to Network, and so do both model
arms. The cheaper deterministic fix was worth making before adding model calls, as this plan
originally required.
