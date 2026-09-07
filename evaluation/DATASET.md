# Relay evaluation and acceptance notes

## Dataset status and interpretation

`scenarios.json` contains 120 unique, synthetic first-message scenarios: 60 development and 60 held out. Each split contains 12 scenario families with five examples each. Family IDs never cross the split. The development set covers password-change VPN authentication, unreachable VPN gateways, SSO lockout, Wi-Fi joining, laptop displays, Atlas runtime errors, vague access, procurement/approval, unsolicited MFA, individual work blocking, general security information, and multiple unresolved services. The held-out set introduces established tunnel loss, SSO session loops, geographically bounded Wi-Fi symptoms, boot/power failures, Atlas record actions, HR administration, potential Atlas data exposure, security negation, reported broad critical loss, unidentified failure objects, historical red herrings, and phishing credential disclosure.

**Expected labels were authored by an AI agent and have not been human reviewed.** A human must review and freeze the labels before these results support claims about routing quality. This is a synthetic regression corpus, not a validated benchmark or evidence of real employee outcomes. Human review was unavailable during authoring. The scenario author did not inspect the application's router or routing tests.

The `team` label describes the appropriate destination, including general-intake abstention. `service: null` means no single supported service is sufficiently established or the request is outside the break/fix workflow. For multiple unresolved services, retaining multiple candidates is appropriate; the scalar label records the fallback result rather than forbidding alternatives in the decision log. `expectedClarification` means one first-turn question could materially change the destination or troubleshooting; explicit support submission must still bypass it. Do not interpret `false` as permission to assert that all optional facts are known.

`escalation` is an action category, not a factual claim that an incident is confirmed. `security` means suspected compromise or exposure and restricted review, `urgent` means expedited review of reported broad loss of the fixture's critical Atlas service, and `elevated` means individual work is explicitly blocked without a usable workaround. `none` does not assert low impact; unknown impact/urgency must remain unknown with the configured provisional priority. In particular, urgent Atlas examples are user reports, not authoritative outage confirmations.

All six destination labels match the brief exactly. Service IDs are `vpn`, `sso`, `wifi`, `laptop`, `atlas`, and `null`. Family separation prevents exact-family paraphrase leakage but does not establish statistical independence: language and service categories necessarily overlap. The corpus has deliberate challenge-case balance and does not estimate production case prevalence.

## Arm comparison

The four intake arms — `rules-v1`, `rules-v2`, `single`, `multi` — were run over this corpus with
`npm run eval -- --arm all --split all --effort medium`. The measured table, the per-case failures and
the caveats are in [ARMS-RESULTS.md](ARMS-RESULTS.md); the design behind the arms is in
[../MULTI_AGENT_PLAN.md](../MULTI_AGENT_PLAN.md).

Confidence calibration is fitted on the **dev split only**, after the restricted-confidence change,
and the published numbers come from a run made under the shipped defaults (routing scoring v2,
`RELAY_PIPELINE=single`). The dev-split ECE of 0.000 is therefore **in-sample** and is not evidence of
calibration quality; only the heldout ECE is out-of-fit, and even that split was already inspected
during development, so it is a holdout-informed regression comparison rather than a clean holdout
claim. `--arm rules-v1` on its own runs the legacy deterministic runner, which stays pinned to routing
scoring v1 so `python-RESULTS.md` keeps its original label.

## Run discipline and reporting

1. Freeze configuration, thresholds, catalog, prompts, model version and baseline implementations using development data only. Keep held-out messages and labels out of source retrieval, seed cases, prompts and decision code.
2. Feed the same text and available fixture context to each evaluated method. Specify whether each baseline has catalog, retrieved material, simulated directory context, and outage context. These JSON scenarios themselves provide only text; tests requiring visibility or authoritative outage state need separate explicit fixtures.
3. Keyword/catalog, LLM-only and retrieval-plus-policy are three distinct methods. A deterministic mock cannot stand in for an LLM baseline. Mark unavailable live model comparisons skipped and disclose the reason. Do not generate plausible values for a missing run.
4. Report actual numerator/denominator counts for route accuracy overall and by team; accepted-route precision and coverage; escalation recall and false positives, with security separate; and first-turn clarification agreement. Treat a predicted `Service Desk` fallback on a labeled supported case as an abstention and a routing miss while reporting those counts separately.
5. Specify the eligible denominator for coverage before the run. For this corpus, use cases with a non-Service Desk expected destination; additionally report all-case automatic-routing frequency to make exclusions transparent.
6. Display every failed example with its ID, expected output, actual output, decision reason and version. Do not silently exclude ambiguous or unsupported cases from overall accuracy.
7. Record timestamp, mode, source snapshot version, model/prompt version and runtime. If only mock runs occurred, explicitly state that latency is local demo latency and token/cost metrics are not live-model measurements.
8. After examining held-out failures, any subsequent change is development informed by the holdout. Use a fresh independently authored holdout for a new performance claim.

The 120 text-only labels do not grade evidence provenance, unauthorized retrieval, temporal leakage, source injection resistance, true related-incident matches, retry safety, confirmation semantics, or end-to-end persistence. Passing this corpus alone is not functional acceptance. Those requirements need the stateful checks below.

## Required stateful acceptance checks, ordered by risk

### P0 — preserve truthful reports and control external effects

- Persist the original report before any provider write. Repeated submission with the same stable key, duplicate job delivery and process restart must retain one operation and at most one provider request in the tested cases. A second distinct user report must remain a separate report.
- Simulate provider acceptance followed by a lost response. The operation becomes `unknown`; reconciliation finds the existing correlation marker without another create. Empty search immediately after timeout must not authorize retry; multiple matches require review.
- Fail the routing update after a successful create. Retain the real provider key and retry only the update. Never transition back to creation.
- Mark live mode with missing credentials or invalid provider mappings as unavailable/pending with actionable errors. No demo fallback and no invented Jira keys in live mode.
- Assert that unknown impact, device type, affected-user count and urgency remain null unless supported. For password-change VPN input, cached credentials are a hypothesis; reconnecting, successful web login and troubleshooting completion must not be invented.
- Offer an approved procedure, then provide no reply: status remains awaiting response. Only explicit `Fixed it` or verified external resolution logs resolution. `Still broken` advances to support after one procedure. `Send to support` immediately bypasses both procedure and clarification.
- Feed one vague message, then another vague response. At most one clarification turn precedes fallback support or operator review. Fully specified ordinary and urgent cases do not require extra questions for optional diagnostics.
- Restrict security reports server-side. Requesters cannot fetch another user's report, operator endpoint, private source or private incident link by guessing IDs. If provider permissions cannot protect the destination, preserve an operator-only local report and show external handoff blocked.
- Test positive suspicion, informational requests, negation and conflicting evidence. Suspicion bypasses generic troubleshooting; informational requests and explicit negation do not cause an active-compromise classification solely due to security keywords.

### P1 — prove policy, permissions and provider state

- Related-incident fixtures must cover matching open service/location/time, closed incidents, another location, private visibility, missing discriminator and superficially similar symptoms. Filter permissions before ranking and again before display. No automatic merge, close, participant addition or access expansion. In-app follows remain individual saved reports and are labeled as in-app follows.
- Authoritative critical outage and broad user-reported outage each trigger their configured action, while retaining different evidence origins and confirmation status. A related outage cannot erase a user's reported authentication or security evidence.
- Set historical cases and incident fixture timestamps explicitly. A case finalized after the evaluated request cannot become supporting truth; an incident published afterward cannot be visible at request time.
- Inject source text instructing the model to disclose secrets, override team policy or call an arbitrary URL. Validate that no unauthorized effect or source disclosure occurs and that evidence IDs remain in the allowed set.
- Correct an existing request's team/priority in the operator screen. Show pending until provider confirmation. Poll provider reassignment and resolution into the local mirror without automatically reverting them.
- Simulate provider 429 with Retry-After, revoked credentials, invalid fields, read failures, failed safe updates and delayed indexing. Show distinct actionable recovery states with bounded retries.
- Advance the review timer past its configured window. Persist one escalation event across restarts; stop after acknowledgement or resolution.
- Validate request-field discovery, truthful defaults, fallback request type, reporter permissions and support-team options against provider stubs. A configured numeric option alone is not proof the real Jira destination accepts it.

### P2 — complete visible journeys and handoff

- Run browser journeys for Wi-Fi procedure then confirmed resolution, password-change VPN support handoff, permitted Atlas advisory follow, ambiguous clarification then fallback, and lost-create-response recovery.
- Check keyboard-only navigation, labels and focus, loading/empty/error states, saved-outcome status text, simulated-context disclosure, and persistence after reload.
- Confirm My Requests shows only authorized own reports and distinguishes local draft, awaiting response, submission pending, related report, created request and resolved interaction.
- Confirm the operator console exposes evidence/source inspection, alternatives, unknowns, correction/acknowledgement, connector recovery and integration health.
- Execute an opt-in live Jira smoke test only against the configured authorized synthetic test project. Verify the returned request and mapped support-team field. Without credentials/mappings/session access, report this check as pending external setup, not passed.
- Publish measured results, tested failure scenarios, implementation deviations, remaining checks and precise startup/setup commands in the handoff. App completion, live Jira verification and live model evaluation must be reported separately.

## Limits of this initial suite

The JSON corpus does not include authoritative incident or requester visibility fields and therefore cannot honestly yield related-incident precision/recall. It also lacks follow-up messages, so clarification-turn caps and resolution rates require separate scripted conversations. Do not infer ticket validity, fact fidelity, real-world resolution rate or provider-confirmed latency from route labels. Scripted `Fixed it` responses demonstrate state handling only. Non-English text, spelling variation, accessibility technologies, adversarial inputs and real enterprise distributions require additional coverage.
