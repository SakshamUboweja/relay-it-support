# Measured synthetic demo results

Run: 2026-09-05T04:11:25.588Z. Policy: northstar-1.0. Frozen SHA-256: b365566068c1000be23629803cd061a4f54c691a7df11a12602c45d27ee6767c.

Labels are agent-authored and **not human reviewed**. This is a regression corpus, not validated real-world model performance. The held-out set was not used to tune these results.

| Split / method | Route accuracy | Accepted precision | Eligible coverage | Security recall |
|---|---|---|---|---|
| dev / keyword | 41/60 (68.3%) | 29/48 (60.4%) | 40/40 (100.0%) | 0/5 (0.0%) |
| dev / proposed | 49/60 (81.7%) | 32/37 (86.5%) | 34/40 (85.0%) | 3/5 (60.0%) |
| heldout / keyword | 44/60 (73.3%) | 35/51 (68.6%) | 50/50 (100.0%) | 0/10 (0.0%) |
| heldout / proposed | 43/60 (71.7%) | 34/43 (79.1%) | 42/50 (84.0%) | 3/10 (30.0%) |

All failure cases and per-team counts are recorded in results.json. The live LLM baseline was skipped because no authorized live model configuration was supplied. Demo calls use zero model tokens; zero model cost is not an estimate of live costs.

Limitations: Live model performance/baseline/cost; Human-reviewed label validity; Related-incident precision/recall: text corpus lacks association labels; Fact fidelity across broad language; targeted assertions exist in tests; Real-world resolution effectiveness; Browser and provider-confirmed latency.
