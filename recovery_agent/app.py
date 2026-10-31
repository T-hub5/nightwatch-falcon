import datetime, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from data import load_orders
from sim import System, FaultConfig
from invariants import check_all
from baseline import run_baseline
from eval import truth_unhealed
from agent import SIGNATURES, MAX_STEPS, MAX_FIX_ATTEMPTS
from tools import Toolbox
from learning import LearningAgent, IncidentMemory
from forecast import Forecaster, monitor

DEFAULTS = {"silent_drop": (0.1, 0), "queue_loss": (0.3, 0), "sync_corruption": (0.2, 0),
            "hung_worker": (1.0, 20), "provider_down": (1.0, 150)}
MEMORY_PATH = "results/incident_memory.json"
MEMORY = IncidentMemory(MEMORY_PATH)
AUDIT_PATH = "results/audit_log.jsonl"
HISTORY_PATH = "results/run_history.jsonl"
CONSTRAINTS_PATH = "results/constraints.json"
PRIORITY = {"High": 0, "Medium": 1, "Low": 2}
PREVENT = {"silent_drop": "Fix the payment handler that swallows errors and still returns success",
           "queue_loss": "Raise the queue capacity or add back-pressure before peak traffic",
           "sync_corruption": "Fix the sync job so it stops truncating cents",
           "hung_worker": "Add a timeout and a watchdog so a stuck worker restarts itself"}

def now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path) if line.strip()]

def constraints():
    return {
        "allowed_tools": sorted(Toolbox.ALLOWED),
        "max_steps": MAX_STEPS,
        "max_fix_attempts": MAX_FIX_ATTEMPTS,
        "rules": [
            "Only allowlisted tools can be called; anything else is blocked and logged",
            f"At most {MAX_STEPS} tool calls per incident",
            f"At most {MAX_FIX_ATTEMPTS} fix attempts per cause, then it escalates",
            "Problems outside our control, like a payment provider outage, are escalated at once and never retried",
            "A fix counts only if fresh traffic proves it; otherwise it escalates",
            "Lost orders are reported as lost and never claimed as restored",
        ],
    }

def save_constraints():
    os.makedirs("results", exist_ok=True)
    json.dump(constraints(), open(CONSTRAINTS_PATH, "w"), indent=2)

def task_guide(out):
    tasks = []
    if out.residual_loss:
        tasks.append({"priority": "High", "owner": "Support team",
                      "task": f"Contact the {out.residual_loss} customers whose orders were lost, and refund or re-take them",
                      "why": "These orders left no record, so the agent cannot restore them"})
    if "provider_down" in out.escalated:
        tasks.append({"priority": "High", "owner": "On-call engineer",
                      "task": "Check the payment provider status. When it is back, re-run the payments for orders stuck waiting for payment",
                      "why": "No safe local fix exists, so the agent escalated instead of retrying"})
    for cause in out.escalated:
        if cause in PREVENT:
            tasks.append({"priority": "Medium", "owner": "On-call engineer",
                          "task": f"Re-verify {cause.replace('_', ' ')}: a fix was applied but could not be confirmed",
                          "why": "Verification was inconclusive or failed"})
    for cause in out.fixed:
        if cause in PREVENT:
            tasks.append({"priority": "Low", "owner": "Engineering", "task": PREVENT[cause],
                          "why": "The agent stopped the damage, but the root cause is still in the code"})
    return sorted(tasks, key=lambda x: PRIORITY[x["priority"]])

def summary():
    rows = read_jsonl(HISTORY_PATH)
    per = {}
    for r in rows:
        for k in r["kinds"]:
            p = per.setdefault(k, {"occurred": 0, "fixed": 0, "escalated": 0})
            p["occurred"] += 1
            p["fixed"] += int(k in r["fixed"])
            p["escalated"] += int(k in r["escalated"])
    return {"runs": len(rows), "per_kind": per,
            "problems": sum(len(r["kinds"]) for r in rows),
            "fixed": sum(len(r["fixed"]) for r in rows),
            "escalated": sum(len(r["escalated"]) for r in rows),
            "lost": sum(r["lost"] for r in rows),
            "baseline_false_healthy": sum(int(r["baseline_false_healthy"]) for r in rows)}

def faults_for(kinds, seed):
    return [FaultConfig(k, DEFAULTS[k][0], seed, DEFAULTS[k][1]) for k in kinds]

def invariants(system):
    return [{"name": r.name, "ok": r.ok, "violations": r.violations, "detail": r.detail} for r in check_all(system)]

def short(obs, n=150):
    text = json.dumps(obs, default=str)
    return text if len(text) <= n else text[:n] + "..."

def run_scenario(kinds, seed):
    burst = 4 if "queue_loss" in kinds else 1
    orders = load_orders(300, seed=seed)
    base_sys = System(faults_for(kinds, seed), seed=seed).run(orders, burst=burst)
    bv = {r.name: r.violations for r in check_all(base_sys)}
    base_lost = bv["requests_accounted"] + bv["queue_conservation"] + bv["warehouse_consistent"]
    base = run_baseline(base_sys)
    base_unhealed = sorted(truth_unhealed(base_sys, set(kinds)))

    agent_sys = System(faults_for(kinds, seed), seed=seed).run(orders, burst=burst)
    before = invariants(agent_sys)
    shop_before = {"placed": agent_sys.counters["received"],
                   "saved": agent_sys.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                   "unpaid": agent_sys.db.execute("SELECT COUNT(*) FROM orders WHERE status='pending_payment'").fetchone()[0]}
    ag = LearningAgent(agent_sys, MEMORY, seed=seed)
    out = ag.run()
    after = invariants(agent_sys)
    unhealed = truth_unhealed(agent_sys, set(kinds))
    escalated = {k for k in out.escalated if k in SIGNATURES}
    correct = unhealed <= escalated and not (out.status == "recovered" and bool(unhealed))

    f, warns = Forecaster(), []
    fore_sys = System(faults_for(kinds, seed), seed=seed)
    snaps = monitor(fore_sys, orders, burst, on_window=lambda sn: warns.extend(f.update(sn)))
    series = [{"tick": s["tick"], "depth": s["depth"],
               "lost": s["viol"]["queue_conservation"] + s["viol"]["requests_accounted"] + s["viol"]["warehouse_consistent"]}
              for s in snaps]
    run_id = len(read_jsonl(HISTORY_PATH)) + 1
    scenario = " + ".join(kinds) or "no faults"
    audit_rows = [{"time": now(), "run": run_id, "scenario": scenario, "step": t["step"], "tool": t["tool"], "args": t["args"],
                   "result": short(t["observation"], 200), "allowed": True if t["tool"] in Toolbox.ALLOWED else "internal", "decision": t["decision"]}
                  for t in out.trace]
    for e in ag.tb.escalations:
        audit_rows.append({"time": now(), "run": run_id, "scenario": scenario, "step": len(audit_rows) + 1, "tool": "escalate",
                           "args": {"reason": e["reason"]}, "result": e["summary"], "allowed": True, "decision": "handed to an engineer"})
    append_jsonl(AUDIT_PATH, audit_rows)
    append_jsonl(HISTORY_PATH, [{"run": run_id, "time": now(), "kinds": kinds, "fixed": out.fixed, "escalated": out.escalated,
                                 "lost": out.residual_loss, "steps": out.steps,
                                 "baseline_false_healthy": base["claimed"] == "healthy" and bool(base_unhealed)}])
    return {
        "kinds": kinds, "seed": seed, "run": run_id, "tasks": task_guide(out),
        "baseline": {"claimed": base["claimed"], "actions": base["actions"], "harm": len(base["harm"]),
                     "actually_healthy": not base_unhealed, "unhealed": base_unhealed, "lost_orders": base_lost},
        "agent": {"status": out.status, "fixed": out.fixed, "escalated": out.escalated, "steps": out.steps,
                  "unrecoverable_orders": out.residual_loss, "summary": out.human_summary or out.summary,
                  "strategies": out.strategies, "actually_healthy": not unhealed, "unhealed": sorted(unhealed), "correct": correct},
        "shop": shop_before,
        "invariants_before": before, "invariants_after": after,
        "trace": [{"step": t["step"], "thought": t["thought"], "tool": t["tool"], "decision": t["decision"],
                   "obs": short(t["observation"]), "args": t["args"], "allowed": t["tool"] in Toolbox.ALLOWED,
                   "stable": t["observation"].get("stable") if t["tool"] == "verify_window" and isinstance(t["observation"], dict) else None}
                  for t in out.trace],
        "forecast": {"series": series, "warnings": warns, "capacity": f.capacity},
        "memory": {"lessons": MEMORY.lessons(), "incidents": len(MEMORY.log)},
    }

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Recovery Agent</title>
<style>
:root{--bg:#f6f5f1;--card:#ffffff;--line:#dedbd2;--ink:#1d1d1b;--mute:#6a6860;--teal:#1f7a5c;--amber:#a85d00;--red:#b3261e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Helvetica Neue",Helvetica,Arial,sans-serif}
header{padding:18px 28px;background:#1d1d1b;color:#fff}h1{margin:0;font-size:19px;font-weight:700}header p{margin:2px 0 0;color:#b9b6ad;font-size:14px}
main{max-width:1120px;margin:0 auto;padding:22px 28px 60px;display:flex;flex-direction:column;gap:16px}
#out{display:flex;flex-direction:column;gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:18px 20px}
.card h2{margin:0 0 10px;font-size:13px;color:var(--ink);font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
label.k{display:flex;gap:6px;align-items:center;background:#fff;border:1px solid var(--line);padding:6px 10px;border-radius:4px;cursor:pointer}
button{background:#1d1d1b;color:#fff;border:0;border-radius:4px;padding:9px 16px;font-weight:600;cursor:pointer;font-size:14px}
button.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}button:disabled{opacity:.5}
input[type=number]{width:70px;background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:6px}
.presets{display:flex;flex-wrap:wrap;gap:8px}
.shopgrid{display:grid;grid-template-columns:1fr 1.4fr;gap:24px}@media(max-width:860px){.shopgrid{grid-template-columns:1fr}}
.stat{display:flex;justify-content:space-between;align-items:baseline;padding:8px 0;border-top:1px solid var(--line)}.stat b{font-size:26px;font-variant-numeric:tabular-nums}
.ev{display:grid;grid-template-columns:92px 1fr;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.tag{font-size:11px;font-weight:700;letter-spacing:.06em}.t-det{color:var(--red)}.t-act{color:var(--amber)}.t-ok{color:var(--teal)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:860px){.two{grid-template-columns:1fr}}
.big{font-size:24px;font-weight:700;margin:2px 0 8px}.good{color:var(--teal)}.bad{color:var(--red)}.warn{color:var(--amber)}
.row{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-top:1px solid var(--line);color:var(--mute)}.row b{color:var(--ink);font-weight:600;text-align:right}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-top:1px solid var(--line)}th{color:var(--mute);font-weight:600;font-size:13px}
.chip{display:inline-block;padding:1px 8px;border-radius:3px;font-size:12px;font-weight:700}.c-ok{background:#e3f1ea;color:var(--teal)}.c-bad{background:#f8e1df;color:var(--red)}
.step{display:grid;grid-template-columns:34px 1fr;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.num{color:var(--mute);font-variant-numeric:tabular-nums}.tool{font-family:ui-monospace,Menlo,monospace;color:var(--ink);font-size:13px;font-weight:600}
.obs{font-family:ui-monospace,Menlo,monospace;color:var(--mute);font-size:12px;word-break:break-all}.dec{color:var(--amber);font-size:13px}
pre{white-space:pre-wrap;margin:0;font:inherit;color:var(--ink)}.note{color:var(--mute);font-size:13px}svg text{fill:var(--mute);font-size:11px}
#empty{color:var(--mute);text-align:center;padding:40px}
.pri{font-size:11px;font-weight:700;letter-spacing:.06em;padding:2px 7px;border-radius:3px;display:inline-block;text-align:center}
.p-High{background:#f8e1df;color:var(--red)}.p-Medium{background:#f7e8d2;color:var(--amber)}.p-Low{background:#ece9e0;color:var(--mute)}
.task{display:grid;grid-template-columns:76px 1fr;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
.rules{margin:0;padding-left:18px;color:var(--ink)}.rules li{margin:3px 0}
.tools{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.tools span{font-family:ui-monospace,Menlo,monospace;font-size:12px;border:1px solid var(--line);border-radius:3px;padding:2px 7px;background:#fafaf7}
.logrow{display:grid;grid-template-columns:150px 1.1fr 1.6fr 80px;gap:10px;padding:6px 0;border-top:1px solid var(--line);font-size:13px}
.logrow .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}
a.dl{color:var(--ink);font-weight:600}
</style></head><body>
<header><h1>Recovery Agent</h1><p>Inject a failure, then compare a plain alert-and-restart system with an agent that detects, acts, verifies, learns and forecasts.</p></header>
<main>
<div class="card"><h2>1. Choose what breaks</h2>
<div class="controls" id="kinds"></div>
<div class="controls" style="margin-top:12px"><label>Seed <input type="number" id="seed" value="1"></label>
<button id="run">Run scenario</button><button class="ghost" id="reset">Reset learned memory</button><button class="ghost" id="clear">Clear history and log</button></div>
<div class="presets" style="margin-top:12px" id="presets"></div></div>
<div id="out"><div class="card" id="empty">Pick faults and press Run scenario.</div></div>
</main>
<script>
const KINDS=[["silent_drop","Silent order drop"],["queue_loss","Queue loses messages"],["sync_corruption","Sync corrupts data"],["hung_worker","Worker hangs"],["provider_down","Payment provider down"]];
const PRESETS=[["Silent drop",["silent_drop"]],["Queue loss",["queue_loss"]],["Hung worker + drop",["hung_worker","silent_drop"]],["Provider outage + corruption",["provider_down","sync_corruption"]],["Everything",["silent_drop","queue_loss","sync_corruption"]]];
const $=s=>document.querySelector(s);
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const box=$("#kinds");
KINDS.forEach(([k,n],i)=>{const l=el("label","k");const c=el("input");c.type="checkbox";c.value=k;c.checked=i===0;l.append(c,document.createTextNode(n));box.append(l)});
function setKinds(list){box.querySelectorAll("input").forEach(c=>c.checked=list.includes(c.value))}
PRESETS.forEach(([n,l])=>{const b=el("button","ghost",n);b.onclick=()=>{setKinds(l);run()};$("#presets").append(b)});
$("#run").onclick=run;
$("#reset").onclick=async()=>{await fetch("/api/reset",{method:"POST"});if(window.lastData)run()};
$("#clear").onclick=async()=>{await fetch("/api/clear",{method:"POST"});if(window.lastData)render(window.lastData);else $("#out").replaceChildren()};
async function run(){
  const kinds=[...box.querySelectorAll("input:checked")].map(c=>c.value);
  const b=$("#run");b.disabled=true;b.textContent="Running...";
  try{const r=await fetch("/api/run?faults="+kinds.join(",")+"&seed="+$("#seed").value);const d=await r.json();if(d.error)throw new Error(d.error);window.lastData=d;render(d)}
  catch(e){$("#out").replaceChildren(Object.assign(el("div","card"),{textContent:"Error: "+e.message}))}
  b.disabled=false;b.textContent="Run scenario"}
function row(a,k,v,cls){const r=el("div","row");r.append(el("span","",k));const b=el("b",cls||"",v);r.append(b);a.append(r)}
function chart(title,series,key,max,marks,note,cap){
  const W=520,H=150,L=34,R=10,T=14,B=22,mt=Math.max(...series.map(s=>s.tick),1);
  const x=t=>L+t/mt*(W-L-R),y=v=>H-B-Math.min(v,max)/max*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%"><line x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}" stroke="#cfcbc0"/>`;
  s+=`<text x="2" y="${T}">${max}</text><text x="2" y="${H-B}">0</text><text x="${W-R}" y="${H-4}" text-anchor="end">orders processed</text>`;
  if(cap)s+=`<line x1="${L}" x2="${W-R}" y1="${y(cap)}" y2="${y(cap)}" stroke="#b3261e" stroke-dasharray="4 4"/><text x="${W-R}" y="${y(cap)-4}" text-anchor="end" style="fill:#b3261e">queue capacity ${cap}</text>`;
  s+=`<polyline fill="none" stroke="#1f7a5c" stroke-width="2" points="${series.map(p=>x(p.tick)+","+y(p[key])).join(" ")}"/>`;
  marks.forEach(m=>{if(m.tick==null)return;s+=`<line x1="${x(m.tick)}" x2="${x(m.tick)}" y1="${T}" y2="${H-B}" stroke="${m.color}" stroke-dasharray="3 3"/><text x="${Math.min(x(m.tick)+4,W-90)}" y="${T+(m.dy||0)+8}" style="fill:${m.color}">${m.label}</text>`});
  s+="</svg>";
  const c=el("div");c.append(el("div","note",title));c.insertAdjacentHTML("beforeend",s);if(note)c.append(el("div","note",note));return c}
const RULE_LABELS={requests_accounted:"Every order received was saved",queue_conservation:"No order lost in the queue",payments_complete:"Every order was paid",ledger_matches_payments:"Every payment is in the books",warehouse_consistent:"Warehouse amounts match the orders",worker_liveness:"The order worker is running"};
const PLAIN={requests_accounted:n=>n+" orders were accepted but never saved",queue_conservation:n=>n+" orders vanished from the work queue",payments_complete:n=>n+" orders are stuck waiting for payment",ledger_matches_payments:n=>n+" paid orders are missing from the books",warehouse_consistent:n=>n+" warehouse records have the wrong amount",worker_liveness:()=>"The order worker has stopped processing"};
function plainAction(s){
  let o={};try{o=JSON.parse(s.obs)}catch(e){}
  if(s.tool==="rollback_config")return o.changed===false?"Tried to roll back a faulty setting (it did not apply)":"Rolled back the faulty "+(o.component||"")+" setting";
  if(s.tool==="replay_messages")return"Replayed "+(o.replayed!==undefined?o.replayed:"the")+" lost orders from the payment records";
  if(s.tool==="drain_queue")return"Cleared the stuck queue";
  if(s.tool==="repair_warehouse")return"Repaired the wrong warehouse records";
  if(s.tool==="restart_service")return"Restarted the service (no effect on the root cause)";
  if(s.tool==="check_provider")return o.healthy===false?"Checked the payment provider: it is down":"Checked the payment provider: it is healthy";
  return null}
function shopCard(d){
  const c=el("div","card");c.append(el("h2","","Online shop monitor"));
  const g=el("div","shopgrid");
  const left=el("div");
  const stat=(k,v,cls)=>{const r=el("div","stat");r.append(el("span","note",k));r.append(el("b",cls||"",String(v)));left.append(r)};
  stat("Orders customers placed",d.shop.placed);
  stat("Orders the shop actually has",d.shop.saved,d.shop.saved<d.shop.placed?"bad":"good");
  if(d.shop.saved<d.shop.placed)stat("Customers who paid but have no order",d.shop.placed-d.shop.saved,"bad");
  if(d.shop.unpaid)stat("Orders stuck waiting for payment",d.shop.unpaid,"bad");
  const ql=d.invariants_before.find(r=>r.name==="queue_conservation");if(ql&&!ql.ok)stat("Paid orders never fulfilled",ql.violations,"bad");
  const dash=el("div","note","Old dashboard: "+(d.baseline.claimed==="healthy"?"all green, nothing wrong":"gave up after restarts"));
  dash.style.marginTop="10px";left.append(dash);
  const right=el("div");
  const ev=(tag,cls,text)=>{const r=el("div","ev");r.append(el("div","tag "+cls,tag));r.append(el("div","",text));right.append(r)};
  let any=false;
  d.invariants_before.forEach(r=>{if(!r.ok){any=true;ev("DETECTED","t-det",(PLAIN[r.name]||(n=>r.name))(r.violations))}});
  if(!any)ev("ALL CLEAR","t-ok","No problems detected.");
  d.trace.forEach(s=>{const p=plainAction(s);if(p)ev("ACTION","t-act",p);
    if(s.tool==="verify_window")ev("CHECK","t-act",s.stable?"Sent fresh test orders: no new problems, fix confirmed":"Sent fresh test orders: could not confirm the fix")});
  if(d.agent.status==="recovered"&&any)ev("RESOLVED","t-ok","Fixed and verified."+(d.agent.unrecoverable_orders?" "+d.agent.unrecoverable_orders+" orders are lost for good: contact those customers.":""));
  if(d.agent.status!=="recovered")ev("ESCALATED","t-det","An engineer is needed: "+d.agent.escalated.join(", ")+". See the handoff note below.");
  g.append(left,right);c.append(g);return c}
function taskCard(d){
  const c=el("div","card");c.append(el("h2","","Task guide: what an engineer should solve next"));
  if(!d.tasks.length){c.append(el("div","note","Nothing to do. The system is healthy and the agent has nothing to hand over."));return c}
  d.tasks.forEach(k=>{const r=el("div","task");r.append(el("span","pri p-"+k.priority,k.priority.toUpperCase()));
    const x=el("div");x.append(el("div","",k.task));x.append(el("div","note","Owner: "+k.owner+". "+k.why));r.append(x);c.append(r)});
  return c}
async function loadExtra(){
  const old=$("#extra");if(old)old.remove();
  const ex=el("div");ex.id="extra";ex.style.display="flex";ex.style.flexDirection="column";ex.style.gap="16px";
  const [cons,sum,log]=await Promise.all([fetch("/api/constraints").then(r=>r.json()),fetch("/api/summary").then(r=>r.json()),fetch("/api/audit").then(r=>r.json())]);
  const dash=el("div","card");dash.append(el("h2","","Dashboard: problems found and solved by the agent"));
  dash.append(el("div","note",sum.runs+" runs so far. "+sum.problems+" problems injected, "+sum.fixed+" fixed and verified by the agent, "+sum.escalated+" escalated to an engineer, "+sum.lost+" orders lost for good. The old alert system said healthy while something was broken in "+sum.baseline_false_healthy+" runs."));
  const W=720,rowH=44,H=KINDS.length*rowH+30,L=170;let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  const mx=Math.max(1,...Object.values(sum.per_kind).map(p=>p.occurred));const bw=v=>v/mx*(W-L-60);
  const series=[["occurred","Occurred","#9a978d"],["fixed","Fixed","#1f7a5c"],["escalated","Escalated","#a85d00"]];
  KINDS.forEach(([k,n],i)=>{const p=sum.per_kind[k]||{occurred:0,fixed:0,escalated:0};const y=10+i*rowH;
    s+=`<text x="0" y="${y+20}" style="fill:#1d1d1b;font-size:12px">${n}</text>`;
    series.forEach(([key,label,col],j)=>{const w=Math.max(bw(p[key]),p[key]?2:0);s+=`<rect x="${L}" y="${y+j*12}" width="${w}" height="9" fill="${col}"/><text x="${L+w+6}" y="${y+j*12+8}" style="font-size:10px">${p[key]}</text>`})});
  s+="</svg>";dash.insertAdjacentHTML("beforeend",s);
  const lg=el("div","note","Grey: occurred. Green: fixed by the agent. Amber: escalated to an engineer.");dash.append(lg);ex.append(dash);
  const g=el("div","card");g.append(el("h2","","Constraints the agent cannot break (saved in results/constraints.json)"));
  const ul=el("ul","rules");cons.rules.forEach(r=>ul.append(el("li","",r)));g.append(ul);
  const tl=el("div","tools");cons.allowed_tools.forEach(n=>tl.append(el("span","",n)));g.append(el("div","note","Allowed tools (nothing else can be called):"));g.append(tl);
  ex.append(g);
  const a=el("div","card");a.append(el("h2","","Audit log: every action the agent took (latest 14)"));
  const dl=el("a","dl","Download the full log (audit_log.jsonl)");dl.href="/audit.jsonl";dl.setAttribute("download","audit_log.jsonl");a.append(dl);
  const hd=el("div","logrow");["Time","Scenario","Tool call and result","Allowed"].forEach(x=>hd.append(el("b","note",x)));a.append(hd);
  log.forEach(r=>{const row=el("div","logrow");row.append(el("div","mono",r.time.replace("T"," ")));row.append(el("div","",(r.run?"#"+r.run+" ":"")+r.scenario));
    row.append(el("div","mono",r.tool+"("+Object.entries(r.args||{}).map(([k,v])=>k+"="+JSON.stringify(v)).join(", ")+") "+String(r.result).slice(0,110)));
    const ok=el("div","",r.allowed===true?"yes":r.allowed==="internal"?"reasoning":"BLOCKED");ok.style.color=r.allowed===true?"#1f7a5c":r.allowed==="internal"?"#6a6860":"#b3261e";ok.style.fontWeight="700";row.append(ok);a.append(row)});
  if(!log.length)a.append(el("div","note","No actions logged yet."));ex.append(a);
  $("#out").append(ex)}
function render(d){
  const o=$("#out");o.replaceChildren();o.append(shopCard(d));o.append(taskCard(d));
  const two=el("div","two");
  const b=el("div","card");b.append(el("h2","","Alert and restart (baseline)"));
  b.append(el("div","big "+(d.baseline.actually_healthy?"good":"bad"),d.baseline.claimed==="healthy"?"Says: healthy":"Says: "+d.baseline.claimed));
  row(b,"Actually healthy?",d.baseline.actually_healthy?"yes":"no",d.baseline.actually_healthy?"good":"bad");
  row(b,"Records lost or corrupted",String(d.baseline.lost_orders),d.baseline.lost_orders?"bad":"");
  row(b,"Still broken",d.baseline.unhealed.join(", ")||"nothing");
  row(b,"Actions taken",String(d.baseline.actions));
  const a=el("div","card");a.append(el("h2","","Recovery agent"));
  const verdict=d.agent.status==="recovered"?"Recovered and verified":"Escalated to an engineer";
  a.append(el("div","big "+(d.agent.correct?"good":"bad"),verdict));
  row(a,"Honest outcome (checked against real system state)",d.agent.correct?"correct":"wrong",d.agent.correct?"good":"bad");
  row(a,"Fixed",d.agent.fixed.join(", ")||"none");
  row(a,"Escalated",d.agent.escalated.join(", ")||"none");
  row(a,"Unrecoverable orders reported",String(d.agent.unrecoverable_orders),d.agent.unrecoverable_orders?"warn":"");
  row(a,"Steps used",String(d.agent.steps));
  Object.entries(d.agent.strategies).forEach(([c,s])=>row(a,"Fix strategy for "+c,s[0]+" ("+s[1]+")"));
  two.append(b,a);o.append(two);
  if(d.agent.status!=="recovered"){const n=el("div","card");n.append(el("h2","","Handoff note for the on-call engineer"));n.append(Object.assign(el("pre"),{textContent:d.agent.summary}));o.append(n)}
  const inv=el("div","card");inv.append(el("h2","","What the checks saw (conservation rules)"));
  const t=el("table");t.innerHTML="<tr><th>Rule</th><th>Before the agent</th><th>After the agent</th></tr>";
  d.invariants_before.forEach((r,i)=>{const af=d.invariants_after[i];const tr=el("tr");tr.append(el("td","",RULE_LABELS[r.name]||r.name));
    [r,af].forEach(x=>{const td=el("td");td.append(el("span","chip "+(x.ok?"c-ok":"c-bad"),x.ok?"ok":x.violations+" violation"+(x.violations===1?"":"s")));tr.append(td)});t.append(tr)});
  inv.append(t);o.append(inv);
  const tr=el("div","card");tr.append(el("h2","","Agent trace: observe, hypothesize, act, verify"));
  d.trace.forEach(s=>{const r=el("div","step");r.append(el("div","num",String(s.step)));const c=el("div");
    c.append(el("div","",s.thought));c.append(el("span","tool",s.tool+"("+Object.entries(s.args||{}).map(([k,v])=>k+"="+JSON.stringify(v)).join(", ")+")"));if(s.allowed){const ch=el("span","chip c-ok","allowed");ch.style.marginLeft="8px";c.append(ch)}if(s.decision)c.append(el("div","dec",s.decision));c.append(el("div","obs",s.obs));r.append(c);tr.append(r)});
  o.append(tr);
  const f=d.forecast,warnSat=f.warnings.find(w=>w.kind==="queue_saturation"),warnDrift=f.warnings.find(w=>w.kind==="loss_drift");
  const firstLoss=f.series.find(p=>p.lost>0);
  const fc=el("div","card");fc.append(el("h2","","Forecast: early warning (monitor only, no action)"));
  const g=el("div","two");
  g.append(chart("Queue depth",f.series,"depth",Math.max(60,...f.series.map(p=>p.depth)),[{tick:warnSat&&warnSat.tick,label:"warning",color:"#a85d00"},{tick:firstLoss&&firstLoss.tick,label:"first loss",color:"#b3261e",dy:14}],warnSat?"Saturation predicted "+warnSat.eta+" orders ahead":"No saturation predicted",f.capacity));
  const mx=Math.max(5,...f.series.map(p=>p.lost));
  g.append(chart("Lost or corrupted records",f.series,"lost",mx,[{tick:warnDrift&&warnDrift.tick,label:"projection",color:"#a85d00"},{tick:firstLoss&&firstLoss.tick,label:"first loss",color:"#b3261e",dy:14}],warnDrift?"Projected "+warnDrift.projected+" lost records 150 orders after the warning":"No loss trend"));
  fc.append(g);o.append(fc);
  const lm=el("div","card");lm.append(el("h2","","Learning: what the agent remembers ("+d.memory.incidents+" past fix"+(d.memory.incidents===1?"":"es")+")"));
  if(d.memory.lessons.length)d.memory.lessons.forEach(l=>lm.append(el("div","row",l)));else lm.append(el("div","note","Nothing learned yet. Run a few different scenarios; it tries each fix strategy once, then keeps the best."));
  o.append(lm);loadExtra()}
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/api/constraints":
            return self._send(200, json.dumps(constraints()))
        if url.path == "/api/summary":
            return self._send(200, json.dumps(summary()))
        if url.path == "/api/audit":
            return self._send(200, json.dumps(read_jsonl(AUDIT_PATH)[-14:][::-1]))
        if url.path == "/audit.jsonl":
            body = open(AUDIT_PATH).read() if os.path.exists(AUDIT_PATH) else ""
            return self._send(200, body, "text/plain; charset=utf-8")
        if url.path == "/api/run":
            q = parse_qs(url.query)
            kinds = [k for k in q.get("faults", [""])[0].split(",") if k in DEFAULTS]
            try:
                seed = int(q.get("seed", ["1"])[0])
                return self._send(200, json.dumps(run_scenario(kinds, seed), default=str))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        global MEMORY
        if self.path == "/api/reset":
            if os.path.exists(MEMORY_PATH):
                os.remove(MEMORY_PATH)
            MEMORY = IncidentMemory(MEMORY_PATH)
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/clear":
            for p in (HISTORY_PATH, AUDIT_PATH):
                if os.path.exists(p):
                    os.remove(p)
            return self._send(200, json.dumps({"ok": True}))
        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    save_constraints()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Open http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()