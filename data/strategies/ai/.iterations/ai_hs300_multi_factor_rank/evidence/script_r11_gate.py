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
def run(th,start,end,commission=None,slippage=None):
    p={"strategy_id":"ai_hs300_lowrisk_gated","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock",
       "params":{"breadth_min":th}}
    if commission: p["commission_pct"]=commission
    if slippage: p["slippage_bps"]=slippage
    r=post("/api/backtest/strategy/run",p); return r.get("stats") or {}
out={}
for th in (0.35,0.5,0.6):
    res={"recent":[],"full":[]}
    t0=time.time()
    for s in rec:
        stt=run(th,s,"2026-10-14"); res["recent"].append({"start":s,"ret":stt.get("total_return"),"mdd":stt.get("max_drawdown"),"trades":stt.get("n_trades")})
    for s in full:
        stt=run(th,s,"2026-10-14"); res["full"].append({"start":s,"ret":stt.get("total_return"),"sharpe":stt.get("sharpe"),"mdd":stt.get("max_drawdown"),"calmar":stt.get("calmar")})
    stt=run(th,rec[0],"2026-10-14",commission=0.0004,slippage=10.0)
    res["cost2x"]={"ret":stt.get("total_return"),"mdd":stt.get("max_drawdown")}
    rr=[x["ret"] for x in res["recent"]]
    print("[gate=%s] recent: %s | med=%+.1f%% allpos=%s | cost2x=%+.1f%% | full: %s | mdds=%s (%.0fs)" % (
        th, ", ".join("%+.1f%%"%(x["ret"]*100) for x in res["recent"]), st.median(rr)*100, all(x>0 for x in rr),
        res["cost2x"]["ret"]*100, ", ".join("%+.0f%%(sh %.2f, mdd %.0f%%)"%(x["ret"]*100,x["sharpe"],x["mdd"]*100) for x in res["full"]),
        ", ".join("%.0f%%"%(x["mdd"]*100) for x in res["recent"]), time.time()-t0), flush=True)
    out[str(th)]=res
json.dump(out, open(os.path.join(os.environ["TEMP"],"r11_gate.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r11_gate.json")
