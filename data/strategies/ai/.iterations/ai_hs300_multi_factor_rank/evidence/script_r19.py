# 输入说明: 需本地后端 (127.0.0.1:3018) 与 API Token: 运行前 `export TF_TOKEN=<token>`。
# 结果 JSON: 与本脚本同目录的 r19_blend_p2_v20.json。数据源为仓库内 data/ 下的本地缓存。
import json
import math
import os
import statistics as st
import time
import urllib.request
from collections import defaultdict

import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
INDEX_PATH = os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet")
INDEX = (
    pl.scan_parquet(INDEX_PATH, extra_columns="ignore")
    .filter(pl.col("symbol") == "000300.SH")
    .select(["date", "close"])
    .collect()
    .sort("date")
    .with_columns(pl.col("date").cast(pl.Utf8))
)
INDEX_DATES = INDEX["date"].to_list()
INDEX_CLOSE = dict(zip(INDEX_DATES, INDEX["close"].to_list(), strict=True))

P10 = [d for d in INDEX_DATES if d >= "2018-06-01"][:10]
P1Y = [d for d in INDEX_DATES if d >= "2025-10-14"][:5]

V2 = {"uf_low_amount": 0.34, "uf_small_mv": 0.33, "uf_low_vol": 0.33}
P2_OVERRIDES = {
    "scoring": V2,
    "scoring_replace": True,
    "max_hold_days": 35,
    "stop_loss": None,
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "neutralize": {"industry": "industry_l1", "shrink": 0.5},
}
V20_OVERRIDES = {
    "scoring": {"overnight_ret_20d": 0.5, "amplitude_trend_20_60": 0.5},
    "scoring_replace": True,
    "max_hold_days": 20,
    "stop_loss": -0.20,
}
V20_PARAMS = {"use_trend_filter": True}


def post(path, payload, timeout=3600):
    token = os.environ.get("TF_TOKEN", "")
    if not token:
        raise RuntimeError("缺少 TF_TOKEN 环境变量")
    req = urllib.request.Request(
        "http://127.0.0.1:3018" + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_bt(strategy_id, start, maxpos, overrides=None, params=None,
           commission=None, slippage=None):
    payload = {
        "strategy_id": strategy_id,
        "start": start,
        "end": "2026-10-14",
        "matching": "open_t+1",
        "mode": "position",
        "hs300": True,
        "max_positions": maxpos,
        "position_sizing": "equal",
        "holding_days": 5,
        "asset_type": "stock",
    }
    if overrides is not None:
        payload["overrides"] = overrides
    if params is not None:
        payload["params"] = params
    if commission is not None:
        payload["commission_pct"] = commission
    if slippage is not None:
        payload["slippage_bps"] = slippage
    t0 = time.time()
    raw = post("/api/backtest/strategy/run", payload)
    if raw.get("error"):
        raise RuntimeError(f"{strategy_id} {start}: {raw['error']}")
    curve = [
        {"date": str(x["date"])[:10], "value": float(x["value"])}
        for x in (raw.get("equity_curve") or [])
    ]
    if len(curve) < 2:
        raise RuntimeError(f"{strategy_id} {start}: 净值曲线不足")
    return {
        "stats": raw.get("stats") or {},
        "curve": curve,
        "factor_attribution": (raw.get("factor_attribution") or {}).get("factors") or [],
        "elapsed_s": round(time.time() - t0, 2),
    }


def returns_from_curve(curve):
    vals = [x["value"] for x in curve]
    return [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]


def max_drawdown(values):
    peak = values[0]
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def blend_curve(curve_a, curve_b, weight_a):
    da = {x["date"]: x["value"] for x in curve_a}
    db = {x["date"]: x["value"] for x in curve_b}
    dates = sorted(set(da) & set(db))
    if len(dates) < 2:
        raise RuntimeError("组合曲线日期交集不足")
    values = [1.0]
    for i in range(1, len(dates)):
        ra = da[dates[i]] / da[dates[i - 1]] - 1
        rb = db[dates[i]] / db[dates[i - 1]] - 1
        values.append(values[-1] * (1 + weight_a * ra + (1 - weight_a) * rb))
    return [{"date": d, "value": v} for d, v in zip(dates, values, strict=True)]


def curve_metrics(curve):
    vals = [x["value"] for x in curve]
    rets = returns_from_curve(curve)
    sharpe = None
    if len(rets) > 1 and st.stdev(rets) > 0:
        sharpe = st.mean(rets) / st.stdev(rets) * math.sqrt(252)
    return {
        "total_return": vals[-1] / vals[0] - 1,
        "max_drawdown": max_drawdown(vals),
        "sharpe": sharpe,
        "daily_vol": st.stdev(rets) if len(rets) > 1 else None,
    }


def yearly_returns(curve):
    by_year = defaultdict(list)
    for x in curve:
        by_year[x["date"][:4]].append(x["value"])
    out = {}
    prev = None
    for year in sorted(by_year):
        vals = by_year[year]
        base = prev if prev is not None else vals[0]
        out[year] = vals[-1] / base - 1
        prev = vals[-1]
    return out


def benchmark_yearly(curve):
    dates = [x["date"] for x in curve]
    by_year = defaultdict(list)
    for d in dates:
        if d in INDEX_CLOSE:
            by_year[d[:4]].append(d)
    out = {}
    prev = None
    for year in sorted(by_year):
        ds = by_year[year]
        if prev is None:
            # 起始年使用曲线首个交易日前最近一个指数收盘作为基期。
            pos = INDEX_DATES.index(ds[0])
            base_date = INDEX_DATES[pos - 1] if pos > 0 else ds[0]
        else:
            base_date = prev
        out[year] = INDEX_CLOSE[ds[-1]] / INDEX_CLOSE[base_date] - 1
        prev = ds[-1]
    return out


def aggregate(records):
    return {
        "sharpe_mean": st.mean(x["sharpe"] for x in records),
        "mdd_mean": st.mean(x["max_drawdown"] for x in records),
        "ret_mean": st.mean(x["total_return"] for x in records),
        "records": records,
    }


def run_phase_set(phases, cost=False):
    runs = {}
    for tag, sid, ov, params in (
        ("P2", "ai_hs300_lowrisk_alpha", P2_OVERRIDES, None),
        ("V20", "ai_hs300_multi_factor_rank", V20_OVERRIDES, V20_PARAMS),
    ):
        runs[tag] = []
        for phase in phases:
            rec = run_bt(
                sid, phase, 20, overrides=ov, params=params,
                commission=0.0004 if cost else None,
                slippage=10.0 if cost else None,
            )
            runs[tag].append({"phase": phase, **rec})
            print(f"[{'2x' if cost else 'base'}] {tag} {phase}: "
                  f"ret={rec['stats'].get('total_return'):+.3f} "
                  f"sharpe={rec['stats'].get('sharpe'):+.3f} ({rec['elapsed_s']}s)",
                  flush=True)
    return runs


def summarize(phase_runs, label):
    # 单腿直接使用 API 统计；组合从日净值曲线重算。
    p2_curves = [x["curve"] for x in phase_runs["P2"]]
    v20_curves = [x["curve"] for x in phase_runs["V20"]]
    out = {
        "label": label,
        "P2": aggregate([
            {"phase": x["phase"], "curve": x["curve"], **curve_metrics(x["curve"]), "api_stats": x["stats"]}
            for x in phase_runs["P2"]
        ]),
        "V20": aggregate([
            {"phase": x["phase"], "curve": x["curve"], **curve_metrics(x["curve"]), "api_stats": x["stats"]}
            for x in phase_runs["V20"]
        ]),
    }
    for tag, w in (("B70", 0.70), ("B50", 0.50)):
        recs = []
        for i, phase in enumerate([x["phase"] for x in phase_runs["P2"]]):
            curve = blend_curve(p2_curves[i], v20_curves[i], w)
            recs.append({"phase": phase, **curve_metrics(curve), "curve": curve})
        out[tag] = aggregate(recs)
    # 机制: 日收益相关性与组合波动。
    corrs = []
    vol_p2 = []
    vol_v20 = []
    vol_blends = {"B70": [], "B50": []}
    for i in range(len(p2_curves)):
        ra = returns_from_curve(p2_curves[i])
        rb = returns_from_curve(v20_curves[i])
        n = min(len(ra), len(rb))
        if n > 1:
            corrs.append(st.correlation(ra[:n], rb[:n]))
        vol_p2.append(st.stdev(ra))
        vol_v20.append(st.stdev(rb))
        for tag, _w in (("B70", 0.70), ("B50", 0.50)):
            bc = out[tag]["records"][i]["curve"]
            vol_blends[tag].append(st.stdev(returns_from_curve(bc)))
    out["mechanism"] = {
        "corr_mean": st.mean(corrs) if corrs else None,
        "p2_daily_vol_mean": st.mean(vol_p2),
        "v20_daily_vol_mean": st.mean(vol_v20),
        "B70_daily_vol_mean": st.mean(vol_blends["B70"]),
        "B50_daily_vol_mean": st.mean(vol_blends["B50"]),
    }
    # 逐年相对指数超额: 逐相位算收益差，再按年份取相位平均。
    for tag in ("P2", "V20", "B70", "B50"):
        records = out[tag]["records"]
        years = sorted({y for r in records for y in yearly_returns(r["curve"])})
        excess = {}
        for year in years:
            vals = []
            for r in records:
                yr = yearly_returns(r["curve"])
                if year in yr:
                    b = benchmark_yearly(r["curve"]).get(year)
                    if b is not None:
                        vals.append(yr[year] - b)
            excess[year] = st.mean(vals) if vals else None
        out[tag]["yearly_excess"] = excess
        out[tag]["pos_excess_full_years"] = sum(
            1 for y in ("2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025")
            if excess.get(y) is not None and excess[y] > 0
        )
    return out


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k != "curve"}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def main():
    out = {
        "phases_10": P10,
        "phases_1y": P1Y,
        "method": "P2/V20 分别回测，固定权重日收益合成；2x 成本先分别重跑再合成",
        "base": {},
        "cost2x": {},
    }
    t0 = time.time()
    base10 = run_phase_set(P10)
    out["base"]["long10"] = summarize(base10, "long10")
    with open(EV + "r19_blend_p2_v20.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print(f"[saved] base long10 ({time.time()-t0:.0f}s)", flush=True)

    base1y = run_phase_set(P1Y)
    out["base"]["recent1y"] = summarize(base1y, "recent1y")
    with open(EV + "r19_blend_p2_v20.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print(f"[saved] base recent1y ({time.time()-t0:.0f}s)", flush=True)

    cost10 = run_phase_set(P10, cost=True)
    out["cost2x"]["long10"] = summarize(cost10, "long10_2x")
    with open(EV + "r19_blend_p2_v20.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print(f"[saved] cost2x long10 ({time.time()-t0:.0f}s)", flush=True)

    # 近一年 5 相位的中位收益：组合从最近一轮 base1y 记录直接取。
    for tag in ("P2", "V20", "B70", "B50"):
        recs = out["base"]["recent1y"][tag]["records"]
        vals = [r["total_return"] for r in recs]
        out["base"]["recent1y"][tag]["recent_median"] = st.median(vals)
        out["base"]["recent1y"][tag]["recent_all_pos"] = all(v > 0 for v in vals)
        out["base"]["recent1y"][tag]["recent_returns"] = vals
    with open(EV + "r19_blend_p2_v20.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print("saved r19_blend_p2_v20.json", flush=True)


if __name__ == "__main__":
    main()
