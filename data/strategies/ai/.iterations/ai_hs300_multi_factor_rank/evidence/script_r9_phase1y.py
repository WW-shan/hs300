import json, time, urllib.request, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
with urllib.request.urlopen(BASE+"/api/kline/daily?symbol=000300.SH&start_date=2025-10-14&end_date=2025-10-24",timeout=60) as r:
    days=[x["date"] for x in sorted(json.loads(r.read().decode()).get("rows") or [], key=lambda x:x["date"])][:5]
res=[]
for s in days:
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":s,"end":"2026-10-14","matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    d=post("/api/backtest/strategy/run",p); stt=d.get("stats") or {}
    res.append(stt.get("total_return")); print(f"{s}: ret={stt.get('total_return'):+.3f} mdd={stt.get('max_drawdown'):+.3f} sharpe={stt.get('sharpe')}")
print(f"nearby-start spread: min={min(res):+.3f} max={max(res):+.3f} mean={st.mean(res):+.3f}  (HS300 same window -0.071)")
