# Arm comparison results

Run: 2026-09-07T07:23:22.928062+00:00. Model: gpt-5.6-terra. Effort: medium. Scoring: v2 for the model arms, candidates ranked with v2 (rules arms carry their own).

Prompts: intake=relay-intake-v3, single=relay-single-v2, triage=relay-triage-v1, reviewer=relay-reviewer-v1. Policy SHA-256: 8d199a4ac680e7f49e498cc8c7bfbc2ce32aa055d0d5cdb3a660e175b5845748. Calibration SHA-256: 8269ad8c78d1729845f87b7f6f617ea9da7099b466d1c3f5f9cfaffc94d27423. Pricing: 2026-09-06-openrouter. Cache hits: 240. Aborted at the spend cap: no.

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
