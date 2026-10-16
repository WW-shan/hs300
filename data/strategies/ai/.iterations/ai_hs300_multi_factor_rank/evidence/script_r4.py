import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
C1={"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3}
def run(scoring=C1, maxpos=10, hold=30, start="2021-06-01", end="2026-10-14",
        commission=None, slippage=None, label=""):
    ov={"max_hold_days":hold,"stop_loss":None,"scoring":scoring,"scoring_replace":True}
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":start,"end":end,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":maxpos,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock","overrides":ov}
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
HS={"2021":-0.0751,"2022":-0.2163,"2023":-0.1138,"2024":0.1468,"2025":0.1766,"2026":-0.0890}
out={}
print("=== R4 main: C1 hold=30 ===")
r=run(label="main 10x30"); y=yearly(r.get("equity_curve"))
print("   yearly:",{a:round(b,4) for a,b in y.items()})
print("   excess(vs HS300):",{a:round(y[a]-HS[a],4) for a in y})
out["main"]={"stats":r["stats"],"yearly":y,"excess":{a:y[a]-HS[a] for a in y}}
print("=== L4 cost 2x @hold30 ===")
r=run(commission=0.0004,slippage=10.0,label="L4 cost2x"); y=yearly(r.get("equity_curve"))
print("   yearly:",{a:round(b,4) for a,b in y.items()})
out["L4"]={"stats":r["stats"],"yearly":y}
print("=== L5 platform new cells hold=40 ===")
l5={}
for mp in (10,15):
    r=run(maxpos=mp,hold=40,label=f"L5 mp={mp} hold=40")
    l5[f"{mp}x40"]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe")}
out["L5_new"]=l5
print("=== L6 LOO @hold30 ===")
l6={}
for drop in ("overnight_ret_20d","uf_low_vol","uf_small_mv"):
    sc={k:v for k,v in C1.items() if k!=drop}
    r=run(scoring=sc,label=f"L6 drop {drop}")
    l6[drop]={"ret":r["stats"].get("total_return"),"sharpe":r["stats"].get("sharpe"),"mdd":r["stats"].get("max_drawdown")}
out["L6"]=l6
print("=== L7 rolling starts @hold30 ===")
l7={}
for start in ("2022-01-01","2023-01-01","2024-01-01","2025-01-01"):
    r=run(start=start,label=f"L7 from {start}")
    l7[start]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe")}
out["L7"]=l7
print("=== L8 weight perturbation @hold30 (report only) ===")
l8={}
for name,sc in (("overnight-heavy","OV"),("risk-heavy","RH")):
    if name=="overnight-heavy": s={"overnight_ret_20d":0.5,"uf_low_vol":0.25,"uf_small_mv":0.25}
    else: s={"overnight_ret_20d":0.2,"uf_low_vol":0.4,"uf_small_mv":0.4}
    r=run(scoring=s,label=f"L8 {name}"); y=yearly(r.get("equity_curve"))
    print("   yearly:",{a:round(b,4) for a,b in y.items()})
    l8[name]={"stats":r["stats"],"yearly":y}
out["L8"]=l8
json.dump(out, open(os.path.join(os.environ["TEMP"],"r4.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r4.json")
