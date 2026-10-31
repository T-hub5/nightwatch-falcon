# Agentic Recovery of a Failing System
 
Challenge 1 submission. An agent that notices failures that raise no error, fixes them safely, proves the fix, and hands off to an engineer when it cannot.
 
## The problem
 
Some of the worst failures in online systems are silent. Orders vanish, queues drop messages and data is corrupted, while every dashboard stays green. Alert-and-restart setups miss these, or "fix" them with a restart that cannot repair the data.
 
## What it does
 
1. **Detect.** Six business-rule checks watch a simulated online shop (for example, "every paid order must be fulfilled").
2. **Fix safely.** A guarded agent loop (observe, hypothesize, act, verify, close or escalate) acts only through 11 allowlisted tools: roll back a faulty setting, replay lost orders, repair bad records, and more.
3. **Prove.** After each fix it sends fresh test orders and re-runs the checks. Nothing is called "recovered" until that passes.
4. **Hand off.** Problems it cannot fix, such as a payment provider outage, are escalated to an engineer immediately with a prioritized task list. Every action is written to an audit log.
5. **Learn and forecast.** It tries each fix strategy once per cause and keeps the best. It also warns before a queue starts losing data.
Detection and verification are rules, not a language model. An optional language model (gpt-oss-20b on Groq) can rank likely causes and write the handoff note. It cannot choose actions, and the agent works the same without it.
 
## Guardrails
 
- Only allowlisted tools can run. Anything else is blocked and logged.
- At most 30 tool calls per incident, and 2 fix attempts per cause.
- Provider outages escalate at once and are never retried.
- Inconclusive verification escalates instead of claiming success.
- Lost orders are reported as lost, never as restored.
## Results
 
Evaluated on 14 held-out scenarios that were not used during development. The code was locked by hash and the held-out run was done once.
 
| | Alert-and-restart baseline | This agent |
|---|---|---|
| Correct outcome | 0% | 100% |
| Falsely says "recovered" | 71.4% | 0% |
 
- Without the verification step, with fixes that never work: false "recovered" 71.4% vs 0% for the full agent.
- With fixes that work only some of the time: 30.0% (±14.6) vs 100%.
- Learning, on unseen tests: harmful actions fell from 33.3% to 0%, and average steps from 9.0 to 7.7.
- Forecasting: it warned before the first lost order in 14 of 14 queue-loss cases (median 30 orders ahead).
- Speed: 6 to 10 tool calls per incident (single faults 6 to 7, compound faults about 10).
Full numbers are in the `results/` folder.
 
## Run it
 
Python 3 only, standard library, no installs.
 
```
python3 app.py
```
 
Then open http://127.0.0.1:8000. Pick what breaks (or press a quick scenario) and press Run scenario.
 
The interface shows what the shop sees, what the old alert setup says, what the agent did step by step, a task guide for the engineer, the early-warning charts, what the agent has learned, the saved rules, and the audit log. It has larger-text and high-contrast options and works with a keyboard.
 
## Tests
 
22 automated tests:
 
```
python3 test_regressions.py
python3 test_learning_forecast.py
python3 test_restart_works.py
python3 test_allowlist.py
```
 
`test_allowlist.py` checks that a forbidden call (`delete_database`) is blocked.
 
## Files
 
- `sim.py`: the simulated shop and its five injectable faults
- `invariants.py`: the six checks
- `agent.py`, `tools.py`: the guarded agent and its allowlisted tools
- `learning.py`, `forecast.py`: learning and early warning
- `baseline.py`: the alert-and-restart comparison
- `eval.py`, `learn_eval.py`, `forecast_eval.py`: evaluations
- `app.py`: the web interface
- `results/`: saved evaluation results
## Limitations
 
- It runs on a simulator, not a production system. It shows the method, not production readiness.
- Only 14 held-out scenarios.
- The language model gave clearer handoffs, not better accuracy.
- Learning uses few trials and a fixed set of strategies.

## Next steps
 
Test on different logs and a staging system, add scenarios written by other people, generate the checks automatically, re-test dropped fixes, and add engineer approval for riskier fixes.
 