import tempfile, os
import tools
from eval import make_variants, make_system
from learning import LearningAgent, IncidentMemory

ORDER = ["generic", "targeted", "restart_only"]

def restart_that_works(self, name):
    if name == "queue":
        self.rollback_config(component="queue")
        self.replay_messages()
    return {"effect": "fixed_root_cause"}

def stream():
    return make_variants([(("queue_loss",), 8)], 9000)

def run(memory, learn=True):
    results = []
    for spec in stream():
        s = make_system(spec)
        out = LearningAgent(s, memory, learn=learn, seed=spec["seed"]).run()
        results.append(out)
    return results

def test_learner_picks_restart_when_restart_works():
    original = tools.Toolbox.restart_service
    tools.Toolbox.restart_service = restart_that_works
    try:
        mem = IncidentMemory(os.path.join(tempfile.mkdtemp(), "m.json"), order=ORDER)
        run(mem)
        best = mem.choose("queue_loss")
        stats = mem.stats["queue_loss"]
    finally:
        tools.Toolbox.restart_service = original
    assert best == ("restart_only", "exploit"), (best, stats)

def test_learner_avoids_restart_when_restart_useless():
    mem = IncidentMemory(os.path.join(tempfile.mkdtemp(), "m.json"), order=ORDER)
    run(mem)
    assert mem.choose("queue_loss")[0] == "targeted", mem.stats["queue_loss"]

if __name__ == "__main__":
    test_learner_picks_restart_when_restart_works()
    test_learner_avoids_restart_when_restart_useless()
    print("2 passed")