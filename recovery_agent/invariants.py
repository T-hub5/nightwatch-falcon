from dataclasses import dataclass

@dataclass
class Result:
    name: str
    ok: bool
    violations: int
    detail: str

def _q(s, sql): return s.db.execute(sql).fetchone()[0]

def check_all(s, drained=False):
    out = []
    n_orders = _q(s, "SELECT COUNT(*) FROM orders")
    c = s.counters
    miss = c["received"] - n_orders
    out.append(Result("requests_accounted", miss == 0, miss, f"received={c['received']} 5xx={c['http_5xx']} orders={n_orders}"))
    lost = c["produced"] - c["consumed"] - len(s.queue)
    out.append(Result("queue_conservation", lost == 0, lost, f"produced={c['produced']} consumed={c['consumed']} in_flight={len(s.queue)}"))
    n_pay = _q(s, "SELECT COUNT(*) FROM payments")
    unpaid = _q(s, "SELECT COUNT(*) FROM orders WHERE status='pending_payment'")
    out.append(Result("payments_complete", unpaid == 0, unpaid, f"orders={n_orders} payments={n_pay} pending_payment={unpaid}"))
    gap = _q(s, "SELECT COUNT(*) FROM payments p LEFT JOIN ledger l ON l.order_id=p.order_id WHERE l.order_id IS NULL") - len(s.queue)
    out.append(Result("ledger_matches_payments", gap == 0, max(gap, 0), f"payments_without_ledger_minus_inflight={gap}"))
    bad = _q(s, "SELECT COUNT(*) FROM warehouse w JOIN orders o ON o.id=w.order_id WHERE w.amount_cents<>o.amount_cents")
    out.append(Result("warehouse_consistent", bad == 0, bad, f"mismatched_rows={bad}"))
    streak = s.stall_streak
    out.append(Result("worker_liveness", streak < 5, streak if streak >= 5 else 0, f"consecutive_stalled_worker_runs={streak}"))
    return out

def violated(results): return [r for r in results if not r.ok]