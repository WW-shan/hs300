import json, time, urllib.request, os, statistics as st, math
BASE="http://localhost:3018"
def post(path,payload,timeout=900):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def get(path,timeout=120):
    with urllib.request.urlopen(BASE+path,timeout=timeout) as resp: return json.loads(resp.read().decode())
d=get("/api/kline/daily?symbol=000300.SH&start_date=2018-06-01&end_date=2018-06-20")
phases=[x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])][:10]
def run(th,start,end="2026-10-14"):
    p={"strategy_id":"ai_hs300_lowrisk_gated","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock",
       "params":{"breadth_min":th}}
    return post("/api/backtest/strategy/run",p)
out={}
for th in (0.5,0.6):
    res=[]; t0=time.time()
    for s in phases:
        r=run(th,s); sst=r.get("stats") or {}
        res.append({"start":s,"ret":sst.get("total_return"),"sharpe":sst.get("sharpe"),"mdd":sst.get("max_drawdown"),"calmar":sst.get("calmar")})
    rr=[x["ret"] for x in res]; shs=[x["sharpe"] for x in res]; mdds=[x["mdd"] for x in res]
    print("[gate=%s] 10相位: ret mean %+.0f%% [%+.0f%%, %+.0f%%] sharpe mean %.2f [%.2f, %.2f] mdd mean %.0f%% (%.0fs)" % (
        th, st.mean(rr)*100, min(rr)*100, max(rr)*100, st.mean(shs), min(shs), max(shs), st.mean(mdds)*100, time.time()-t0), flush=True)
    out[str(th)]=res
json.dump(out, open(os.path.join(os.environ["TEMP"],"r11_gate10.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved")
