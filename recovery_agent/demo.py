import json, sys
from data import load_orders
from sim import System, FaultConfig
from agent import Agent
from baseline import run_baseline
from invariants import check_all, violated

SCENARIOS = {
  "silent_drop":   ([FaultConfig("silent_drop", 0.1)], 1),
  "queue_loss":    ([FaultConfig("queue_loss", 0.3)], 4),
  "sync_corrupt":  ([FaultConfig("sync_corruption", 0.2)], 1),
  "hung_worker":   ([FaultConfig("hung_worker", 1.0, start_tick=20)], 1),
  "COMPOUND hung+drop": ([FaultConfig("hung_worker", 1.0, start_tick=20), FaultConfig("silent_drop", 0.1)], 1),
  "provider_down": ([FaultConfig("provider_down", 1.0, start_tick=150)], 1),
  "COMPOUND drop+corrupt": ([FaultConfig("silent_drop", 0.1), FaultConfig("sync_corruption", 0.2)], 1),
  "COMPOUND provider+corrupt": ([FaultConfig("provider_down", 1.0, start_tick=150), FaultConfig("sync_corruption", 0.2)], 1),
}
def build(faults, burst):
    return System(faults, seed=1).run(load_orders(300, seed=1), burst=burst)

for name, (faults, burst) in SCENARIOS.items():
    base = run_baseline(build(faults, burst))
    s = build(faults, burst)
    ag = Agent(s); out = ag.run()
    ag.save_trace(out, "trace_" + name.lower().replace(" ", "_").replace("+", "_") + ".json")
    still = [r.name for r in violated(check_all(s))]
    print(f"{name:26s} baseline={base['claimed']:8s} | agent={out.status:9s} fixed={out.fixed} escalated={out.escalated} "
          f"lost_orders={out.residual_loss} steps={out.steps} harm={len(ag.tb.harm)}")