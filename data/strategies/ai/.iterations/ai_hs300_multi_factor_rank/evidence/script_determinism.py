import json, urllib.request, os
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
p={"strategy_id":"ai_hs300_lowrisk_alpha","start":"2021-06-01","end":"2026-10-14","matching":"open_t+1",
   "mode":"position","hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock",
   "overrides":{"max_hold_days":20,"stop_loss":None}}
outs=[]
for i in range(3):
    r=post("/api/backtest/strategy/run",p); st=r["stats"]
    outs.append((st.get("total_return"),st.get("max_drawdown"),st.get("sharpe"),st.get("n_trades")))
    print(f"run{i}: ret={st.get('total_return'):.4f} mdd={st.get('max_drawdown'):.4f} sharpe={st.get('sharpe')} trades={st.get('n_trades')}")
print("identical:", len(set(outs))==1)
