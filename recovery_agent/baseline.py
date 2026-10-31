from tools import Toolbox

def run_baseline(system, max_restarts=3):
    tb = Toolbox(system)
    for _ in range(max_restarts):
        if tb.call("get_metrics")["error_rate"] <= 0.05:
            return {"claimed": "healthy", "actions": tb.calls, "harm": tb.harm}
        tb.call("restart_service", name="payment")
    return {"claimed": "gave_up", "actions": tb.calls, "harm": tb.harm}