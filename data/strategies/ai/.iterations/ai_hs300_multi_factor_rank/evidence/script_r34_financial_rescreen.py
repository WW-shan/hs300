# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r34_financial_rescreen.py
# 作用: R34 — 修正 PIT 后 10 个财务因子全量复筛 + PEAD 重做。
# 结果 JSON: 同目录 r34_financial_rescreen.json。
import bisect
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
START, END = date(2021, 6, 1), date(2026, 10, 14)
ROUND_TRIP_2X = 0.0028


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")
r30 = _load("r30_final")
r32 = _load("r32_pit_recheck")

FACTORS = {
    "pb_latest": False,
    "roe_latest": True,
    "gross_margin_latest": True,
    "net_margin_latest": True,
    "revenue_yoy_latest": True,
    "net_income_yoy_latest": True,
    "debt_ratio_latest": False,
    "ocf_ni": True,
    "accrual": False,
    "ocf_debt": True,
}
FACTOR_NAMES = list(FACTORS)


def build_panel():
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r23.load_panel(symbols).sort(["symbol", "date"])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    panel = attach_fundamental_factors(panel, snapshot, FACTOR_NAMES)
    panel = panel.with_columns(
        pl.when(pl.col("pb_latest") > 0)
        .then(pl.col("roe_latest") / (100.0 * pl.col("pb_latest")))
        .otherwise(None)
        .alias("ep")
    )
    reb = r22.leg_columns(r22.build_monthly(panel, r22.monthly_pairs(panel)))
    return panel, reb, snapshot, symbols


def batch_check() -> dict:
    from app.backtest.engine import BacktestEngine
    from app.backtest.factor import FactorBacktestService, FactorBatchConfig
    from app.tickflow.repository import DataStore, KlineRepository

    service = FactorBacktestService(BacktestEngine(KlineRepository(DataStore())))
    result = service.run_batch(FactorBatchConfig(
        factor_names=FACTOR_NAMES, symbols=None, start=START, end=END,
        n_groups=5, rebalance="monthly", weight="equal",
        fees_pct=0.0004, slippage_bps=10.0, asset_type="stock", hs300=True,
    ))
    items = {}
    for item in result.results:
        items[item.factor_name] = {
            "error": item.error,
            "coverage": item.coverage,
            "ic_mean": item.ic_mean,
            "ir": item.ir,
            "long_short_return": item.long_short_return,
            "long_short_sharpe": item.long_short_sharpe,
        }
    return {"error": result.error, "n_symbols": result.n_symbols, "n_dates": result.n_dates, "items": items}


def pead(panel: pl.DataFrame, snapshot: pl.DataFrame) -> dict:
    px = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"])
    px = px.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret"))
    mkt_df = px.group_by("date").agg(pl.col("ret").mean().alias("mkt")).sort("date")
    mkt = dict(zip(mkt_df["date"].to_list(), mkt_df["mkt"].to_list(), strict=False))
    series = {}
    for sym, group in px.group_by("symbol"):
        key = sym[0] if isinstance(sym, tuple) else sym
        series[key] = (group["date"].to_list(), group["close"].to_list())
    events = (
        snapshot.select(["symbol", pl.col("_announce_metrics").alias("announce"), "net_income_yoy"])
        .filter(
            pl.col("announce").is_not_null()
            & (pl.col("announce") >= START)
            & (pl.col("announce") <= END)
        )
        .drop_nulls()
    )
    rows = []
    for sym, ann, yoy in events.iter_rows():
        item = series.get(sym)
        if not item:
            continue
        dates, closes = item
        i = bisect.bisect_right(dates, ann)
        if i + 60 >= len(dates):
            continue
        stock = closes[i + 60] / closes[i] - 1.0
        bench = 1.0
        for j in range(i + 1, i + 61):
            bench *= 1.0 + mkt.get(dates[j], 0.0)
        rows.append({"month": dates[i].strftime("%Y-%m"), "car60": stock - (bench - 1.0), "yoy": yoy})
    if not rows:
        return {"n_events": 0}
    df = pl.DataFrame(rows).with_columns(
        ((pl.col("yoy").rank().over("month") - 1.0) * 5.0 / pl.len().over("month")).floor().clip(0, 4).cast(pl.Int64).alias("q")
    )
    summary = []
    for q, group in df.group_by("q", maintain_order=True):
        vals = group["car60"].to_list()
        if len(vals) < 20:
            continue
        mean = st.mean(vals)
        sd = st.pstdev(vals)
        summary.append({
            "q": int(q[0] if isinstance(q, tuple) else q),
            "n": len(vals),
            "mean": round(mean, 4),
            "median": round(st.median(vals), 4),
            "t": round(mean / (sd / len(vals) ** 0.5), 2) if sd > 0 else None,
        })
    summary.sort(key=lambda x: x["q"])
    top = summary[-1] if summary else None
    bottom = summary[0] if summary else None
    monotonic = bool(summary and len(summary) == 5 and all(
        summary[i]["mean"] <= summary[i + 1]["mean"] for i in range(4)
    ))
    return {
        "n_events": df.height,
        "by_quintile": summary,
        "top_median": top["median"] if top else None,
        "top_t": top["t"] if top else None,
        "bottom_median": bottom["median"] if bottom else None,
        "monotonic": monotonic,
    }


def main() -> int:
    panel, reb, snapshot, symbols = build_panel()
    print(f"panel rows={panel.height}, rebalances={len(r22.monthly_pairs(panel))}", flush=True)
    audit = r32.pit_audit(snapshot, symbols, set(symbols))
    batch = batch_check()
    print("batch errors:", {k: v["error"] for k, v in batch["items"].items()}, flush=True)

    gates = {}
    for name, good_high in FACTORS.items():
        ic = r22.ic_summary(reb, name)
        grp = r22.offline_groups(reb, name, good_high=good_high)
        corr_v2 = max(
            (v for v in (r22.mean_abs_corr(reb, name, leg) for leg in ("leg_low_amount", "leg_small_mv", "leg_low_vol")) if v is not None),
            default=None,
        )
        corr_value = max(
            (v for v in (r22.mean_abs_corr(reb, name, other) for other in ("pb_latest", "ep")) if v is not None),
            default=None,
        )
        rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
        gates[name] = {
            "direction": "high" if good_high else "low",
            "F1_ic": bool(ic and abs(ic["ic_mean"]) >= 0.03 and r22.yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4),
            "F1_ic_mean": ic["ic_mean"] if ic else None,
            "F1_yearly": ic["yearly"] if ic else None,
            "F2_monotonic_spread": bool(rho_ok and grp and grp["spread_month"] > 0),
            "F2_monotonic_rho": grp["monotonic_rho"] if grp else None,
            "F2_spread_month": grp["spread_month"] if grp else None,
            "F3_spread_2x": bool(grp and grp["spread_annual_net2x"] is not None and grp["spread_annual_net2x"] > 0),
            "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
            "F4_max_abs_corr_v2": corr_v2,
            "F4_ok": bool(corr_v2 is not None and corr_v2 < 0.7),
            "F6_max_abs_corr_value": corr_value,
            "F6_ok": bool(name == "pb_latest" or (corr_value is not None and corr_value < 0.7)),
            "batch": batch["items"].get(name),
        }
        gates[name]["all_pass"] = bool(
            gates[name]["F1_ic"] and gates[name]["F2_monotonic_spread"]
            and gates[name]["F3_spread_2x"] and gates[name]["F4_ok"] and gates[name]["F6_ok"]
        )
        print(f"{name}: IC={gates[name]['F1_ic_mean']} F1={gates[name]['F1_ic']} F2={gates[name]['F2_monotonic_spread']} "
              f"F3={gates[name]['F3_spread_2x']} F4={gates[name]['F4_max_abs_corr_v2']} F6={gates[name]['F6_max_abs_corr_value']} "
              f"all={gates[name]['all_pass']}", flush=True)

    pead_result = pead(panel, snapshot)
    print("PEAD:", {k: pead_result.get(k) for k in ("n_events", "top_median", "top_t", "monotonic")}, flush=True)

    old = {}
    r22_json = json.loads((EV / "r22_value_factors.json").read_text(encoding="utf-8"))
    r23_json = json.loads((EV / "r23_quality_growth.json").read_text(encoding="utf-8"))
    for name in FACTORS:
        source = r22_json.get("gates", {}).get(name) or r23_json.get("gates", {}).get(name) or {}
        old[name] = {
            "ic_mean": source.get("F1_ic_mean"),
            "all_pass": source.get("all_pass"),
        }

    all_pass = sorted(name for name, gate in gates.items() if gate["all_pass"])
    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(r22.monthly_pairs(panel))},
        "pit_audit": audit,
        "batch": batch,
        "gates": gates,
        "all_pass_factors": all_pass,
        "pead": pead_result,
        "old_pit_comparison": old,
    }
    path = EV / "r34_financial_rescreen.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, flush=True)
    print("all_pass:", all_pass, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
