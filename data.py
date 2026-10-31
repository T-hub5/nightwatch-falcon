import csv, os, random

OLIST = os.path.join(os.path.dirname(__file__), "data", "olist_order_payments_dataset.csv")

def load_orders(n=500, seed=0):
    rng = random.Random(seed)
    if os.path.exists(OLIST):
        totals = {}
        with open(OLIST, newline="") as f:
            for row in csv.DictReader(f):
                totals[row["order_id"]] = totals.get(row["order_id"], 0) + round(float(row["payment_value"]) * 100)
        items = sorted(totals.items())
        rng.shuffle(items)
        return items[:n]
    return [(f"ord_{i:05d}", rng.randint(500, 50000) + rng.choice([0, 37, 99])) for i in range(n)]