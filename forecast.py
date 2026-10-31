from invariants import check_all

DRIFT = ("requests_accounted", "warehouse_consistent")

def _slope(points):
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    den = sum((t - mt) ** 2 for t, _ in points)
    return sum((t - mt) * (v - mv) for t, v in points) / den if den else 0.0

class Forecaster:
    def __init__(self, capacity=50, horizon=40, lookback=4, min_slope=0.05, project_over=150):
        self.capacity, self.horizon, self.lookback = capacity, horizon, lookback
        self.min_slope, self.project_over = min_slope, project_over
        self.history = []

    def update(self, snap):
        self.history.append(snap)
        recent = self.history[-self.lookback:]
        if len(recent) < self.lookback:
            return []
        warnings = []
        slope = _slope([(h["tick"], h["depth"]) for h in recent])
        if slope > self.min_slope:
            eta = (self.capacity - snap["depth"]) / slope
            if eta <= self.horizon:
                warnings.append({"kind": "queue_saturation", "tick": snap["tick"], "eta": round(max(eta, 0), 1)})
        for name in DRIFT:
            slope = _slope([(h["tick"], h["viol"][name]) for h in recent])
            if slope > 0 and snap["viol"][name] > 0:
                warnings.append({"kind": "loss_drift", "invariant": name, "tick": snap["tick"],
                                 "projected": round(snap["viol"][name] + slope * self.project_over)})
        return warnings

def snapshot(system):
    return {"tick": system.tick, "depth": len(system.queue),
            "viol": {r.name: r.violations for r in check_all(system)}}

def monitor(system, orders, burst=1, window=10, sync_every=25, offset=0, on_window=None):
    snaps = []
    for i, (oid, amt) in enumerate(orders):
        j = offset + i
        system.submit_order(oid, amt)
        if j % burst == 0:
            system.work(1)
        if j % sync_every == 0:
            system.sync_job()
        if (i + 1) % window == 0:
            snap = snapshot(system)
            snaps.append(snap)
            if on_window:
                on_window(snap)
    return snaps