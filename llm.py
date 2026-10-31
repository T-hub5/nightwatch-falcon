import json, os, urllib.request

PROVIDERS = [
    ("ANTHROPIC_API_KEY", "anthropic", "https://api.anthropic.com/v1/messages", "claude-haiku-5-5"),
    ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    ("GROQ_API_KEY", "openai", "https://api.groq.com/openai/v1/chat/completions", "llama-3.1-8b-instant"),
    ("GEMINI_API_KEY", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.0-flash"),
]

class LLM:
    def __init__(self, key, style, url, model):
        self.key, self.style, self.url, self.model = key, style, url, os.getenv("LLM_MODEL", model)

    def complete(self, system, prompt, max_tokens=300, timeout=20):
        if self.style == "anthropic":
            body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": prompt}]}
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            headers = {"Authorization": f"Bearer {self.key}", "content-type": "application/json"}
        headers["User-Agent"] = "recovery-agent/1.0"
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        data = json.load(urllib.request.urlopen(req, timeout=timeout))
        return data["content"][0]["text"] if self.style == "anthropic" else data["choices"][0]["message"]["content"]

def get_llm():
    for env, style, url, model in PROVIDERS:
        if os.getenv(env): return LLM(os.environ[env], style, url, model)
    return None

def make_llm_ranker(llm, stats):
    def rank(hyps, evidence):
        det = sorted(hyps, key=lambda h: -h["score"])
        if len(hyps) < 2: return det
        causes = [h["cause"] for h in hyps]
        facts = {"violated_invariants": {k: v["detail"] for k, v in evidence["invariants"].items() if not v["ok"]},
                 "error_logs": evidence["logs"], "candidate_causes": causes}
        try:
            txt = llm.complete("You are an SRE triage assistant. Reply ONLY with a JSON list.",
                f"Facts: {json.dumps(facts)}\nOrder the candidate causes from most to least urgent to fix "
                f"(external-dependency outages last). Reply with a JSON list containing exactly these names: {causes}")
            order = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
            if sorted(order) != sorted(causes): raise ValueError("not a permutation of candidates")
            stats["llm_ok"] += 1
            return sorted(hyps, key=lambda h: order.index(h["cause"]))
        except Exception:
            stats["fallback"] += 1
            return det
    return rank

def facts_line(out, escalations):
    return (f"fixed={out.fixed}; escalated={out.escalated}; "
            f"reasons={[e['reason'] for e in escalations]}; unrecoverable_orders={out.residual_loss}")

def summarize(out, escalations, llm):
    facts = facts_line(out, escalations)
    note = ""
    if llm:
        try:
            note = llm.complete("You write concise incident handoffs for on-call engineers. Use ONLY the facts given; invent nothing.",
                f"Facts: {facts}\nEscalation details: {json.dumps(escalations)}\nWrite 3 short sentences: what happened, what was fixed, what a human must do.").strip()
        except Exception:
            note = ""
    if not note:
        note = "Escalating to on-call: " + "; ".join(f"{e['reason']}: {e['summary']}" for e in escalations)
    return f"{note}\n[verified facts] {facts}"