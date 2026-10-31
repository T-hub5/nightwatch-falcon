import json, os, re
import agent as agent_mod
from agent import Agent, RUNBOOK
from tools import COMPONENT_FAULT

CAUSE_COMPONENT = {fault: comp for comp, fault in COMPONENT_FAULT.items()}
STRATEGY_ORDER = ["generic", "targeted"]
STRATEGIES = {
    cause: {
        "generic": [("restart_service", {"name": CAUSE_COMPONENT[cause]})] + steps,
        "targeted": steps,
        "restart_only": [("restart_service", {"name": CAUSE_COMPONENT[cause]})],
    }
    for cause, steps in RUNBOOK.items()
}

def utility(s):
    n = s["n"]
    return s["ok"] / n - 0.02 * s["steps"] / n - 0.5 * s["harm"] / n

class IncidentMemory:
    def __init__(self, path=None, order=None):
        self.path, self.stats, self.log = path, {}, []
        self.order = list(order or STRATEGY_ORDER)
        if path and os.path.exists(path):
            data = json.load(open(path))
            self.stats, self.log = data["stats"], data["log"]

    def record(self, cause, strategy, ok, steps, harm):
        s = self.stats.setdefault(cause, {}).setdefault(strategy, {"n": 0, "ok": 0, "steps": 0, "harm": 0})
        s["n"] += 1
        s["ok"] += int(ok)
        s["steps"] += steps
        s["harm"] += harm
        self.log.append({"cause": cause, "strategy": strategy, "ok": ok, "steps": steps, "harm": harm})

    def choose(self, cause):
        tried = self.stats.get(cause, {})
        for name in self.order:
            if name not in tried:
                return name, "explore"
        return max(tried, key=lambda k: utility(tried[k])), "exploit"

    def lessons(self):
        out = []
        for cause, tried in sorted(self.stats.items()):
            if len(tried) < len(self.order):
                continue
            best = max(tried, key=lambda k: utility(tried[k]))
            b = tried[best]
            out.append(f"{cause}: use '{best}' ({b['ok']}/{b['n']} fixed, {b['steps'] / b['n']:.1f} steps, {b['harm']} harmful actions)")
        return out

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            json.dump({"stats": self.stats, "log": self.log}, open(self.path, "w"), indent=2)

class LearningAgent(Agent):
    def __init__(self, system, memory, learn=True, force_strategy=None, **kw):
        super().__init__(system, **kw)
        self.memory, self.learn, self.force = memory, learn, force_strategy
        self.cost = {}

    def _call(self, trace, thought, tool, decision="", **args):
        before = len(self.tb.harm)
        obs = super()._call(trace, thought, tool, decision, **args)
        m = re.match(r"(?:Remediate|Verify) (\w+)", thought)
        if m:
            c = self.cost.setdefault(m.group(1), {"steps": 0, "harm": 0})
            c["steps"] += 1
            c["harm"] += len(self.tb.harm) - before
        return obs

    def run(self):
        plan = {c: ((self.force, "forced") if self.force else self.memory.choose(c)) for c in RUNBOOK}
        original = dict(agent_mod.RUNBOOK)
        agent_mod.RUNBOOK.update({c: STRATEGIES[c][plan[c][0]] for c in plan})
        try:
            out = super().run()
        finally:
            agent_mod.RUNBOOK.clear()
            agent_mod.RUNBOOK.update(original)
        out.strategies = {c: plan[c] for c in self.cost}
        if self.learn:
            for c, cost in self.cost.items():
                self.memory.record(c, plan[c][0], c in out.fixed, cost["steps"], cost["harm"])
            self.memory.save()
        return out