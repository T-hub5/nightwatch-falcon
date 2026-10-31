from sim import System, FaultConfig
from data import load_orders
from invariants import check_all
from tools import Toolbox
from learning import LearningAgent, IncidentMemory
from forecast import Forecaster, monitor
import agent as agent_mod
import os, tempfile

def _system(faults, burst=1):
    return System(faults, seed=1).run(load_orders(300, seed=1), burst=burst)

def test_memory_explores_then_prefers_cheaper_strategy():
    m = IncidentMemory()
    assert m.choose("queue_loss") == ("generic", "explore")
    m.record("queue_loss", "generic", True, 6, 1)
    assert m.choose("queue_loss") == ("targeted", "explore")
    m.record("queue_loss", "targeted", True, 3, 0)
    assert m.choose("queue_loss") == ("targeted", "exploit")

def test_memory_survives_save_and_load():
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    m = IncidentMemory(p)
    m.record("silent_drop", "generic", True, 3, 0)
    m.save()
    assert IncidentMemory(p).stats["silent_drop"]["generic"]["n"] == 1

def test_learning_agent_restores_runbook_and_records():
    before = {k: list(v) for k, v in agent_mod.RUNBOOK.items()}
    m = IncidentMemory()
    out = LearningAgent(_system([FaultConfig("silent_drop", 0.1)]), m).run()
    assert out.status == "recovered"
    assert {k: list(v) for k, v in agent_mod.RUNBOOK.items()} == before
    assert m.stats["silent_drop"]["generic"]["n"] == 1

def test_forecaster_warns_before_queue_loss():
    s = System([FaultConfig("queue_loss", 0.4)], seed=1)
    f, warned = Forecaster(), []
    snaps = monitor(s, load_orders(300, seed=1), burst=4, on_window=lambda sn: warned.extend(f.update(sn)))
    loss = next(sn["tick"] for sn in snaps if sn["viol"]["queue_conservation"] > 0)
    assert any(w["kind"] == "queue_saturation" and w["tick"] < loss for w in warned)

def test_forecast_quiet_on_balanced_queue():
    s = System(seed=1)
    f, warned = Forecaster(), []
    monitor(s, load_orders(300, seed=1), burst=1, on_window=lambda sn: warned.extend(f.update(sn)))
    assert not warned

def test_draining_on_warning_prevents_loss():
    s = System([FaultConfig("queue_loss", 0.5)], seed=1)
    f, tb = Forecaster(), Toolbox(s)
    def hook(sn):
        if any(w["kind"] == "queue_saturation" for w in f.update(sn)):
            tb.call("drain_queue")
    monitor(s, load_orders(300, seed=1), burst=4, on_window=hook)
    assert all(r.ok for r in check_all(s) if r.name in ("queue_conservation", "ledger_matches_payments"))

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)