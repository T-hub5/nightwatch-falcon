import argparse, hashlib, json, os, random, statistics, sys
from data import load_orders
from sim import System, FaultConfig
from invariants import check_all
from agent import Agent, SIGNATURES
from baseline import run_baseline
from llm import get_llm

RANGES = {"silent_drop": (0.05, 0.3), "queue_loss": (0.2, 0.6), "sync_corruption": (0.1, 0.5), "provider_down": (1.0, 1.0), "hung_worker": (1.0, 1.0)}
DEV_PLAN = [(("silent_drop",), 3), (("queue_loss",), 3), (("sync_corruption",), 3), (("provider_down",), 3),
            (("silent_drop", "sync_corruption"), 4), (("provider_down", "sync_corruption"), 4),
            (("hung_worker",), 3), (("hung_worker", "silent_drop"), 3)]
HELD_PLAN = [(("queue_loss", "sync_corruption"), 3), (("silent_drop", "queue_loss"), 3),
             (("provider_down", "silent_drop"), 2), (("silent_drop", "queue_loss", "sync_corruption"), 2),
             (("hung_worker", "queue_loss"), 2), (("hung_worker", "provider_down"), 2)]
SYSTEMS = ["baseline", "no_invariants", "no_verification", "full_agent"] + (["llm_agent"] if get_llm() else [])
PERTURB = {"none": (0.0, 1), "flaky_fix": (0.5, 5), "stuck_fix": (1.0, 1)}
CODE_FILES = ["llm.py", "sim.py", "invariants.py", "tools.py", "agent.py", "baseline.py", "data.py"]

def make_system(spec):
    faults = [FaultConfig(k, sev, spec["seed"], start) for k, sev, start in spec["faults"]]
    return System(faults, seed=spec["seed"]).run(load_orders(300, seed=spec["seed"]), burst=spec["burst"])

def manifests(spec):
    v = {r.name for r in check_all(make_system(spec)) if not r.ok}
    return all(SIGNATURES[k] <= v for k in spec["kinds"])

def make_variants(plan, base):
    out, n = [], 0
    for kinds, count in plan:
        for _ in range(count):
            while True:
                n += 1
                rng = random.Random(base + n)
                spec = {"kinds": list(kinds), "seed": base + n, "burst": 4 if "queue_loss" in kinds else rng.choice([1, 2]),
                        "faults": [(k, round(rng.uniform(*RANGES[k]), 3), rng.randint(120, 200) if k == "provider_down" else rng.randint(0, 60))
                                   for k in kinds]}
                if manifests(spec):
                    out.append(spec); break
    return out

def truth_unhealed(s, kinds):
    res = {r.name: r.violations for r in check_all(s)}
    bad = set()
    for k in kinds:
        if k == "provider_down" or k not in s.disabled: bad.add(k)
        elif k == "queue_loss" and (res["queue_conservation"] or res["ledger_matches_payments"]): bad.add(k)
        elif k == "sync_corruption" and res["warehouse_consistent"]: bad.add(k)
        elif k == "hung_worker" and res["worker_liveness"]: bad.add(k)
    return bad

def run_one(name, spec, fail_p, rseed):
    s, kinds = make_system(spec), set(spec["kinds"])
    if name == "baseline":
        b = run_baseline(s)
        claims_ok, esc, diag, detected, steps, harm = b["claimed"] == "healthy", set(), set(), b["actions"] > 1, b["actions"], len(b["harm"])
    else:
        ag = Agent(s, use_invariants=name != "no_invariants", use_verification=name != "no_verification",
                   fix_fail_prob=fail_p, seed=spec["seed"] * 10 + rseed, llm=get_llm() if name == "llm_agent" else None)
        out = ag.run()
        claims_ok = out.status == "recovered"
        esc = {k for k in out.escalated if k in SIGNATURES}
        diag, detected, steps, harm = set(out.diagnosed), bool(out.diagnosed or out.escalated), out.steps, len(ag.tb.harm)
    unhealed, fixable = truth_unhealed(s, kinds), kinds - {"provider_down"}
    return {"detect": detected, "rootcause": diag == kinds, "fixable": bool(fixable), "recovered": not (unhealed & fixable),
            "correct": unhealed <= esc and not (claims_ok and unhealed), "false_claim": claims_ok and bool(unhealed),
            "harm": harm > 0, "steps": steps}

def agg(rows):
    f = [r for r in rows if r["fixable"]]
    pct = lambda xs, key: round(100 * sum(x[key] for x in xs) / max(len(xs), 1), 1)
    return {"n": len(rows), "detect": pct(rows, "detect"), "rootcause": pct(rows, "rootcause"), "recovered": pct(f, "recovered"),
            "correct": pct(rows, "correct"), "false_claim": pct(rows, "false_claim"), "harm": pct(rows, "harm"),
            "steps": round(statistics.mean(r["steps"] for r in rows), 1)}

def evaluate(specs):
    table = {}
    for pname, (p, nseeds) in PERTURB.items():
        for sysname in SYSTEMS:
            if sysname in ("baseline", "llm_agent") and pname != "none": continue
            per_seed = [[run_one(sysname, sp, p, rs) for sp in specs] for rs in range(nseeds)]
            a = agg([r for seed_rows in per_seed for r in seed_rows])
            if nseeds > 1:
                a["correct_sd"] = round(statistics.pstdev(agg(sr)["correct"] for sr in per_seed), 1)
            table[f"{sysname}|{pname}"] = a
    return table

def render(table, title):
    L = [f"### {title}", "", "| system | perturbation | n | detect % | root-cause % | verified recovery % | correct outcome % | false 'recovered' % | harmful % | steps |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for key, a in table.items():
        sysn, pert = key.split("|")
        sd = f" ±{a['correct_sd']}" if "correct_sd" in a else ""
        L.append(f"| {sysn} | {pert} | {a['n']} | {a['detect']} | {a['rootcause']} | {a['recovered']} | {a['correct']}{sd} | {a['false_claim']} | {a['harm']} | {a['steps']} |")
    return "\n".join(L)

def code_hash():
    return hashlib.sha256(b"".join(open(f, "rb").read() for f in CODE_FILES)).hexdigest()[:12]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--heldout", action="store_true"); ap.add_argument("--force", action="store_true")
    args = ap.parse_args(); os.makedirs("results", exist_ok=True)
    lock = "results/heldout_lock.json"
    if args.heldout:
        if os.path.exists(lock) and not args.force:
            sys.exit(f"Held-out already run on code hash {json.load(open(lock))['code_hash']}. Refusing to re-run (use --force to override, and say so).")
        specs, title, out = make_variants(HELD_PLAN, 9000), "HELD-OUT (unseen fault combinations, run once)", "results/heldout.json"
    else:
        specs, title, out = make_variants(DEV_PLAN, 100), "DEV (used for development)", "results/dev.json"
    table = evaluate(specs)
    md = render(table, f"{title} | code {code_hash()} | {len(specs)} variants")
    print(md)
    json.dump({"code_hash": code_hash(), "table": table}, open(out, "w"), indent=2)
    open(out.replace(".json", ".md"), "w").write(md + "\n")
    if args.heldout: json.dump({"code_hash": code_hash()}, open(lock, "w"))


