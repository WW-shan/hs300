import json, time, urllib.request, os, math
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))

C1={"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3}
C2={"overnight_ret_20d":0.25,"uf_low_vol":0.25,"uf_small_mv":0.25,"uf_low_amount":0.25}
V2=None  # use file default

def run(scoring=None, maxpos=10, hold=20, start="2021-06-01", end="2026-10-14",
        commission=None, slippage=None, amount_min=None, label=""):
    ov={"max_hold_days":hold,"stop_loss":None}
    if scoring is not None:
        ov["scoring"]=scoring; ov["scoring_replace"]=True
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":start,"end":end,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":maxpos,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock","overrides":ov}
    if commission is not None: p["commission_pct"]=commission
    if slippage is not None: p["slippage_bps"]=slippage
    if amount_min is not None:
        ov["basic_filter"]={**({"amount_min":amount_min})}
    t0=time.time()
    r=post("/api/backtest/strategy/run",p)
    el=time.time()-t0
    r["_elapsed"]=el; r["_label"]=label
    st=r.get("stats") or {}
    print(f"[{label}] {el:.1f}s ret={st.get('total_return')} mdd={st.get('max_drawdown')} sharpe={st.get('sharpe')} calmar={st.get('calmar')} win={st.get('win_rate')} PF={st.get('profit_factor')} trades={st.get('n_trades')} worst={st.get('worst_trade') or st.get('worst')}")
    return r

def yearly(curve, value_key="value"):
    by={}
    for x in curve or []:
        d=str(x.get("date"))[:10]; v=x.get(value_key)
        if v is None: v=x.get("close")
        if v is None: continue
        by.setdefault(d[:4],[]).append(v)
    out={}; prev=None
    for y in sorted(by):
        sv=prev if prev is not None else by[y][0]
        out[y]=by[y][-1]/sv-1; prev=by[y][-1]
    return out

results={}
for k,sc in (("C1",C1),("C2",C2),("v2",V2)):
    r=run(sc, label=k)
    results[k]=r
    y=yearly(r.get("equity_curve"))
    b=yearly(r.get("benchmark_curve"))
    print("   yearly equity:", {a:round(b_,4) for a,b_ in y.items()})
    print("   yearly bm    :", {a:round(b_,4) for a,b_ in b.items()})
    exc={k2:round(y[k2]-b[k2],4) for k2 in y if k2 in b}
    print("   yearly excess:", exc)
json.dump(results, open(os.path.join(os.environ["TEMP"],"r3_step1.json"),"w",encoding="utf-8"), ensure_ascii=False)
print("saved r3_step1.json")
