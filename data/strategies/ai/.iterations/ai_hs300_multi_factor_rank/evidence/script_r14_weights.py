import os
import json, time, urllib.request
BASE = "http://127.0.0.1:3018"
TOKEN = os.environ.get("TF_SESSION", "")

def post(path, payload, timeout=3600):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"tf_session={TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def get(path, timeout=120):
    req = urllib.request.Request(BASE + path, headers={"Cookie": f"tf_session={TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def run_raw(strategy_id="ai_hs300_lowrisk_alpha", start="2018-06-01", end="2026-10-14",
            maxpos=10, overrides=None, commission=None, slippage=None, holding_days=5):
    p = {"strategy_id": strategy_id, "start": start, "end": end, "matching": "open_t+1",
         "mode": "position", "hs300": True, "max_positions": maxpos,
         "position_sizing": "equal", "holding_days": holding_days, "asset_type": "stock"}
    if overrides is not None: p["overrides"] = overrides
    if commission is not None: p["commission_pct"] = commission
    if slippage is not None: p["slippage_bps"] = slippage
    t0 = time.time()
    r = post("/api/backtest/strategy/run", p)
    return r, time.time() - t0

def run_bt(**kw):
    r, el = run_raw(**kw)
    s = r.get("stats") or {}
    fa = (r.get("factor_attribution") or {}).get("factors") or []
    return s, [f.get("factor") for f in fa], r.get("error"), el, r

import json, time, statistics as st
import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH").select(["date","close"]).collect().sort("date")
         .with_columns(pl.col("date").cast(pl.Utf8)))
P10 = IDX.filter(pl.col("date") >= "2018-06-01").head(10)["date"].to_list()
BASE = [0.34, 0.33, 0.33]
NAMES = ["uf_low_amount","uf_small_mv","uf_low_vol"]
HOLD = 35   # R15 起的新默认; 报告项

def run(scoring):
    o={"scoring":scoring,"scoring_replace":True,"max_hold_days":HOLD,"stop_loss":None,
       "basic_filter":{"amount_min":0.5e8}}
    recs=[]
    for ph in P10:
        s,fac,err,el,raw = run_bt(start=ph, maxpos=10, overrides=o)
        if err: raise RuntimeError(err)
        recs.append((s.get("sharpe"), s.get("total_return"), s.get("max_drawdown")))
    return recs

out={"hold":HOLD,"base":dict(zip(NAMES,BASE)),"runs":{}}
t0=time.time()
base_rec = run(dict(zip(NAMES,BASE)))
out["runs"]["equal_0.34/0.33/0.33"]={"scoring":dict(zip(NAMES,BASE)),
    "sharpe_mean":st.mean(x[0] for x in base_rec),"ret_mean":st.mean(x[1] for x in base_rec),"mdd_mean":st.mean(x[2] for x in base_rec)}
print("base:", round(out['runs']['equal_0.34/0.33/0.33']['sharpe_mean'],3), flush=True)
for f in (0.05, 0.10):
    for i in range(3):
        w = list(BASE); w[i] += f; w[(i+1) % 3] -= f
        tag = f"w{i}+{f:.2f} ({NAMES[i]}↑ {NAMES[(i+1)%3]}↓)"
        recs = run(dict(zip(NAMES, [round(x,4) for x in w])))
        out["runs"][tag]={"scoring":dict(zip(NAMES,[round(x,4) for x in w])),
            "sharpe_mean":st.mean(x[0] for x in recs),"ret_mean":st.mean(x[1] for x in recs),
            "mdd_mean":st.mean(x[2] for x in recs)}
        print(f"  {tag}: sharpe {out['runs'][tag]['sharpe_mean']:.3f} ret {out['runs'][tag]['ret_mean']*100:+.0f}% mdd {out['runs'][tag]['mdd_mean']*100:.1f}% ({time.time()-t0:.0f}s)", flush=True)
json.dump(out, open(EV+"r14_weight_perturbation.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r14_weight_perturbation.json")
