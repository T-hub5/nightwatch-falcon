from sim import System, FaultConfig
from agent import Agent
from data import load_orders
from invariants import check_all, violated
from eval import truth_unhealed

def _run(faults, burst=1, **kw):
    s = System(faults, seed=1).run(load_orders(300, seed=1), burst=burst)
    return s, Agent(s, **kw).run()

def test_provider_down_not_flagged_as_missing_requests():
    s = System([FaultConfig("provider_down", 1.0, start_tick=150)], seed=1).run(load_orders(300, seed=1))
    assert "requests_accounted" not in [r.name for r in violated(check_all(s))]

def test_dispatcher_accepts_name_kwarg():
    from tools import Toolbox
    Toolbox(System()).call("restart_service", name="payment")

def test_outage_does_not_fake_verification():
    s, out = _run([FaultConfig("provider_down", 1.0, start_tick=150), FaultConfig("sync_corruption", 0.3)], fix_fail_prob=1.0)
    assert "sync_corruption" in out.escalated and out.status == "escalated"

def test_provider_down_escalates_without_retrying():
    s, out = _run([FaultConfig("provider_down", 1.0, start_tick=150)])
    assert out.escalated == ["provider_down"] and out.steps <= 8

def test_silent_drop_detected_and_fixed():
    s, out = _run([FaultConfig("silent_drop", 0.1)])
    assert out.status == "recovered" and not truth_unhealed(s, {"silent_drop"})

def test_queue_loss_repaired():
    s, out = _run([FaultConfig("queue_loss", 0.3)], burst=4)
    assert out.status == "recovered" and not truth_unhealed(s, {"queue_loss"})

def test_hung_worker_has_no_error_logs_but_breaks_liveness():
    s = System([FaultConfig("hung_worker", 1.0, start_tick=20)], seed=1).run(load_orders(300, seed=1))
    assert s.logs == [] and "worker_liveness" in [r.name for r in violated(check_all(s))]

def test_hung_worker_recovered_and_verified():
    s, out = _run([FaultConfig("hung_worker", 1.0, start_tick=20)])
    assert out.status == "recovered" and not truth_unhealed(s, {"hung_worker"})

def test_hung_worker_stuck_fix_escalates():
    s, out = _run([FaultConfig("hung_worker", 1.0, start_tick=20)], fix_fail_prob=1.0)
    assert out.status == "escalated" and "hung_worker" in out.escalated and out.steps <= 20

class FakeLLM:
    def __init__(self, reply): self.reply = reply
    def complete(self, system, prompt, **kw):
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply

def test_llm_garbage_falls_back_to_rules():
    s, out = _run([FaultConfig("silent_drop", 0.1), FaultConfig("sync_corruption", 0.2)], llm=FakeLLM("lol no"))
    assert out.status == "recovered"

def test_llm_outage_does_not_break_agent():
    s, out = _run([FaultConfig("provider_down", 1.0, start_tick=150), FaultConfig("sync_corruption", 0.2)], llm=FakeLLM(RuntimeError("api down")))
    assert set(out.escalated) == {"provider_down", "sync_corruption"} and "[verified facts]" in out.human_summary

def test_llm_cannot_bypass_guardrails():
    s, out = _run([FaultConfig("provider_down", 1.0, start_tick=150), FaultConfig("sync_corruption", 0.2)],
                  llm=FakeLLM('["provider_down", "sync_corruption"]'))
    assert "provider_down" in out.escalated and out.status == "escalated" and out.steps <= 20

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("PASS", n)