### DEV (used for development) | code 267dfd2b41c7 | 26 variants

| system | perturbation | n | detect % | root-cause % | verified recovery % | correct outcome % | false 'recovered' % | harmful % | steps |
|---|---|---|---|---|---|---|---|---|---|
| baseline | none | 26 | 26.9 | 0.0 | 0.0 | 0.0 | 73.1 | 0.0 | 2.3 |
| no_invariants | none | 26 | 26.9 | 11.5 | 0.0 | 11.5 | 73.1 | 0.0 | 3.8 |
| no_verification | none | 26 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 6.5 |
| full_agent | none | 26 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 7.7 |
| llm_agent | none | 26 | 100.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 7.7 |
| no_invariants | flaky_fix | 130 | 26.9 | 11.5 | 0.0 | 11.5 ±0.0 | 73.1 | 0.0 | 3.8 |
| no_verification | flaky_fix | 130 | 100.0 | 100.0 | 41.7 | 48.5 ±8.9 | 42.3 | 0.0 | 6.5 |
| full_agent | flaky_fix | 130 | 100.0 | 100.0 | 60.9 | 100.0 ±0.0 | 0.0 | 0.0 | 9.5 |
| no_invariants | stuck_fix | 26 | 26.9 | 11.5 | 0.0 | 11.5 | 73.1 | 0.0 | 3.8 |
| no_verification | stuck_fix | 26 | 100.0 | 100.0 | 0.0 | 11.5 | 73.1 | 0.0 | 6.5 |
| full_agent | stuck_fix | 26 | 100.0 | 100.0 | 0.0 | 100.0 | 0.0 | 0.0 | 11.5 |
