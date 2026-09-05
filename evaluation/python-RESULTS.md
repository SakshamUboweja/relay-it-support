# Python migration regression results

Run: 2026-09-05T22:32:25.238508+00:00. Policy: northstar-1.0. Implementation SHA-256: c4c498321f75f7ee41087b22333cf6b99c725c4d48ce9b0b23c8526d77c682a7.

Labels are agent-authored and **not human reviewed**. Both historical split names are retained for comparison; their cases have already been observed. This is migration regression evidence, not a new untouched holdout or proof of improved real-world accuracy.

| Split / method | Route accuracy | Accepted precision | Eligible coverage | Security recall |
|---|---|---|---|---|
| dev / keyword | 41/60 (68.3%) | 29/48 (60.4%) | 40/40 (100.0%) | 0/5 (0.0%) |
| dev / proposed | 49/60 (81.7%) | 32/37 (86.5%) | 34/40 (85.0%) | 3/5 (60.0%) |
| heldout / keyword | 44/60 (73.3%) | 35/51 (68.6%) | 50/50 (100.0%) | 0/10 (0.0%) |
| heldout / proposed | 43/60 (71.7%) | 34/43 (79.1%) | 42/50 (84.0%) | 3/10 (30.0%) |

All failure cases and per-team counts are recorded in python-results.json. Historical TypeScript frozen.json and results.json remain unchanged. No live model calls occur; zero model cost is not an estimate of live costs.

Limitations: Live model performance/baseline/cost; Human-reviewed label validity; Related-incident precision/recall: text corpus lacks association labels; Fact fidelity across broad language; targeted assertions exist in tests; Real-world resolution effectiveness; Browser and provider-confirmed latency.
