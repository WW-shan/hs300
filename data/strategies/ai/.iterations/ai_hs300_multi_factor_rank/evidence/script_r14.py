import sys, statistics as st
import polars as pl
import json, time, os, urllib.request

BASE = "http://127.0.0.1:3018"
# 本地研究脚本需要一个已登录会话的 cookie: 运行前 `export TF_SESSION=<tf_session cookie>`
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


EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH").select(["date","close"]).collect()
         .sort("date").with_columns(pl.col("date").cast(pl.Utf8)))
P10 = IDX.filter(pl.col("date") >= "2018-06-01").head(10)["date"].to_list()
P1Y = IDX.filter(pl.col("date") >= "2025-10-14").head(5)["date"].to_list()
bm = {}
for dte, close in IDX.filter(pl.col("date") >= "2018-05-31").select(["date","close"]).iter_rows():
    bm.setdefault(str(dte)[:4], []).append(close)
BM = {}; prev = None
for y in sorted(bm):
    sv = prev if prev is not None else bm[y][0]
    BM[y] = bm[y][-1]/sv - 1; prev = bm[y][-1]

def yearly(curve):
    by = {}
    for x in curve or []:
        d = str(x.get("date"))[:10]; v = x.get("value")
        if v is None: v = x.get("close")
        if v is None: continue
        by.setdefault(d[:4], []).append(v)
    out = {}; prev = None
    for y in sorted(by):
        sv = prev if prev is not None else by[y][0]
        out[y] = by[y][-1]/sv - 1; prev = by[y][-1]
    return out

def agg_phases(recs):
    return {"ret_mean": st.mean([r["ret"] for r in recs]),
            "sharpe_mean": st.mean([r["sharpe"] for r in recs]),
            "sharpe_min": min(r["sharpe"] for r in recs),
            "mdd_mean": st.mean([r["mdd"] for r in recs]),
            "mdd_worst": min(r["mdd"] for r in recs),
            "trades_mean": st.mean([r["trades"] for r in recs])}

def run_cell(scoring, dirs, phases, maxpos=10, hold=20, amount_min=None, commission=None, slippage=None):
    o = {"scoring": scoring, "scoring_replace": True, "max_hold_days": hold, "stop_loss": None}
    if dirs: o["scoring_directions"] = dirs
    if amount_min is not None: o["basic_filter"] = {"amount_min": amount_min}
    recs = []
    for ph in phases:
        s, fac, err, el, raw = run_bt(start=ph, maxpos=maxpos, overrides=o,
                                          commission=commission, slippage=slippage)
        if err: raise RuntimeError(f"{ph} mp={maxpos} hold={hold} am={amount_min}: {err}")
        y = yearly(raw.get("equity_curve"))
        recs.append({"phase": ph, "ret": s.get("total_return"), "mdd": s.get("max_drawdown"),
                     "sharpe": s.get("sharpe"), "trades": s.get("n_trades"),
                     "yearly": {k: round(v,4) for k,v in y.items()}, "factors": fac})
    return recs

# ---------------- 层 A: 指标窗口参数 ----------------
if sys.argv[1] == "layerA":
    SM = "uf_small_mv"
    combos = {
      "A00_vol20_amt1":  {"uf_low_amount":0.33, SM:0.34, "uf_low_vol":0.33},
      "A01_vol20_amt20": {"uf_low_amount_20":0.33, SM:0.34, "uf_low_vol":0.33},
      "A10_vol60_amt1":  {"uf_low_amount":0.33, SM:0.34, "uf_low_vol_60":0.33},
      "A11_vol60_amt20": {"uf_low_amount_20":0.33, SM:0.34, "uf_low_vol_60":0.33},
    }
    out = {"phases": P10, "combos": {}}
    for name, sc in combos.items():
        t0 = time.time()
        recs = run_cell(sc, {}, P10)
        a = agg_phases(recs); a.update(recs=recs, factors=recs[0]["factors"])
        exc = {y: round(st.mean([r["yearly"].get(y,0.0)-BM.get(y,0.0) for r in recs]),4) for y in ["2018"]+[str(v) for v in range(2019,2027)]}
        a["excess_mean"] = exc
        a["pos_years"] = sum(1 for y in [str(v) for v in range(2019,2027)] if exc[y] > 0)
        out["combos"][name] = a
        print(f"{name}: ret {a['ret_mean']*100:+.0f}% sharpe {a['sharpe_mean']:.3f} (min {a['sharpe_min']:.2f}) mdd {a['mdd_mean']*100:.1f}% posY {a['pos_years']}/8 factors {recs[0]['factors']} ({time.time()-t0:.0f}s)", flush=True)
        json.dump(out, open(EV+"r14_layerA.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
    base = out["combos"]["A00_vol20_amt1"]["sharpe_mean"]
    print(f"\n== 层A 判定 (基线 A00 sharpe {base:.3f}) ==", flush=True)
    for name, a in out["combos"].items():
        if name == "A00_vol20_amt1": continue
        pass_gate = (a["sharpe_mean"] >= base - 0.02) and (a["sharpe_mean"] >= base + 0.03)
        print(f"{name}: sharpe {a['sharpe_mean']:.3f} (Δ{a['sharpe_mean']-base:+.3f}) gate={pass_gate}", flush=True)
    print("saved r14_layerA.json")

# ---------------- 层 B: 风控参数网格 ----------------
if sys.argv[1] == "layerB":
    scoring = json.loads(sys.argv[2]); dirs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    mp_grid = [8,10,12,15,20]; hold_grid = [10,15,20,30]; am_grid = [0.3e8,0.5e8,0.8e8]
    out = {"phases": P10, "scoring": scoring, "dirs": dirs,
           "grid_mp": mp_grid, "grid_hold": hold_grid, "grid_amount_min": am_grid, "cells": {}}
    t0 = time.time()
    for mp in mp_grid:
        for hold in hold_grid:
            for am in am_grid:
                key = f"mp{mp}_h{hold}_am{am/1e8:.1f}"
                recs = run_cell(scoring, dirs, P10, maxpos=mp, hold=hold, amount_min=am)
                a = agg_phases(recs); a["key"] = key
                out["cells"][key] = a
                json.dump(out, open(EV+"r14_layerB.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"{key}: ret {a['ret_mean']*100:+.0f}% sharpe {a['sharpe_mean']:.3f} mdd {a['mdd_mean']*100:.1f}% trades {a['trades_mean']:.0f} ({time.time()-t0:.0f}s)", flush=True)
    print("saved r14_layerB.json")
