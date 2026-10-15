import json, time, urllib.request, os, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=900):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def get(path,timeout=120):
    with urllib.request.urlopen(BASE+path,timeout=timeout) as resp: return json.loads(resp.read().decode())
d=get("/api/kline/daily?symbol=000300.SH&start_date=2025-10-14&end_date=2025-10-31")
rec=[x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])][:5]
def run(maxpos,start,end="2026-10-14",commission=None,slippage=None):
    p={"strategy_id":"ai_hs300_multi_factor_rank","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":maxpos,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    if commission: p["commission_pct"]=commission
    if slippage: p["slippage_bps"]=slippage
    r=post("/api/backtest/strategy/run",p); return r.get("stats") or {}
for mp in (15,20):
    res=[]; t0=time.time()
    for s in rec:
        stt=run(mp,s); res.append({"start":s,"ret":stt.get("total_return"),"mdd":stt.get("max_drawdown")})
    stt=run(mp,rec[0],commission=0.0004,slippage=10.0)
    sttfull=run(mp,"2018-06-01")
    rr=[x["ret"] for x in res]
    print("[v1 maxpos=%d] recent: %s | med=%+.1f%% allpos=%s | cost2x=%+.1f%% | full18-26: %+.0f%% sharpe=%.2f mdd=%.0f%% (%.0fs)" % (
        mp, ", ".join("%+.1f%%"%(x["ret"]*100) for x in res), st.median(rr)*100, all(x>0 for x in rr),
        stt.get("total_return")*100, sttfull.get("total_return")*100, sttfull.get("sharpe") or 0, (sttfull.get("max_drawdown") or 0)*100, time.time()-t0), flush=True)
