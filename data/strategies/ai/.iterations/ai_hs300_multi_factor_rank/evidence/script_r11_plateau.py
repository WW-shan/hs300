import json, time, urllib.request, os, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=900):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def run(th,start,end="2026-10-14",scoring=None,stop=None,label=""):
    p={"strategy_id":"ai_hs300_lowrisk_gated","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock",
       "params":{"breadth_min":th}}
    ov={}
    if scoring: ov["scoring"]=scoring; ov["scoring_replace"]=True
    if stop is not None: ov["stop_loss"]=stop
    if ov: p["overrides"]=ov
    r=post("/api/backtest/strategy/run",p); s=r.get("stats") or {}
    print("[%s] ret=%+.1f%% sharpe=%.2f mdd=%.1f%% calmar=%.2f trades=%s" % (label, (s.get("total_return") or 0)*100, s.get("sharpe") or 0, (s.get("max_drawdown") or 0)*100, s.get("calmar") or 0, s.get("n_trades")), flush=True)
    return s
print("=== 阈值平台检查 (v2因子+闸门, 全历史 2018-06-01起) ===")
for th in (0.4,0.45,0.55,0.65,0.7):
    run(th,"2018-06-01",label=f"gate={th}")
print("=== v1因子 + 闸门(0.6) + 止损-0.20 ===")
V1={"overnight_ret_20d":0.5,"amplitude_trend_20_60":0.5}
run(0.6,"2025-10-14",scoring=V1,stop=-0.20,label="v1+gate recent")
run(0.6,"2018-06-01",scoring=V1,stop=-0.20,label="v1+gate full")
print("=== v1因子 + 闸门(0.5) ===")
run(0.5,"2025-10-14",scoring=V1,stop=-0.20,label="v1+gate0.5 recent")
run(0.5,"2018-06-01",scoring=V1,stop=-0.20,label="v1+gate0.5 full")
