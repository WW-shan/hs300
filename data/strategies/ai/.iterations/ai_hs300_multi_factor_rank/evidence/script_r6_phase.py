import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
def run(start,end,label="",hold=None):
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":start,"end":end,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":10,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock"}
    if hold is not None: p["overrides"]={"max_hold_days":hold,"stop_loss":None}
    r=post("/api/backtest/strategy/run",p); st=r.get("stats") or {}
    print(f"[{label}] ret={st.get('total_return')} mdd={st.get('max_drawdown')} sharpe={st.get('sharpe')} trades={st.get('n_trades')}")
    r["_label"]=label; return r
out={}
print("=== data-integrity: rerun R5 main config post-backfill ===")
r=run("2021-06-01","2026-10-14","R5-main-recheck",hold=20)
out["recheck"]={"stats":r["stats"]}
def yearly(curve):
    by={}
    for x in curve or []:
        d=str(x.get("date"))[:10]; v=x.get("value") or x.get("close")
        if v is None: continue
        by.setdefault(d[:4],[]).append(v)
    o={}; prev=None
    for y in sorted(by):
        sv=prev if prev is not None else by[y][0]
        o[y]=round(by[y][-1]/sv-1,4); prev=by[y][-1]
    return o
print("   yearly:",yearly(r.get("equity_curve")))
out["recheck"]["yearly"]=yearly(r.get("equity_curve"))
# trading-day starts lookup
import urllib.request as u2
def days(d0,d1):
    with u2.urlopen(f"{BASE}/api/kline/daily?symbol=000300.SH&start_date={d0}&end_date={d1}",timeout=120) as resp:
        d=json.loads(resp.read().decode("utf-8"))
    return [x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])]
t22=days("2022-01-01","2022-01-31")[:10]
t25=days("2025-01-01","2025-01-31")[:10]
print("2022 starts:",t22)
print("2025 starts:",t25)
for tag,starts,end in (("2022",t22,"2022-12-30"),("2025",t25,"2025-12-31")):
    res=[]
    for s in starts:
        rr=run(s,end,f"{tag} start={s}")
        st=rr["stats"]
        res.append({"start":s,"ret":st.get("total_return"),"mdd":st.get("max_drawdown"),"sharpe":st.get("sharpe")})
    rets=[x["ret"] for x in res]
    print(f"   phase spread {tag}: min={min(rets):+.3f} max={max(rets):+.3f} mean={sum(rets)/len(rets):+.3f}")
    out[f"phase_{tag}"]=res
json.dump(out, open(os.path.join(os.environ["TEMP"],"r6_phase.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r6_phase.json")
