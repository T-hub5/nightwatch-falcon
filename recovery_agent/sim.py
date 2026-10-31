import random, sqlite3
from collections import deque
from dataclasses import dataclass

class ProviderDown(Exception): pass

@dataclass
class FaultConfig:
    kind: str = "none"
    severity: float = 0.3
    seed: int = 0
    start_tick: int = 0

class System:
    def __init__(self, faults=None, seed=0):
        self.faults = faults or []
        self.rng = random.Random(seed)
        self.tick = 0
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
          CREATE TABLE orders(id TEXT PRIMARY KEY, amount_cents INT, status TEXT);
          CREATE TABLE payments(order_id TEXT PRIMARY KEY, amount_cents INT);
          CREATE TABLE ledger(order_id TEXT PRIMARY KEY, amount_cents INT);
          CREATE TABLE warehouse(order_id TEXT PRIMARY KEY, amount_cents INT);""")
        self.queue = deque()
        self.counters = dict(received=0, produced=0, consumed=0, http_5xx=0)
        self.logs = []
        self.disabled = set()
        self.queue_capacity = 50
        self.stall_streak = 0

    def _active(self, kind):
        return [f for f in self.faults if f.kind == kind and f.kind not in self.disabled
                and self.tick >= f.start_tick]
    def _hit(self, kind):
        return any(self.rng.random() < f.severity for f in self._active(kind))
    def log(self, level, msg): self.logs.append((self.tick, level, msg))

    def submit_order(self, oid, amount):
        self.tick += 1
        self.counters["received"] += 1
        try:
            if self._hit("silent_drop"):
                return 200
            self.db.execute("INSERT INTO orders VALUES(?,?,?)", (oid, amount, "pending_payment"))
            self._charge(oid, amount)
            self._enqueue(oid)
            return 200
        except ProviderDown:
            self.counters["http_5xx"] += 1
            self.log("ERROR", f"payment provider timeout for {oid}")
            return 503

    def _charge(self, oid, amount):
        if self._active("provider_down"):
            raise ProviderDown()
        self.db.execute("INSERT INTO payments VALUES(?,?)", (oid, amount))
        self.db.execute("UPDATE orders SET status='paid' WHERE id=?", (oid,))

    def _enqueue(self, oid):
        self.counters["produced"] += 1
        if len(self.queue) >= self.queue_capacity and self._hit("queue_loss"):
            return
        self.queue.append(oid)

    def work(self, n=10):
        if self.queue and self._active("hung_worker"):
            self.stall_streak += 1
            return
        if self.queue:
            self.stall_streak = 0
        for _ in range(min(n, len(self.queue))):
            oid = self.queue.popleft()
            self.counters["consumed"] += 1
            amt = self.db.execute("SELECT amount_cents FROM payments WHERE order_id=?", (oid,)).fetchone()[0]
            self.db.execute("INSERT OR REPLACE INTO ledger VALUES(?,?)", (oid, amt))
            self.db.execute("UPDATE orders SET status='fulfilled' WHERE id=?", (oid,))

    def sync_job(self):
        rows = self.db.execute("""SELECT o.id, o.amount_cents FROM orders o
                                  JOIN ledger l ON l.order_id=o.id
                                  WHERE o.id NOT IN (SELECT order_id FROM warehouse)""").fetchall()
        for oid, amt in rows:
            if self._hit("sync_corruption"):
                amt = (amt // 100) * 100
            self.db.execute("INSERT INTO warehouse VALUES(?,?)", (oid, amt))

    def run(self, orders, burst=1, sync_every=25):
        for i, (oid, amt) in enumerate(orders):
            self.submit_order(oid, amt)
            if i % burst == 0: self.work(1)
            if i % sync_every == 0: self.sync_job()
        return self