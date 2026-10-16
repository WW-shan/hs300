# 输入说明: 进程内运行 (无需后端; 策略回测走 worker 子进程)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r28_combos.py
# 作用: R28 预注册 — B80 × D1(低PB∩高ROE∩高现金流) × D4(低负债∩高OCF/负债) 6 组权重, 5 相位, 2× 成本, G1~G7。
# 结果 JSON: 同目录 r28_cross_dimension_combos.json。
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
END = date(2026, 10, 14)
LAST_YEAR_YM = "2025-10"
ROUND_TRIP_2X = 0.0028


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")

COMBOS = {
    "C1_B80": {"B80": 1.0},
    "C2_70B80_30D1": {"B80": 0.7, "D1": 0.3},
    "C3_70B80_30D4": {"B80": 0.7, "D4": 0.3},
    "C4_60B80_20D1_20D4": {"B80": 0.6, "D1": 0.2, "D4": 0.2},
    "C5_50B80_25D1_25D4": {"B80": 0.5, "D1": 0.25, "D4": 0.25},
    "C6_50V20_25D1_25D4": {"V20": 0.5, "D1": 0.25, "D4": 0.25},
}
D1_LEGS = [("pb", "low", 1 / 3), ("roe", "high", 1 / 3), ("ocf_ni", "high", 0.5)]
D4_LEGS = [("debt_to_asset_ratio", "low", 1 / 3), ("ocf_debt", "high", 1 / 3)]
P2_OVERRIDES = {
    "scoring": {"uf_low_amount": 0.34, "uf_small_mv": 0.33, "uf_low_vol": 0.33},
    "scoring_replace": True,
    "max_hold_days": 35,
    "stop_loss": None,
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8,
                     "amount_min": 0.5e8, "exclude_st": True, "exclude_new_days": 60},
    "neutralize": {"industry": "industry_l1", "shrink": 0.5},
}
V20_OVERRIDES = {
    "scoring": {"overnight_ret_20d": 0.5, "amplitude_trend_20_60": 0.5},
    "scoring_replace": True,
    "max_hold_days": 20,
    "stop_loss": -0.20,
}
V20_PARAMS = {"use_trend_filter": True}


def run_strategy(strategy_id: str, start: date, end: date, params=None, overrides=None,
                 commission: float | None = None, slippage: float | None = None) -> dict:
    from app.backtest.strategy import StrategyBacktestConfig
    from app.backtest.worker import make_worker_task, run_worker_task
    from app.config import settings

    cfg = StrategyBacktestConfig(
        strategy_id=strategy_id, symbols=None, start=start, end=end,
        params=params, overrides=overrides, matching="open_t+1",
        fees_pct=commission if commission is not None else 0.0002,
        slippage_bps=slippage if slippage is not None else 5.0,
        max_positions=20, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    return run_worker_task(make_worker_task("backtest", settings.data_dir, cfg))


def monthly_from_curve(curve: list) -> pl.DataFrame:
    if not curve:
        return pl.DataFrame({"ym": [], "r": []})
    rows = [
        {"date": x["date"] if isinstance(x["date"], date) else date.fromisoformat(str(x["date"])[:10]),
         "value": float(x["value"])}
        for x in curve
    ]
    df = pl.DataFrame(rows).sort("date").with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
    last = df.group_by("ym", maintain_order=True).agg(pl.col("value").last().alias("close"), pl.col("date").max().alias("end")).sort("end")
    ys = last["ym"].to_list()
    vals = last["close"].to_list()
    out = [{"ym": ys[i], "r": vals[i] / vals[i - 1] - 1.0} for i in range(1, len(ys))]
    if out:
        first = df.filter(pl.col("ym") == ys[0])["value"].to_list()
        out.insert(0, {"ym": ys[0], "r": first[-1] / first[0] - 1.0})
    return pl.DataFrame(out)


def monthly_d_series(legs, cost=ROUND_TRIP_2X) -> pl.DataFrame:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r23.attach_pit(
        r23.load_panel(symbols),
        r23.derived(r23.to_quarterly(r23.merge_tables(symbols))).with_columns(pl.col("announce").alias("_eff")),
    )
    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    d = reb
    for col, direction, q in legs:
        d = d.filter(pl.col(col).is_not_null()).with_columns(
            (pl.col(col).rank().over("date") / pl.len().over("date")).alias("_pct")
        )
        d = d.filter(pl.col("_pct") <= q) if direction == "low" else d.filter(pl.col("_pct") >= 1 - q)
    monthly = (
        d.filter(pl.col("fwd").is_not_null())
        .group_by("date")
        .agg([pl.col("fwd").mean().alias("r"), pl.col("symbol").alias("symbols")])
        .sort("date")
    )
    rows = []
    prev = None
    for row in monthly.iter_rows(named=True):
        members_set = set(row["symbols"])
        turn = 1.0 if prev is None else 1.0 - len(members_set & prev) / max(len(members_set), 1)
        rows.append({"ym": row["date"].strftime("%Y-%m"), "r": row["r"] - turn * cost})
        prev = members_set
    return pl.DataFrame(rows).sort("ym")


def build_combo(weights: dict, phase: date, runs: dict, d1: pl.DataFrame, d4: pl.DataFrame, suffix: str) -> pl.DataFrame:
    start_ym = phase.strftime("%Y-%m")
    sleeves = {
        "B80": (
            runs[f"P2_{suffix}"].join(runs[f"V20_{suffix}"], on="ym", suffix="_v")
            .with_columns((0.8 * pl.col("r") + 0.2 * pl.col("r_v")).alias("r"))
            .select(["ym", "r"])
        ),
        "V20": runs[f"V20_{suffix}"].select(["ym", "r"]),
        "D1": d1.select(["ym", "r"]),
        "D4": d4.select(["ym", "r"]),
    }
    out = None
    for name, weight in weights.items():
        part = sleeves[name].filter(pl.col("ym") >= start_ym).with_columns((pl.col("r") * weight).alias("r"))
        if out is None:
            out = part
        else:
            out = (
                out.join(part, on="ym", how="inner", suffix="_x")
                .with_columns((pl.col("r") + pl.col("r_x")).alias("r"))
                .select(["ym", "r"])
            )
    return out


def stats(monthly: pl.DataFrame) -> dict:
    if monthly is None or monthly.height < 6:
        return {"annual": None, "sharpe": None, "mdd": None, "yearly": {}, "positive_years": 0,
                "last_year_return": None, "months": monthly.height if monthly is not None else 0}
    rows = monthly.sort("ym")
    vals = rows["r"].to_list()
    total = 1.0
    for v in vals:
        total *= 1.0 + v
    n = len(vals)
    sd = st.pstdev(vals)
    sharpe = st.mean(vals) / sd * (12 ** 0.5) if sd > 0 else None
    peak = value = 1.0
    mdd = 0.0
    for v in vals:
        value *= 1.0 + v
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    yearly_prod: dict[str, float] = {}
    for ym, v in zip(rows["ym"].to_list(), vals, strict=False):
        yearly_prod[ym[:4]] = yearly_prod.get(ym[:4], 1.0) * (1.0 + v)
    yearly = {k: round(v - 1.0, 4) for k, v in sorted(yearly_prod.items())}
    last = 1.0
    for ym, v in zip(rows["ym"].to_list(), vals, strict=False):
        if ym >= LAST_YEAR_YM:
            last *= 1.0 + v
    return {
        "annual": round(total ** (12.0 / n) - 1.0, 4),
        "sharpe": round(sharpe, 3) if sharpe else None,
        "mdd": round(mdd, 4),
        "yearly": yearly,
        "positive_years": sum(1 for v in yearly.values() if v > 0),
        "last_year_return": round(last - 1.0, 4),
        "months": n,
    }


def main() -> int:
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH").select(["date"]).collect().sort("date")
    )
    dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    starts_target = [date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    phases = [next(d for d in dates if d >= t) for t in starts_target]
    print("phases:", [str(p) for p in phases], flush=True)

    d1 = monthly_d_series(D1_LEGS)
    d4 = monthly_d_series(D4_LEGS)
    print("D1 months:", d1.height, "D4 months:", d4.height, flush=True)

    phase_runs: dict[str, dict] = {}
    phase_monthly: dict[str, dict] = {str(p): {} for p in phases}
    for phase in phases:
        key = str(phase)
        runs = {}
        for tag, sid, params, overrides in (
            ("P2", "ai_hs300_lowrisk_alpha", None, P2_OVERRIDES),
            ("V20", "ai_hs300_multi_factor_rank", V20_PARAMS, V20_OVERRIDES),
        ):
            for cost_tag, comm, slip in (("1x", None, None), ("2x", 0.0004, 10.0)):
                res = run_strategy(sid, phase, END, params=params, overrides=overrides, commission=comm, slippage=slip)
                runs[f"{tag}_{cost_tag}"] = monthly_from_curve(res.get("equity_curve") or [])
                print(f"[{key}] {tag} {cost_tag}: months={runs[f'{tag}_{cost_tag}'].height}", flush=True)
        phase_runs[key] = runs
        for name, weights in COMBOS.items():
            phase_monthly[key][name] = {
                "1x": build_combo(weights, phase, runs, d1, d4, "1x"),
                "2x": build_combo(weights, phase, runs, d1, d4, "2x"),
            }
        print(f"[{key}] combos built", flush=True)

    summary = {}
    for name in COMBOS:
        rec_1x = [stats(phase_monthly[k][name]["1x"]) for k in phase_monthly]
        rec_2x = [stats(phase_monthly[k][name]["2x"]) for k in phase_monthly]
        summary[name] = {
            "sharpe_mean": round(st.mean(r["sharpe"] for r in rec_1x if r["sharpe"] is not None), 3),
            "mdd_mean": round(st.mean(r["mdd"] for r in rec_1x), 4),
            "annual_mean": round(st.mean(r["annual"] for r in rec_1x if r["annual"] is not None), 4),
            "last_year_min": round(min(r["last_year_return"] for r in rec_1x), 4),
            "sharpe_mean_2x": round(st.mean(r["sharpe"] for r in rec_2x if r["sharpe"] is not None), 3),
            "positive_years_mean": round(st.mean(r["positive_years"] for r in rec_1x), 2),
            "phases": {k: {**rec_1x[i], "2x": rec_2x[i]} for i, k in enumerate(phase_monthly)},
        }

    base = summary["C1_B80"]
    gates = {}
    for name in COMBOS:
        s = summary[name]
        g1 = s["sharpe_mean"] >= base["sharpe_mean"] - 0.05
        g2 = s["mdd_mean"] >= base["mdd_mean"] - 0.05
        g3 = s["last_year_min"] >= base["last_year_min"] - 0.01
        g4 = s["sharpe_mean_2x"] >= 0.90 and s["sharpe_mean_2x"] >= base["sharpe_mean_2x"] - 0.05
        g5 = s["positive_years_mean"] >= base["positive_years_mean"]

        corrs = []
        for k in phase_monthly:
            cand = phase_monthly[k][name]["1x"].rename({"r": "rc"})
            b80 = phase_monthly[k]["C1_B80"]["1x"].rename({"r": "rb"})
            j = cand.join(b80, on="ym", how="inner")
            diff = (j["rc"] - j["rb"]).to_list()
            base_vals = j["rb"].to_list()
            if len(diff) > 6 and st.pstdev(diff) > 0 and st.pstdev(base_vals) > 0:
                corrs.append(st.correlation(diff, base_vals))
        g6 = bool(corrs and st.mean(corrs) < 0.7)

        # G7: 风险袖权重 ±5pp, 其余腿按比例缩放
        risk = "B80" if "B80" in COMBOS[name] else "V20"
        pert_sharpes = {}
        for tag, delta in (("plus5", 0.05), ("minus5", -0.05)):
            w = dict(COMBOS[name])
            others_total = sum(v for k, v in w.items() if k != risk)
            new_risk = w[risk] + delta
            scale = (1.0 - new_risk) / others_total if others_total > 0 else 0.0
            w = {k: (new_risk if k == risk else v * scale) for k, v in w.items()}
            recs = []
            for phase in phases:
                k = str(phase)
                frame = build_combo(w, phase, phase_runs[k], d1, d4, "1x")
                r = stats(frame)
                if r["sharpe"] is not None:
                    recs.append(r["sharpe"])
            pert_sharpes[tag] = round(st.mean(recs), 3) if recs else None
        worst_pert = min(v for v in pert_sharpes.values() if v is not None)
        g7 = bool(worst_pert >= s["sharpe_mean"] - 0.05)

        gates[name] = {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7,
                       "G6_corr": round(st.mean(corrs), 3) if corrs else None,
                       "G7_perturbed_sharpes": pert_sharpes,
                       "all_pass": bool(g1 and g2 and g3 and g4 and g5 and g6 and g7)}

    result = {
        "window": {"phases": [str(p) for p in phases], "end": str(END), "rebalance": "monthly",
                   "note": "D1/D4 TTM 前置, 合成自 2023-08 起有共同月份"},
        "summary": summary,
        "gates": gates,
        "adopt": sorted([n for n, g in gates.items() if g["all_pass"] and n != "C1_B80"],
                        key=lambda n: -summary[n]["sharpe_mean"]),
    }
    out = EV / "r28_cross_dimension_combos.json"
    out.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    for name in COMBOS:
        s = summary[name]
        print(f"{name:22s} sharpe={s['sharpe_mean']} annual={s['annual_mean']} mdd={s['mdd_mean']} lastY_min={s['last_year_min']} sharpe2x={s['sharpe_mean_2x']} gates={gates[name]}")
    print("adopt:", result["adopt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
