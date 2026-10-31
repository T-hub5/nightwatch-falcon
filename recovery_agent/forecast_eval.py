import os, statistics
from data import load_orders
from sim import System, FaultConfig
from tools import Toolbox
from eval import make_variants
from invariants import check_all
from forecast import Forecaster, monitor

def fresh(spec):
    faults = [FaultConfig(k, sev, spec["seed"], start) for k, sev, start in spec["faults"]]
    return System(faults, seed=spec["seed"])

def violations(system):
    return {r.name: r.violations for r in check_all(system)}

def first_tick(snaps, pred):
    return next((s["tick"] for s in snaps if pred(s)), None)

def collect(spec, orders=None):
    system, f, warns = fresh(spec), Forecaster(), []
    def hook(snap):
        warns.extend(f.update(snap))
    snaps = monitor(system, orders or load_orders(300, seed=spec["seed"]), spec["burst"], on_window=hook)
    return system, snaps, warns

def lead_time(spec, kind, invariant):
    _, snaps, warns = collect(spec)
    loss = first_tick(snaps, lambda s: s["viol"][invariant] > 0)
    warn = next((w["tick"] for w in warns if w["kind"] == kind), None)
    if loss is None:
        return None
    return {"warned": warn is not None and warn < loss, "lead": (loss - warn) if warn is not None else None}

def healthy_alarm(burst, seed):
    spec = {"seed": seed, "burst": burst, "faults": []}
    _, _, warns = collect(spec)
    return any(w["kind"] == "queue_saturation" for w in warns)

def prevention(spec):
    system, f, drains = fresh(spec), Forecaster(), [0]
    tb = Toolbox(system)
    def hook(snap):
        if any(w["kind"] == "queue_saturation" for w in f.update(snap)):
            tb.call("drain_queue")
            drains[0] += 1
    monitor(system, load_orders(300, seed=spec["seed"]), spec["burst"], on_window=hook)
    v = violations(system)
    return v["queue_conservation"] + v["ledger_matches_payments"], drains[0]

def reactive_loss(spec):
    system, _, _ = collect(spec)
    v = violations(system)
    return v["queue_conservation"] + v["ledger_matches_payments"]

def projection(spec, invariant):
    orders = load_orders(300, seed=spec["seed"])
    system, f, warns = fresh(spec), Forecaster(), []
    def hook(snap):
        warns.extend(f.update(snap))
    monitor(system, orders[:150], spec["burst"], on_window=hook)
    mine = [w for w in warns if w.get("invariant") == invariant]
    now = violations(system)[invariant]
    if not mine or now == 0:
        return None
    monitor(system, orders[150:], spec["burst"], offset=150)
    actual = violations(system)[invariant]
    if actual == 0:
        return None
    return abs(mine[-1]["projected"] - actual) / actual, abs(now - actual) / actual

if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)
    q = make_variants([(("queue_loss",), 6), (("queue_loss", "sync_corruption"), 4), (("silent_drop", "queue_loss"), 4)], 3000)
    leads = [r for r in (lead_time(sp, "queue_saturation", "queue_conservation") for sp in q) if r]
    warned = [r for r in leads if r["warned"]]
    L = ["### Forecasting", "", "**1. Queue saturation warning (before the first lost message)**", "",
         f"- {len(warned)} of {len(leads)} queue-loss incidents were warned before the first loss",
         f"- median lead time: {statistics.median(r['lead'] for r in warned)} orders" if warned else "- no lead time",
         f"- capacity warnings on healthy systems with no fault (a busy queue warns even without a fault): burst 1: {sum(healthy_alarm(1, s) for s in range(10))}/10, "
         f"burst 2: {sum(healthy_alarm(2, s) for s in range(10))}/10, burst 4: {sum(healthy_alarm(4, s) for s in range(10))}/10", ""]
    base = [reactive_loss(sp) for sp in q]
    pro = [prevention(sp) for sp in q]
    L += ["**2. Acting on the forecast (drain the queue when saturation is predicted)**", "",
          "| mode | incidents | no message loss % | avg lost messages |", "|---|---|---|---|",
          f"| no action (monitor only) | {len(q)} | {round(100 * sum(b == 0 for b in base) / len(q), 1)} | {round(statistics.mean(base), 1)} |",
          f"| drain on warning | {len(q)} | {round(100 * sum(p[0] == 0 for p in pro) / len(q), 1)} | {round(statistics.mean(p[0] for p in pro), 1)} |", ""]
    L += ["**3. Projected loss 150 orders ahead (checked at order 150, scored at order 300)**", "",
          "| fault | cases | forecast error % | 'no change' error % |", "|---|---|---|---|"]
    for kind, inv in (("silent_drop", "requests_accounted"), ("sync_corruption", "warehouse_consistent")):
        specs = make_variants([((kind,), 8)], 4000)
        res = [r for r in (projection(sp, inv) for sp in specs) if r]
        L.append(f"| {kind} | {len(res)} | {round(100 * statistics.mean(r[0] for r in res), 1)} | {round(100 * statistics.mean(r[1] for r in res), 1)} |")
    L += ["", "**4. Faults these signals cannot forecast**", ""]
    for kind, inv in (("hung_worker", "worker_liveness"), ("provider_down", "payments_complete")):
        specs = make_variants([((kind,), 6)], 4500)
        res = []
        for sp in specs:
            _, snaps, warns = collect(sp)
            t = first_tick(snaps, lambda s: s["viol"][inv] > 0)
            res.append(t is not None and any(w["tick"] < t for w in warns))
        L.append(f"- {kind}: any warning before the failure in {sum(res)} of {len(res)} cases (these are generic queue-capacity warnings, not a signal for this fault)")
    md = "\n".join(L)
    print(md)
    open("results/forecast.md", "w").write(md + "\n")