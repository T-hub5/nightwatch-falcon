import os, random, statistics
from eval import make_variants, make_system, truth_unhealed
from learning import LearningAgent, IncidentMemory
from agent import SIGNATURES

PLAN = [(("silent_drop",), 3), (("queue_loss",), 4), (("sync_corruption",), 3), (("hung_worker",), 3),
        (("queue_loss", "sync_corruption"), 3), (("silent_drop", "hung_worker"), 3), (("provider_down", "silent_drop"), 2)]
MEMORY_PATH = "results/incident_memory.json"

def run_one(spec, memory, learn, force):
    s = make_system(spec)
    ag = LearningAgent(s, memory, learn=learn, force_strategy=force, seed=spec["seed"])
    out = ag.run()
    unhealed = truth_unhealed(s, set(spec["kinds"]))
    esc = {k for k in out.escalated if k in SIGNATURES}
    return {"steps": out.steps, "harm": len(ag.tb.harm) > 0,
            "correct": unhealed <= esc and not (out.status == "recovered" and unhealed)}

def summarize(rows):
    return (round(statistics.mean(r["steps"] for r in rows), 1),
            round(100 * sum(r["harm"] for r in rows) / len(rows), 1),
            round(100 * sum(r["correct"] for r in rows) / len(rows), 1))

def row(label, rows):
    steps, harm, correct = summarize(rows)
    return f"| {label} | {len(rows)} | {steps} | {harm} | {correct} |"

if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)
    if os.path.exists(MEMORY_PATH):
        os.remove(MEMORY_PATH)
    stream = make_variants(PLAN, 5000)
    random.Random(0).shuffle(stream)
    test = make_variants(PLAN, 7000)
    memory = IncidentMemory(MEMORY_PATH)
    cold = [run_one(sp, IncidentMemory(), False, "generic") for sp in stream]
    learner = [run_one(sp, memory, True, None) for sp in stream]
    half = len(stream) // 2
    test_cold = [run_one(sp, IncidentMemory(), False, "generic") for sp in test]
    test_learned = [run_one(sp, memory, False, None) for sp in test]
    test_oracle = [run_one(sp, IncidentMemory(), False, "targeted") for sp in test]
    lines = ["### Learning from incidents", "",
             "| run | incidents | avg steps | harmful % | correct outcome % |", "|---|---|---|---|---|",
             row("stream, no memory (first half)", cold[:half]),
             row("stream, no memory (second half)", cold[half:]),
             row("stream, learning (first half)", learner[:half]),
             row("stream, learning (second half)", learner[half:]),
             row("unseen test, no memory", test_cold),
             row("unseen test, learned memory", test_learned),
             row("unseen test, best fixed strategy", test_oracle), "", "What it learned:"]
    lines += [f"- {l}" for l in memory.lessons()]
    md = "\n".join(lines)
    print(md)
    open("results/learning.md", "w").write(md + "\n")