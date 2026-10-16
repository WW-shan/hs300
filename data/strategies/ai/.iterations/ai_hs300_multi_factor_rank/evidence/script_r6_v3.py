import json, time, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
def run(sid="ai_hs300_lowrisk_alpha", scoring=None, start="2018-06-01", end="2026-10-14",
        commission=None, slippage=None, maxpos=10, hold=None, label=""):
    p={"strategy_id":sid,"start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":maxpos,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    ov={}
    if scoring is not None: ov["scoring"]=scoring; ov["scoring_replace"]=True
    if hold is not None: ov["max_hold_days"]=hold
    if ov: p["overrides"]=ov
    if commission is not None: p["commission_pct"]=commission
    if slippage is not None: p["slippage_bps"]=slippage
    t0=time.time(); r=post("/api/backtest/strategy/run",p); el=time.time()-t0
    st=r.get("stats") or {}
    print(f"[{label}] {el:.0f}s ret={st.get('total_return')} ann={st.get('annual_return')} mdd={st.get('max_drawdown')} sharpe={st.get('sharpe')} calmar={st.get('calmar')} trades={st.get('n_trades')} win={st.get('win_rate')} PF={st.get('profit_factor')} exposure={st.get('avg_exposure')}")
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
print("=== v2 full 2018-06-01..2026-10-14 ===")
r=run(label="v2 8.3y"); y=yearly(r.get("equity_curve")); out["v2_full"]={"stats":r["stats"],"yearly":{k:round(v,4) for k,v in y.items()}}
print("   yearly:",{k:round(v,4) for k,v in y.items()})
print("=== v2 full 8.3y cost 2x ===")
rc=run(commission=0.0004,slippage=10.0,label="v2 8.3y cost2x"); out["v2_full_cost2x"]={"stats":rc["stats"],"yearly":{k:round(v,4) for k,v in yearly(rc.get("equity_curve")).items()}}
print("   yearly:",out["v2_full_cost2x"]["yearly"])
print("=== C1@20 on 2018-06-01..2021-06-01 (report) ===")
C1={"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3}
r1=run(scoring=C1,start="2018-06-01",end="2021-06-01",label="C1 early window")
out["C1_early"]={"stats":r1["stats"],"yearly":{k:round(v,4) for k,v in yearly(r1.get("equity_curve")).items()}}
print("   yearly:",out["C1_early"]["yearly"])
print("=== v1 momentum on 2018-06-01..2021-06-01 (report) ===")
r2=run(sid="ai_hs300_multi_factor_rank",start="2018-06-01",end="2021-06-01",label="v1 early window")
out["v1_early"]={"stats":r2["stats"],"yearly":{k:round(v,4) for k,v in yearly(r2.get("equity_curve")).items()}}
print("   yearly:",out["v1_early"]["yearly"])
# benchmark for full window
u=f"{BASE}/api/kline/daily?symbol=000300.SH&start_date=2018-06-01&end_date=2026-10-14"
with urllib.request.urlopen(u,timeout=120) as resp: d=json.loads(resp.read().decode("utf-8"))
rows=sorted(d.get("rows") or [], key=lambda x:x["date"])
by={}
for x in rows: by.setdefault(str(x["date"])[:4],[]).append(x["close"])
prev=None; bm={}
for yy in sorted(by):
    sv=prev if prev is not None else by[yy][0]
    bm[yy]=round(by[yy][-1]/sv-1,4); prev=by[yy][-1]
print("000300.SH full:",rows[0]["date"],"..",rows[-1]["date"],"n=",len(rows),"full=",round(rows[-1]["close"]/rows[0]["close"]-1,4))
print("   yearly:",bm)
out["benchmark_full"]={"full":rows[-1]["close"]/rows[0]["close"]-1,"yearly":bm}
json.dump(out, open(os.path.join(os.environ["TEMP"],"r6_v3.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r6_v3.json")
