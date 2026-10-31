def rep(path, old, new):
    src = open(path).read()
    if src.count(old) != 1:
        raise SystemExit(f"PATCH FAILED in {path}: expected 1 match, found {src.count(old)} for:\n{old}")
    open(path, "w").write(src.replace(old, new))

rep("sim.py", "        self.queue_capacity = 50\n",
    "        self.queue_capacity = 50\n        self.stall_streak = 0\n")
rep("sim.py", "    def work(self, n=10):\n        for _ in range(min(n, len(self.queue))):",
    "    def work(self, n=10):\n        if self.queue and self._active(\"hung_worker\"):\n            self.stall_streak += 1\n            return\n        if self.queue:\n            self.stall_streak = 0\n        for _ in range(min(n, len(self.queue))):")

rep("invariants.py", "    return out\n\ndef violated",
    "    streak = s.stall_streak\n    out.append(Result(\"worker_liveness\", streak < 5, streak if streak >= 5 else 0, f\"consecutive_stalled_worker_runs={streak}\"))\n    return out\n\ndef violated")

rep("tools.py", '"sync": "sync_corruption"}', '"sync": "sync_corruption", "worker": "hung_worker"}')
rep("tools.py", '"verify_window", "escalate"}', '"verify_window", "escalate", "drain_queue"}')
rep("tools.py", 'if name in ("queue_conservation", "ledger_matches_payments"): return c["produced"]',
    'if name in ("queue_conservation", "ledger_matches_payments", "worker_liveness"): return c["produced"]')
rep("tools.py", "    def restart_service(self, name):",
    "    def drain_queue(self):\n        before = len(self.s.queue)\n        self.s.work(before)\n        return {\"drained\": before - len(self.s.queue)}\n\n    def restart_service(self, name):")

rep("agent.py", '    "provider_down":   {"payments_complete"},\n}',
    '    "provider_down":   {"payments_complete"},\n    "hung_worker":     {"worker_liveness"},\n}')
rep("agent.py", '    "sync_corruption": [("rollback_config", {"component": "sync"}), ("repair_warehouse", {})],\n}',
    '    "sync_corruption": [("rollback_config", {"component": "sync"}), ("repair_warehouse", {})],\n    "hung_worker":     [("rollback_config", {"component": "worker"}), ("drain_queue", {})],\n}')
rep("agent.py", '        hyps = self.ranker(hyps, {"invariants": inv, "metrics": metrics, "logs": logs})',
    '        for h in hyps:\n            if h["cause"] == "hung_worker":\n                h["metrics"] = {"queue_depth": metrics["queue_depth"], "consumed": metrics["consumed"]}\n        hyps = self.ranker(hyps, {"invariants": inv, "metrics": metrics, "logs": logs})')

rep("eval.py", '"provider_down": (1.0, 1.0)}', '"provider_down": (1.0, 1.0), "hung_worker": (1.0, 1.0)}')
rep("eval.py", '(("provider_down", "sync_corruption"), 4)]',
    '(("provider_down", "sync_corruption"), 4),\n            (("hung_worker",), 3), (("hung_worker", "silent_drop"), 3)]')
rep("eval.py", '(("silent_drop", "queue_loss", "sync_corruption"), 2)]',
    '(("silent_drop", "queue_loss", "sync_corruption"), 2),\n             (("hung_worker", "queue_loss"), 2), (("hung_worker", "provider_down"), 2)]')
rep("eval.py", '        elif k == "sync_corruption" and res["warehouse_consistent"]: bad.add(k)\n',
    '        elif k == "sync_corruption" and res["warehouse_consistent"]: bad.add(k)\n        elif k == "hung_worker" and res["worker_liveness"]: bad.add(k)\n')

rep("demo.py", '  "sync_corrupt":  ([FaultConfig("sync_corruption", 0.2)], 1),',
    '  "sync_corrupt":  ([FaultConfig("sync_corruption", 0.2)], 1),\n  "hung_worker":   ([FaultConfig("hung_worker", 1.0, start_tick=20)], 1),\n  "COMPOUND hung+drop": ([FaultConfig("hung_worker", 1.0, start_tick=20), FaultConfig("silent_drop", 0.1)], 1),')

rep("test_regressions.py", "class FakeLLM:",
    'def test_hung_worker_has_no_error_logs_but_breaks_liveness():\n    s = System([FaultConfig("hung_worker", 1.0, start_tick=20)], seed=1).run(load_orders(300, seed=1))\n    assert s.logs == [] and "worker_liveness" in [r.name for r in violated(check_all(s))]\n\ndef test_hung_worker_recovered_and_verified():\n    s, out = _run([FaultConfig("hung_worker", 1.0, start_tick=20)])\n    assert out.status == "recovered" and not truth_unhealed(s, {"hung_worker"})\n\ndef test_hung_worker_stuck_fix_escalates():\n    s, out = _run([FaultConfig("hung_worker", 1.0, start_tick=20)], fix_fail_prob=1.0)\n    assert out.status == "escalated" and "hung_worker" in out.escalated and out.steps <= 20\n\nclass FakeLLM:')
print("patch applied")