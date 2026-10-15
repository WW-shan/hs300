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

import os, json, time, statistics as st
import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH").select(["date","close"]).collect()
         .sort("date").with_columns(pl.col("date").cast(pl.Utf8)))
P10 = IDX.filter(pl.col("date") >= "2018-06-01").head(10)["date"].to_list()

D4 = {"uf_low_amount":0.25,"uf_small_mv":0.25,"uf_low_vol":0.25,"momentum_10d":0.25}
DIRS = {"momentum_10d":"low"}
V2 = {"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}

def run(scoring, dirs, mp, hold, phases, am=0.5e8, commission=None, slippage=None):
    o = {"scoring": scoring, "scoring_replace": True, "max_hold_days": hold, "stop_loss": None,
         "basic_filter": {"amount_min": am}}
    if dirs: o["scoring_directions"] = dirs
    out=[]
    for ph in phases:
        s, fac, err, el, raw = run_bt(start=ph, maxpos=mp, overrides=o, commission=commission, slippage=slippage)
        if err: raise RuntimeError(err)
        out.append({"phase": ph, "ret": s.get("total_return"), "mdd": s.get("max_drawdown"),
                    "sharpe": s.get("sharpe"), "trades": s.get("n_trades"), "curve": raw.get("equity_curve")})
    return out

# R14-R1: D4 探索网格
grid = {"phases": P10, "cells": {}}
t0=time.time()
for mp in [8,10,12,15,20]:
    for hold in [10,20,30]:
        recs = run(D4, DIRS, mp, hold, P10)
        k=f"mp{mp}_h{hold}"
        grid["cells"][k]={"sharpe_mean": st.mean(x["sharpe"] for x in recs),
                          "sharpe_min": min(x["sharpe"] for x in recs),
                          "mdd_mean": st.mean(x["mdd"] for x in recs),
                          "ret_mean": st.mean(x["ret"] for x in recs),
                          "trades_mean": st.mean(x["trades"] for x in recs)}
        print(f"D4 {k}: ret {grid['cells'][k]['ret_mean']*100:+.0f}% sharpe {grid['cells'][k]['sharpe_mean']:.3f} mdd {grid['cells'][k]['mdd_mean']*100:.1f}% ({time.time()-t0:.0f}s)", flush=True)
        json.dump(grid, open(EV+"r14_d4_grid.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

# R14-R2: 相关性 (同相位 2018-06-01 全历史)
b = run(V2, {}, 10, 20, ["2018-06-01"])[0]["curve"]
d4 = run(D4, DIRS, 10, 20, ["2018-06-01"])[0]["curve"]
def series(curve):
    return [round(x["value"],6) for x in curve]
vb, vd = series(b), series(d4)
rb = [vb[i]/vb[i-1]-1 for i in range(1,len(vb))]
rd = [vd[i]/vd[i-1]-1 for i in range(1,len(vd))]
n=min(len(rb),len(rd))
corr = pl.DataFrame({"a":rb[:n],"b":rd[:n]}).select(pl.corr("a","b")).item()
corr_s = pl.DataFrame({"a":rb[:n],"b":rd[:n]}).select(pl.corr("a","b", method="spearman")).item()
out={"corr_pearson":corr,"corr_spearman":corr_s,"n":n}
json.dump(out, open(EV+"r14_d4_corr.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("R14-R2 相关性:", out)
print("saved")
