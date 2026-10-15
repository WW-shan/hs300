import statistics as st
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


REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH")
         .select(["date", "close"])
         .collect()
         .sort("date")
         .with_columns(pl.col("date").cast(pl.Utf8)))

OUT = os.path.dirname(os.path.abspath(__file__)) + "/r13_dimension_combos.json"
V2 = {"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
CANDS = {
  "B_v2":        {"scoring": V2, "dirs": {}},
  "D1_v2_turn":  {"scoring": {"uf_low_amount":0.25,"uf_small_mv":0.25,"uf_low_vol":0.25,"turnover_mean_20d":0.25},
                  "dirs": {"turnover_mean_20d":"low"}},
  "D4_v2_rev":   {"scoring": {"uf_low_amount":0.25,"uf_small_mv":0.25,"uf_low_vol":0.25,"momentum_10d":0.25},
                  "dirs": {"momentum_10d":"low"}},
  "D5_mv_vol_turn": {"scoring": {"uf_small_mv":1/3,"uf_low_vol":1/3,"turnover_mean_20d":1/3},
                  "dirs": {"turnover_mean_20d":"low"}},
  "D6_mv_vol_rev":  {"scoring": {"uf_small_mv":1/3,"uf_low_vol":1/3,"momentum_10d":1/3},
                  "dirs": {"momentum_10d":"low"}},
}

def phases(from_date, n):
    rows = IDX.filter(pl.col("date") >= from_date).head(n)
    return rows["date"].to_list()

P10 = phases("2018-06-01", 10)      # R7 相位集
P1Y = phases("2025-10-14", 5)       # R10/R12 fresh-start 相位集
print("P10:", P10)
print("P1Y:", P1Y, flush=True)

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

# benchmark yearly (same convention as R7)
bm = {}
for dte, close in IDX.filter((pl.col("date") >= "2018-05-31")).select(["date","close"]).iter_rows():
    bm.setdefault(str(dte)[:4], []).append(close)
BM = {}; prev = None
for y in sorted(bm):
    sv = prev if prev is not None else bm[y][0]
    BM[y] = bm[y][-1]/sv - 1; prev = bm[y][-1]
print("benchmark yearly:", {k: round(v,4) for k,v in BM.items()}, flush=True)

def ov(c, cost=False):
    o = {"scoring": c["scoring"], "scoring_replace": True, "max_hold_days": 20, "stop_loss": None}
    if c["dirs"]: o["scoring_directions"] = c["dirs"]
    return o

out = {"phases_full": P10, "phases_1y": P1Y, "benchmark_yearly": {k: round(v,5) for k,v in BM.items()}, "candidates": {}}
for name, c in CANDS.items():
    t0 = time.time(); rec = {"full": [], "cost2x": [], "last1y": [], "factors": None}
    for ph in P10:
        s, fac, err, el, raw = run_bt(start=ph, overrides=ov(c))
        if err: raise RuntimeError(f"{name} {ph}: {err}")
        y = yearly(raw.get("equity_curve"))
        rec["factors"] = fac
        rec["full"].append({"phase": ph, "ret": s.get("total_return"), "mdd": s.get("max_drawdown"),
                            "sharpe": s.get("sharpe"), "calmar": s.get("calmar"), "trades": s.get("n_trades"),
                            "yearly": {k: round(v,4) for k,v in y.items()}})
        print(f"  {name} {ph}: ret={s.get('total_return'):+.3f} sharpe={s.get('sharpe')} mdd={s.get('max_drawdown'):+.3f} ({time.time()-t0:.0f}s)", flush=True)
    # 2x cost
    for ph in P10:
        s, fac, err, el, raw = run_bt(start=ph, overrides=ov(c), commission=0.0004, slippage=10.0)
        rec["cost2x"].append({"phase": ph, "ret": s.get("total_return"), "mdd": s.get("max_drawdown"), "sharpe": s.get("sharpe")})
        print(f"  {name}[2x] {ph}: ret={s.get('total_return'):+.3f} sharpe={s.get('sharpe')} ({time.time()-t0:.0f}s)", flush=True)
    # fresh start 1y
    for ph in P1Y:
        s, fac, err, el, raw = run_bt(start=ph, end="2026-10-14", overrides=ov(c))
        rec["last1y"].append({"phase": ph, "ret": s.get("total_return"), "mdd": s.get("max_drawdown"), "sharpe": s.get("sharpe")})
        print(f"  {name}[1y] {ph}: ret={s.get('total_return'):+.3f} mdd={s.get('max_drawdown'):+.3f} ({time.time()-t0:.0f}s)", flush=True)
    out["candidates"][name] = rec
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", OUT)
