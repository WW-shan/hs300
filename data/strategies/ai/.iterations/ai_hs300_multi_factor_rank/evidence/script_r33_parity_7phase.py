# 输入说明: 进程内运行 (无需后端; 新增 2022 相位会写 R29 月收益缓存)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r33_parity_7phase.py
# 作用: R33 — 修正 parity 门槛 (null mismatch 必须由 _ttm_complete=False 解释) + C6_IND 7 相位稳健性。
# 结果 JSON: 同目录 r33_parity_7phase.json。
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = Path(__file__).resolve().parent.parents[5] / "data"
END = date(2026, 10, 14)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")
r28 = _load("r28_combos")
r30 = _load("r30_final")
r31 = _load("r31_d1_industry")
r32 = _load("r32_pit_recheck")
COSTS = r30.COSTS


def parity_audit(snapshot: pl.DataFrame, symbols: list[str]) -> dict:
    offline = (
        r23.derived(r23.to_quarterly(r23.merge_tables(symbols)))
        .select(["symbol", "period_end", "ocf_ni", "accrual", "ocf_debt"])
    )
    engine = snapshot.select(["symbol", "period_end", "ocf_ni", "accrual", "ocf_debt", "_ttm_complete"])
    joined = engine.join(offline, on=["symbol", "period_end"], how="inner", suffix="_off")
    out = {"rows": joined.height}
    for name in ("ocf_ni", "accrual", "ocf_debt"):
        a = pl.col(name)
        b = pl.col(f"{name}_off")
        both = joined.filter(a.is_not_null() & b.is_not_null())
        diff = (both[name] - both[f"{name}_off"]).abs()
        mism = joined.filter(a.is_null() != b.is_null())
        explained = mism.filter(
            a.is_null() & b.is_not_null() & (~pl.col("_ttm_complete"))
        )
        unexplained = mism.filter(~(a.is_null() & b.is_not_null() & (~pl.col("_ttm_complete"))))
        out[name] = {
            "compared_rows": both.height,
            "max_abs_diff": round(float(diff.max()), 12) if both.height else None,
            "null_mismatch": mism.height,
            "explained_by_incomplete_ttm": explained.height,
            "unexplained": unexplained.height,
            "mismatch_symbols": sorted(set(mism["symbol"].to_list())),
        }
    return out


def phase_dates(targets: list[date]) -> list[date]:
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date"])
        .collect()
        .sort("date")
    )
    dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    return [next(d for d in dates if d >= target) for target in targets]


def combo_runs(phases: list[date], d1_ind: pl.DataFrame, d4: pl.DataFrame, suffix: str) -> tuple[dict, list[pl.DataFrame]]:
    d1_frame, _ = r31.series_and_stats(d1_ind, COSTS[suffix][2])
    d4_frame, _ = r31.series_and_stats(d4, COSTS[suffix][2])
    frames = []
    for phase in phases:
        runs = r30.runs_for(phase, suffix)
        frames.append(r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_frame, d4_frame, suffix
        ))
    return {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}, frames


def b80_runs(phases: list[date], suffix: str) -> dict:
    empty = pl.DataFrame({"ym": [], "r": []})
    frames = []
    for phase in phases:
        runs = r30.runs_for(phase, suffix)
        frames.append(r28.build_combo({"B80": 1.0}, phase, runs, empty, empty, suffix))
    return {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}


def corr_v20(phases: list[date], d1_ind: pl.DataFrame, d4: pl.DataFrame) -> float | None:
    d1_frame, _ = r31.series_and_stats(d1_ind, COSTS["1x"][2])
    d4_frame, _ = r31.series_and_stats(d4, COSTS["1x"][2])
    corrs = []
    for phase in phases:
        runs = r30.runs_for(phase, "1x")
        base = r28.build_combo({"V20": 1.0}, phase, runs, d1_frame, d4_frame, "1x")
        cand = r28.build_combo({"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_frame, d4_frame, "1x")
        joined = cand.rename({"r": "rc"}).join(base.rename({"r": "rb"}), on="ym", how="inner")
        if joined.height > 6 and st.pstdev(joined["rc"].to_list()) > 0 and st.pstdev(joined["rb"].to_list()) > 0:
            corrs.append(st.correlation(joined["rc"].to_list(), joined["rb"].to_list()))
    return round(st.mean(corrs), 3) if corrs else None


def strategy_gate(metrics_3x: dict, rolling_3x: dict, d1_stats: dict, d4_stats: dict, corr: float | None) -> dict:
    return {
        "S1": metrics_3x["sharpe_mean"] >= 0.90,
        "S2": rolling_3x["positive_rate"] is not None
        and rolling_3x["positive_rate"] >= 0.90
        and rolling_3x["worst"] >= -0.08,
        "S3": bool(
            15 <= d1_stats["members_mean"] <= 35
            and 15 <= d4_stats["members_mean"] <= 35
            and d1_stats["top_industry_share_mean"] <= 0.40
            and d4_stats["top_industry_share_mean"] <= 0.40
        ),
        "S4": max(d1_stats["turnover_mean"], d4_stats["turnover_mean"]) <= 0.25,
        "S5": corr is not None and corr < 0.95,
    }


def main() -> int:
    reb, snapshot = r32.build_engine_rebalances()
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    print("reb rows:", reb.height, flush=True)

    audit = r32.pit_audit(snapshot, symbols, set(symbols))
    parity = parity_audit(snapshot, symbols)
    print("pit audit:", audit, flush=True)
    print("parity:", parity, flush=True)

    d1_ind = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    _, d1_stats = r31.series_and_stats(d1_ind, COSTS["2x"][2])
    _, d4_stats = r31.series_and_stats(d4, COSTS["2x"][2])

    phases_5 = phase_dates([
        date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2),
    ])
    phases_7 = phase_dates([
        date(2022, 5, 5), date(2022, 11, 1),
        date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2),
    ])
    print("phases5:", phases_5, "phases7:", phases_7, flush=True)

    five = {suffix: combo_runs(phases_5, d1_ind, d4, suffix)[0] for suffix in ("1x", "2x", "3x")}
    corr5 = corr_v20(phases_5, d1_ind, d4)
    b80_5 = b80_runs(phases_5, "3x")
    print("5-phase C6_IND 3x:", five["3x"], "corr:", corr5, flush=True)

    seven = {suffix: combo_runs(phases_7, d1_ind, d4, suffix)[0] for suffix in ("1x", "2x", "3x")}
    corr7 = corr_v20(phases_7, d1_ind, d4)
    b80_7 = b80_runs(phases_7, "3x")
    print("7-phase C6_IND 3x:", seven["3x"], "corr:", corr7, flush=True)
    print("7-phase B80 3x:", b80_7, flush=True)

    q1 = all(
        parity[name]["compared_rows"] >= 5000
        and parity[name]["max_abs_diff"] is not None
        and parity[name]["max_abs_diff"] < 1e-9
        and parity[name]["null_mismatch"] <= 20
        and parity[name]["unexplained"] == 0
        for name in ("ocf_ni", "accrual", "ocf_debt")
    )
    q2 = bool(
        audit["null_symbol"] == 0
        and audit["null_period_end"] == 0
        and audit["lag_median"] <= 120
        and audit["lag_gt_180_share"] == 0
        and min(audit["hs300_coverage"].values()) >= 0.70
    )
    g5 = strategy_gate(five["3x"]["metrics"], five["3x"]["rolling"], d1_stats, d4_stats, corr5)
    g7 = strategy_gate(seven["3x"]["metrics"], seven["3x"]["rolling"], d1_stats, d4_stats, corr7)
    q3 = all(g5.values())
    q4 = all(g7.values())
    decision = {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4}
    decision["all_pass"] = all(decision.values())

    result = {
        "window": {"phases_5": [str(p) for p in phases_5], "phases_7": [str(p) for p in phases_7], "end": str(END)},
        "pit_audit": audit,
        "parity": parity,
        "d1_ind": d1_stats,
        "d4": d4_stats,
        "five_phase": {"C6_IND": five, "corr_with_v20": corr5, "B80_3x": b80_5, "gates": g5},
        "seven_phase": {"C6_IND": seven, "corr_with_v20": corr7, "B80_3x": b80_7, "gates": g7},
        "decision": decision,
        "final_recommendation": "C6_IND" if decision["all_pass"] else None,
        "final_note": "Q1~Q4 全过 → C6_IND 首选部署候选; Q4 不过 → 5 相位结论保留, 默认维持 B80",
    }
    path = EV / "r33_parity_7phase.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, flush=True)
    print("decision:", decision, "| final:", result["final_recommendation"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
