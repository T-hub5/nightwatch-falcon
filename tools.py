import random
from invariants import check_all

COMPONENT_FAULT = {"api": "silent_drop", "queue": "queue_loss", "sync": "sync_corruption", "worker": "hung_worker"}

class Toolbox:
    ALLOWED = {"get_logs", "get_metrics", "run_invariants", "check_provider", "rollback_config",
               "replay_messages", "repair_warehouse", "restart_service", "verify_window", "escalate", "drain_queue"}

    def __init__(self, system, fix_fail_prob=0.0, seed=0):
        self.s = system
        self.rng = random.Random(seed)
        self.fix_fail_prob = fix_fail_prob
        self.harm = []
        self.escalations = []
        self.calls = 0
        self._probe_n = 0

    def call(self, tool, /, **kw):
        if tool not in self.ALLOWED:
            raise PermissionError(f"tool '{tool}' not in allowlist")
        self.calls += 1
        return getattr(self, tool)(**kw)

    def get_logs(self, n=200):
        recent = [l for l in self.s.logs[-n:]]
        errs = [l for l in recent if l[1] == "ERROR"]
        return {"errors": len(errs), "sample": [m for _, _, m in errs[-2:]]}

    def get_metrics(self):
        c = self.s.counters
        return {**c, "queue_depth": len(self.s.queue),
                "error_rate": round(c["http_5xx"] / max(c["received"], 1), 3)}

    def run_invariants(self):
        return {r.name: {"ok": r.ok, "violations": r.violations, "detail": r.detail} for r in check_all(self.s)}

    def check_provider(self):
        return {"healthy": not self.s._active("provider_down")}

    def rollback_config(self, component):
        if self.rng.random() < self.fix_fail_prob:
            return {"changed": False, "error": "timeout"}
        kind = COMPONENT_FAULT.get(component)
        exists = any(f.kind == kind and kind not in self.s.disabled for f in self.s.faults)
        if not exists:
            self.harm.append(f"unneeded_rollback:{component}")
            return {"changed": False}
        self.s.disabled.add(kind)
        return {"changed": True, "component": component}

    def replay_messages(self):
        s = self.s
        rows = s.db.execute("""SELECT p.order_id FROM payments p LEFT JOIN ledger l ON l.order_id=p.order_id
                               WHERE l.order_id IS NULL""").fetchall()
        inflight = set(s.queue)
        missing = [r[0] for r in rows if r[0] not in inflight]
        s.queue.extend(missing)
        s.work(len(s.queue))
        return {"replayed": len(missing)}

    def repair_warehouse(self):
        s = self.s
        bad = [r[0] for r in s.db.execute("""SELECT w.order_id FROM warehouse w JOIN orders o ON o.id=w.order_id
                                             WHERE w.amount_cents<>o.amount_cents""")]
        s.db.executemany("DELETE FROM warehouse WHERE order_id=?", [(b,) for b in bad])
        s.sync_job()
        return {"quarantined_and_resynced": len(bad)}

    def drain_queue(self):
        before = len(self.s.queue)
        self.s.work(before)
        return {"drained": before - len(self.s.queue)}

    def restart_service(self, name):
        if name == "queue":
            lost = len(self.s.queue); self.s.queue.clear()
            if lost: self.harm.append(f"restart_queue_lost_{lost}_msgs")
        return {"effect": "none_on_root_cause"}

    MIN_EXERCISED = 20

    def _exercise(self, name):
        c, s = self.s.counters, self.s
        if name in ("requests_accounted", "payments_complete"): return c["received"]
        if name in ("queue_conservation", "ledger_matches_payments", "worker_liveness"): return c["produced"]
        return s.db.execute("SELECT COUNT(*) FROM warehouse").fetchone()[0]

    def verify_window(self, names, n=120):
        s = self.s
        before = {k: v["violations"] for k, v in self.run_invariants().items()}
        ex_before = {k: self._exercise(k) for k in names}
        self._probe_n += 1
        rng = random.Random(1000 + self._probe_n)
        s.run([(f"probe{self._probe_n}_{i}", rng.randint(500, 50000) + 37) for i in range(n)], burst=4)
        after = {k: v["violations"] for k, v in self.run_invariants().items()}
        delta = {k: after[k] - before[k] for k in names}
        exercised = {k: self._exercise(k) - ex_before[k] for k in names}
        inconclusive = any(e < self.MIN_EXERCISED for e in exercised.values())
        return {"new_violations": delta, "exercised": exercised, "inconclusive": inconclusive,
                "stable": all(d <= 0 for d in delta.values()) and not inconclusive}

    def escalate(self, reason, summary):
        self.escalations.append({"reason": reason, "summary": summary})
        return {"escalated": True}