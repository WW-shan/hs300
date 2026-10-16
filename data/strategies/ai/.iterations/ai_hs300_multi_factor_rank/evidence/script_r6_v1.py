import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
V2={"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
START, END = "2018-06-01", "2021-06-01"
def run(scoring=None, commission=None, slippage=None, label=""):
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":START,"end":END,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":10,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock"}
    if scoring is not None:
        p["overrides"]={"scoring":scoring,"scoring_replace":True}
    if commission is not None: p["commission_pct"]=commission
    if slippage is not None: p["slippage_bps"]=slippage
    t0=time.time(); r=post("/api/backtest/strategy/run",p); el=time.time()-t0
    st=r.get("stats") or {}
    print(f"[{label}] {el:.0f}s ret={st.get('total_return')} mdd={st.get('max_drawdown')} sharpe={st.get('sharpe')} calmar={st.get('calmar')} trades={st.get('n_trades')} win={st.get('win_rate')} PF={st.get('profit_factor')}")
    r["_label"]=label
    return r
def yearly(curve):
    by={}
    for x in curve or []:
        d=str(x.get("date"))[:10]; v=x.get("value")
        if v is None: v=x.get("close")
        if v is None: continue
        by.setdefault(d[:4],[]).append(v)
    out={}; prev=None
    for y in sorted(by):
        sv=prev if prev is not None else by[y][0]
        out[y]=by[y][-1]/sv-1; prev=by[y][-1]
    return out
out={}
print("=== V1a/b/c v2 untouched 2018-06-01..2021-06-01 ===")
r=run(label="V1 v2")
y=yearly(r.get("equity_curve")); out["v2"]={"stats":r["stats"],"yearly":{k:round(v,4) for k,v in y.items()}}
print("   yearly:",{k:round(v,4) for k,v in y.items()})
print("=== V1d leave-one-out ===")
loo={}
for drop in ("uf_low_amount","uf_low_vol","uf_small_mv"):
    sc={k:v for k,v in V2.items() if k!=drop}
    rr=run(scoring=sc,label=f"V1d drop {drop}")
    loo[drop]={"ret":rr["stats"].get("total_return"),"sharpe":rr["stats"].get("sharpe"),"mdd":rr["stats"].get("max_drawdown"),"yearly":{k:round(v,4) for k,v in yearly(rr.get("equity_curve")).items()}}
out["loo"]=loo
print("=== V1e cost 2x ===")
rr=run(commission=0.0004,slippage=10.0,label="V1e cost2x")
out["cost2x"]={"stats":rr["stats"],"yearly":{k:round(v,4) for k,v in yearly(rr.get("equity_curve")).items()}}
print("   yearly:",out["cost2x"]["yearly"])
# benchmark: 000300.SH same window
u=f"{BASE}/api/kline/daily?symbol=000300.SH&start_date={START}&end_date={END}"
with urllib.request.urlopen(u,timeout=120) as resp: d=json.loads(resp.read().decode("utf-8"))
rows=sorted(d.get("rows") or [], key=lambda x:x["date"])
by={}
for x in rows: by.setdefault(str(x["date"])[:4],[]).append(x["close"])
prev=None; bm={}
for yy in sorted(by):
    sv=prev if prev is not None else by[yy][0]
    bm[yy]=round(by[yy][-1]/sv-1,4); prev=by[yy][-1]
bm_full=rows[-1]["close"]/rows[0]["close"]-1
print("000300.SH window:", rows[0]["date"],"..",rows[-1]["date"], "n=",len(rows), "full=",round(bm_full,4), bm)
out["benchmark"]={"full":bm_full,"yearly":bm,"n":len(rows)}
json.dump(out, open(os.path.join(os.environ["TEMP"],"r6_v1.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r6_v1.json")
