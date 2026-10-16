import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
C1={"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3}
def run(scoring=C1, maxpos=10, hold=20, start="2021-06-01", end="2026-10-14",
        commission=None, slippage=None, label=""):
    ov={"max_hold_days":hold,"stop_loss":None,"scoring":scoring,"scoring_replace":True}
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":start,"end":end,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":maxpos,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock","overrides":ov}
    if commission is not None: p["commission_pct"]=commission
    if slippage is not None: p["slippage_bps"]=slippage
    t0=time.time(); r=post("/api/backtest/strategy/run",p); el=time.time()-t0
    st=r.get("stats") or {}
    print(f"[{label}] {el:.0f}s ret={st.get('total_return')} mdd={st.get('max_drawdown')} sharpe={st.get('sharpe')} calmar={st.get('calmar')} trades={st.get('n_trades')}")
    r["_elapsed"]=el; r["_label"]=label
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
print("=== K4 parameter grid (C1) ===")
k4={}
for mp in (10,15,20):
    for h in (10,20,30):
        r=run(maxpos=mp,hold=h,label=f"K4 mp={mp} hold={h}")
        k4[f"{mp}x{h}"]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe")}
out["K4"]=k4
print("=== K5 cost stress (commission 4bp, slippage 10bp) ===")
r=run(commission=0.0004,slippage=10.0,label="K5 cost2x")
y=yearly(r.get("equity_curve")); print("   yearly:",{a:round(b,4) for a,b in y.items()})
out["K5"]={"stats":r["stats"],"yearly":y}
print("=== K6 leave-one-out ===")
loo={}
for drop in ("overnight_ret_20d","uf_low_vol","uf_small_mv"):
    sc={k:v for k,v in C1.items() if k!=drop}
    r=run(scoring=sc,label=f"K6 drop {drop}")
    loo[drop]={"ret":r["stats"].get("total_return"),"sharpe":r["stats"].get("sharpe"),"mdd":r["stats"].get("max_drawdown")}
out["K6"]=loo
print("=== K7 rolling starts ===")
k7={}
for start in ("2022-01-01","2023-01-01","2024-01-01","2025-01-01"):
    r=run(start=start,label=f"K7 from {start}")
    k7[start]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe")}
out["K7"]=k7
json.dump(out, open(os.path.join(os.environ["TEMP"],"r3_step2.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r3_step2.json")
