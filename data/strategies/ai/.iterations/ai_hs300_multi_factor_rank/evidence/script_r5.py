import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
def run(maxpos=10, hold=20, start="2021-06-01", end="2026-10-14",
        commission=None, slippage=None, scoring=None, label=""):
    ov={"max_hold_days":hold,"stop_loss":None}
    if scoring is not None:
        ov["scoring"]=scoring; ov["scoring_replace"]=True
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
V2={"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
out={}
print("=== A1 v2 parameter grid ===")
a1={}
for mp in (10,15,20):
    for h in (10,20,30):
        r=run(maxpos=mp,hold=h,label=f"A1 mp={mp} hold={h}")
        a1[f"{mp}x{h}"]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe"),"calmar":r["stats"].get("calmar")}
out["A1"]=a1
print("=== A2 v2 cost 2x ===")
r=run(commission=0.0004,slippage=10.0,label="A2 cost2x"); y=yearly(r.get("equity_curve"))
print("   yearly:",{a:round(b,4) for a,b in y.items()})
out["A2"]={"stats":r["stats"],"yearly":y}
print("=== A3 v2 leave-one-out ===")
a3={}
for drop in ("uf_low_amount","uf_low_vol","uf_small_mv"):
    sc={k:v for k,v in V2.items() if k!=drop}
    r=run(scoring=sc,label=f"A3 drop {drop}")
    a3[drop]={"ret":r["stats"].get("total_return"),"sharpe":r["stats"].get("sharpe"),"mdd":r["stats"].get("max_drawdown")}
out["A3"]=a3
print("=== A4 v2 rolling starts ===")
a4={}
for start in ("2022-01-01","2023-01-01","2024-01-01","2025-01-01"):
    r=run(start=start,label=f"A4 from {start}")
    a4[start]={"ret":r["stats"].get("total_return"),"mdd":r["stats"].get("max_drawdown"),"sharpe":r["stats"].get("sharpe"),"calmar":r["stats"].get("calmar")}
out["A4"]=a4
json.dump(out, open(os.path.join(os.environ["TEMP"],"r5.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r5.json")
