import json, urllib.request
BASE="http://localhost:3018"
def post(path,payload,timeout=900):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def run(params,label,start="2025-10-14",end="2026-10-14"):
    p={"strategy_id":"ai_hs300_lowrisk_gated","start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock","params":params}
    r=post("/api/backtest/strategy/run",p); s=r.get("stats") or {}
    print(f"[{label}] ret={s.get('total_return')} mdd={s.get('max_drawdown')} trades={s.get('n_trades')} err={r.get('error')}")
    return r
run({"breadth_min":0.0},"gate=0.0 (期望≈无闸门)")
run({"breadth_min":0.35},"gate=0.35")
run({"breadth_min":0.6},"gate=0.60")
