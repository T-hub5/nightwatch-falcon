### HELD-OUT (unseen fault combinations, run once) | code 267dfd2b41c7 | 14 variants

| system | perturbation | n | detect % | root-cause % | verified recovery % | correct outcome % | false 'recovered' % | harmful % | steps |
|---|---|---|---|---|---|---|---|---|---|
| baseline | none | 14 | 28.6 | 0.0 | 0.0 | 0.0 | 71.4 | 0.0 | 2.4 |
| no_invariants | none | 14 | 28.6 | 0.0 | 0.0 | 0.0 | 71.4 | 0.0 | 3.9 |
| no_verification | none | 14 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 7.8 |
| full_agent | none | 14 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 9.8 |
| llm_agent | none | 14 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 9.8 |
| no_invariants | flaky_fix | 70 | 28.6 | 0.0 | 0.0 | 0.0 ±0.0 | 71.4 | 0.0 | 3.9 |
| no_verification | flaky_fix | 70 | 100.0 | 100.0 | 30.0 | 30.0 ±14.6 | 55.7 | 0.0 | 7.8 |
| full_agent | flaky_fix | 70 | 100.0 | 100.0 | 64.3 | 100.0 ±0.0 | 0.0 | 0.0 | 12.2 |
| no_invariants | stuck_fix | 14 | 28.6 | 0.0 | 0.0 | 0.0 | 71.4 | 0.0 | 3.9 |
| no_verification | stuck_fix | 14 | 100.0 | 100.0 | 0.0 | 0.0 | 71.4 | 0.0 | 7.8 |
| full_agent | stuck_fix | 14 | 100.0 | 100.0 | 0.0 | 92.9 | 0.0 | 0.0 | 16.1 |
