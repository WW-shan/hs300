import json, time, urllib.request, os, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=900):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def get(path,timeout=120):
    with urllib.request.urlopen(BASE+path,timeout=timeout) as resp: return json.loads(resp.read().decode())
def starts(d0,d1,n):
    d=get(f"/api/kline/daily?symbol=000300.SH&start_date={d0}&end_date={d1}")
    return [x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])][:n]
rec=starts("2025-10-14","2025-10-31",5); full=starts("2018-06-01","2018-06-06",2)
print("recent phases:",rec,"| full phases:",full)
CAND={
 "v1_base":(None,None),
 "M1":({"overnight_ret_20d":1/3,"amplitude_trend_20_60":1/3,"momentum_5d":1/3},{"momentum_5d":"low"}),
 "M2":({"overnight_ret_20d":1/3,"amplitude_trend_20_60":1/3,"ma10_bias":1/3},{"ma10_bias":"low"}),
}
def run(scoring,directions,start,end,commission=None,slippage=None):
    p={"strategy_id":"ai_hs300_multi_factor_rank","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    ov={}
    if scoring is not None: ov["scoring"]=scoring; ov["scoring_replace"]=True
    if directions: ov["scoring_directions"]=directions
    if ov: p["overrides"]=ov
    if commission: p["commission_pct"]=commission
    if slippage: p["slippage_bps"]=slippage
    r=post("/api/backtest/strategy/run",p); return r.get("stats") or {}
out={}
for name,(sc,dr) in CAND.items():
    res={"recent":[],"full":[]}
    t0=time.time()
    for s in rec:
        stt=run(sc,dr,s,"2026-10-14")
        res["recent"].append({"start":s,"ret":stt.get("total_return"),"mdd":stt.get("max_drawdown"),"sharpe":stt.get("sharpe"),"trades":stt.get("n_trades")})
        print(f"  {name} recent {s}: ret={stt.get('total_return'):+.3f} mdd={stt.get('max_drawdown'):+.3f}", flush=True)
    for s in full:
        stt=run(sc,dr,s,"2026-10-14")
        res["full"].append({"start":s,"ret":stt.get("total_return"),"sharpe":stt.get("sharpe"),"mdd":stt.get("max_drawdown")})
        print(f"  {name} full {s}: ret={stt.get('total_return'):+.3f} sharpe={stt.get('sharpe')} mdd={stt.get('max_drawdown'):+.3f}", flush=True)
    stt=run(sc,dr,rec[0],"2026-10-14",commission=0.0004,slippage=10.0)
    res["recent_cost2x"]={"ret":stt.get("total_return"),"mdd":stt.get("max_drawdown"),"sharpe":stt.get("sharpe")}
    rr=[x["ret"] for x in res["recent"]]
    fullstr=", ".join("%+.1f%%" % (x["ret"]*100) for x in res["full"])
    print("[%s] recent min=%+.1f%% med=%+.1f%% max=%+.1f%% allpos=%s | cost2x=%+.1f%% | full=%s (%.0fs)" % (name, min(rr)*100, st.median(rr)*100, max(rr)*100, all(x>0 for x in rr), res["recent_cost2x"]["ret"]*100, fullstr, time.time()-t0), flush=True)
    out[name]=res
json.dump(out, open(os.path.join(os.environ["TEMP"],"r10.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r10.json")
