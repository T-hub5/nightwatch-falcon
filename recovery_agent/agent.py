import json
from dataclasses import dataclass, field
from tools import Toolbox
from llm import make_llm_ranker, summarize

SIGNATURES = {
    "silent_drop":     {"requests_accounted"},
    "queue_loss":      {"queue_conservation", "ledger_matches_payments"},
    "sync_corruption": {"warehouse_consistent"},
    "provider_down":   {"payments_complete"},
    "hung_worker":     {"worker_liveness"},
}
RUNBOOK = {
    "silent_drop":     [("rollback_config", {"component": "api"})],
    "queue_loss":      [("rollback_config", {"component": "queue"}), ("replay_messages", {})],
    "sync_corruption": [("rollback_config", {"component": "sync"}), ("repair_warehouse", {})],
    "hung_worker":     [("rollback_config", {"component": "worker"}), ("drain_queue", {})],
}
MAX_STEPS, MAX_FIX_ATTEMPTS, MIN_CONFIDENCE = 30, 2, 0.99

@dataclass
class Outcome:
    status: str
    diagnosed: list = field(default_factory=list)
    fixed: list = field(default_factory=list)
    escalated: list = field(default_factory=list)
    residual_loss: int = 0
    steps: int = 0
    trace: list = field(default_factory=list)
    summary: str = ""
    human_summary: str = ""

def default_ranker(hyps, evidence):
    return sorted(hyps, key=lambda h: -h["score"])

class Agent:
    def __init__(self, system, ranker=default_ranker, use_invariants=True, use_verification=True,
                 fix_fail_prob=0.0, seed=0, llm=None):
        self.tb = Toolbox(system, fix_fail_prob, seed)
        self.llm, self.stats = llm, {"llm_ok": 0, "fallback": 0}
        if llm: ranker = make_llm_ranker(llm, self.stats)
        self.ranker, self.use_inv, self.use_ver = ranker, use_invariants, use_verification

    def _step(self, trace, thought, tool, args, obs, decision):
        trace.append({"step": len(trace) + 1, "thought": thought, "tool": tool, "args": args,
                      "observation": obs, "decision": decision})
        return obs

    def _call(self, trace, thought, tool, decision="", **args):
        obs = self.tb.call(tool, **args)
        return self._step(trace, thought, tool, args, obs, decision)

    def run(self):
        tb, trace, out = self.tb, [], Outcome("recovered")
        inv = self._call(trace, "Observe: check conservation invariants (catches silent failures)", "run_invariants")
        metrics = self._call(trace, "Observe: collect metrics", "get_metrics")
        logs = self._call(trace, "Observe: scan recent error logs", "get_logs")
        if not self.use_inv:
            inv = {k: (v if k == "payments_complete" else {**v, "ok": True, "violations": 0}) for k, v in inv.items()}
        violated = {k for k, v in inv.items() if not v["ok"]}
        if not violated:
            out.summary = "No invariant violated; system healthy."
            out.trace, out.steps = trace, tb.calls
            return out

        hyps = []
        for cause, sig in SIGNATURES.items():
            score = len(sig & violated) / len(sig)
            if score > 0:
                hyps.append({"cause": cause, "score": score, "evidence": sorted(sig & violated)})
        unexplained = violated - set().union(*[SIGNATURES[h["cause"]] for h in hyps]) if hyps else violated
        for h in hyps:
            if h["cause"] == "hung_worker":
                h["metrics"] = {"queue_depth": metrics["queue_depth"], "consumed": metrics["consumed"]}
        hyps = self.ranker(hyps, {"invariants": inv, "metrics": metrics, "logs": logs})
        out.diagnosed = [h["cause"] for h in hyps if h["score"] >= MIN_CONFIDENCE]
        self._step(trace, "Hypothesize from invariant signatures", "rank", {}, hyps, "act on high-confidence causes first")

        for h in hyps:
            cause = h["cause"]
            if tb.calls >= MAX_STEPS:
                out.escalated.append(cause); tb.call("escalate", reason="step_budget", summary=f"{cause} unresolved"); break
            if h["score"] < MIN_CONFIDENCE:
                tb.call("escalate", reason="low_confidence", summary=f"{cause} partial match {h['evidence']}")
                out.escalated.append(cause)
                self._step(trace, f"{cause}: only partial signature match", "escalate", {}, {"score": h["score"]}, "escalate: low confidence")
                continue
            if cause == "provider_down":
                prov = self._call(trace, "Logs show provider timeouts; test whether the dependency is healthy", "check_provider")
                if not prov["healthy"]:
                    tb.call("escalate", reason="external_dependency",
                            summary="Payment provider is down; no safe local fix. Orders stuck in pending_payment.")
                    out.escalated.append(cause)
                    self._step(trace, "External dependency; retrying/restarting cannot help", "escalate", {}, {}, "escalate immediately (no retry loop)")
                    continue
            fixed, fail_reason = False, "fix_failed_twice"
            for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
                for tool, args in RUNBOOK.get(cause, []):
                    self._call(trace, f"Remediate {cause} (attempt {attempt})", tool, **args)
                if not self.use_ver:
                    fixed = True; break
                ver = self._call(trace, f"Verify {cause}: fresh traffic, count NEW invariant violations",
                                 "verify_window", names=sorted(SIGNATURES[cause]))
                if ver["stable"]:
                    fixed = True; break
                if ver["inconclusive"]:
                    fail_reason = "verification_inconclusive"; break
            if fixed:
                out.fixed.append(cause)
                self._step(trace, f"{cause} verified stable", "decide", {}, {}, "close cause")
            else:
                tb.call("escalate", reason=fail_reason, summary=f"{cause}: remediation applied but not verified ({fail_reason})")
                out.escalated.append(cause)
                self._step(trace, f"{cause} not verified: {fail_reason}", "escalate", {}, {}, "escalate instead of retrying")

        if unexplained:
            tb.call("escalate", reason="unexplained_invariant", summary=str(sorted(unexplained)))
            out.escalated.append("unexplained:" + ",".join(sorted(unexplained)))
        final = tb.call("run_invariants")
        out.residual_loss = max(final["requests_accounted"]["violations"], 0)
        out.status = "escalated" if out.escalated else "recovered"
        out.steps, out.trace = tb.calls, trace
        out.human_summary = summarize(out, tb.escalations, self.llm) if out.escalated else ""
        out.summary = (f"fixed={out.fixed} escalated={out.escalated} residual_unrecoverable_orders={out.residual_loss}")
        return out

    def save_trace(self, out, path):
        json.dump({"status": out.status, "summary": out.summary, "trace": out.trace}, open(path, "w"), indent=2, default=str)