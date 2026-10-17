# 输入说明: 进程内运行 (无需后端; P2/V20 复用 R29 月收益缓存)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r32_pit_recheck.py
# 作用: R32 — PIT 修复后用引擎财务因子复跑 C6_IND, 并做 TTM parity / 数据质量审计。
# 结果 JSON: 同目录 r32_pit_recheck.json。
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
COSTS = r30.COSTS


def build_engine_rebalances() -> tuple[pl.DataFrame, pl.DataFrame]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.services.industry_map import load_industry_l1

    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r23.load_panel(symbols).sort(["symbol", "date"])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    panel = attach_fundamental_factors(
        panel,
        snapshot,
        ["pb_latest", "roe_latest", "debt_ratio_latest", "ocf_ni", "accrual", "ocf_debt"],
    ).with_columns([
        pl.col("pb_latest").alias("pb"),
        pl.col("roe_latest").alias("roe"),
        pl.col("debt_ratio_latest").alias("debt_to_asset_ratio"),
    ])
    reb = r22.build_monthly(panel, r22.monthly_pairs(panel))
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    return reb.join(industry, on="symbol", how="left"), snapshot


def parity_check(snapshot: pl.DataFrame, symbols: list[str]) -> dict:
    offline = (
        r23.derived(r23.to_quarterly(r23.merge_tables(symbols)))
        .select(["symbol", "period_end", "ocf_ni", "accrual", "ocf_debt"])
    )
    engine = snapshot.select(["symbol", "period_end", "ocf_ni", "accrual", "ocf_debt"])
    joined = engine.join(offline, on=["symbol", "period_end"], how="inner", suffix="_off")
    out = {"rows": joined.height}
    for name in ("ocf_ni", "accrual", "ocf_debt"):
        a = joined[name]
        b = joined[f"{name}_off"]
        both = joined.filter(a.is_not_null() & b.is_not_null())
        diff = (both[name] - both[f"{name}_off"]).abs()
        out[name] = {
            "compared_rows": both.height,
            "max_abs_diff": round(float(diff.max()), 12) if both.height else None,
            "null_mismatch": joined.filter(a.is_null() != b.is_null()).height,
        }
    return out


def pit_audit(snapshot: pl.DataFrame, symbols: list[str], hs300: set[str]) -> dict:
    lag = snapshot.with_columns(
        (pl.col("_announce_metrics") - pl.col("period_end")).dt.total_days().alias("_lag")
    )
    sub = snapshot.filter(pl.col("symbol").is_in(symbols))
    coverage = {
        name: round(sub[name].is_not_null().sum() / max(sub.height, 1), 4)
        for name in ("ocf_ni", "accrual", "ocf_debt")
    }
    return {
        "rows": snapshot.height,
        "null_symbol": snapshot["symbol"].null_count(),
        "null_period_end": snapshot["period_end"].null_count(),
        "lag_median": float(lag["_lag"].median()),
        "lag_p90": float(lag["_lag"].quantile(0.9)),
        "lag_gt_180_share": round(float((lag["_lag"] > 180).mean()), 6),
        "hs300_rows": sub.height,
        "hs300_coverage": coverage,
    }


def main() -> int:
    reb, snapshot = build_engine_rebalances()
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    hs300 = set(symbols)
    print("reb rows:", reb.height, flush=True)

    parity = parity_check(snapshot, symbols)
    audit = pit_audit(snapshot, symbols, hs300)
    print("parity:", parity, flush=True)
    print("pit audit:", audit, flush=True)

    # 修正后的月度 IC (报告项)
    monthly_ic = {name: r22.ic_summary(reb, name) for name in ("ocf_ni", "accrual", "ocf_debt", "pb", "roe", "debt_to_asset_ratio")}

    d1_ind = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    d1_frame_2x, d1_stats = r31.series_and_stats(d1_ind, COSTS["2x"][2])
    d4_frame_2x, d4_stats = r31.series_and_stats(d4, COSTS["2x"][2])
    print("D1_IND stats:", d1_stats, "| D4 stats:", d4_stats, flush=True)

    # 5 相位 (与 R31 相同)
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date"])
        .collect()
        .sort("date")
    )
    index_dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    starts_target = [date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    phases = [next(d for d in index_dates if d >= t) for t in starts_target]

    candidate: dict[str, dict] = {}
    for suffix in ("1x", "2x", "3x"):
        d1_frame, _ = r31.series_and_stats(d1_ind, COSTS[suffix][2])
        d4_frame, _ = r31.series_and_stats(d4, COSTS[suffix][2])
        frames = []
        for phase in phases:
            runs = r30.runs_for(phase, suffix)
            frames.append(r28.build_combo(
                {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_frame, d4_frame, suffix
            ))
        candidate[suffix] = {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}
        print(f"[{suffix}] C6_IND sharpe={candidate[suffix]['metrics']['sharpe_mean']} "
              f"mdd={candidate[suffix]['metrics']['mdd_mean']} rolling={candidate[suffix]['rolling']}", flush=True)

    # 与 V20 的相关性 (1×)
    d1_1x, _ = r31.series_and_stats(d1_ind, COSTS["1x"][2])
    d4_1x, _ = r31.series_and_stats(d4, COSTS["1x"][2])
    corrs = []
    for phase in phases:
        runs = r30.runs_for(phase, "1x")
        base = r28.build_combo({"V20": 1.0}, phase, runs, d1_1x, d4_1x, "1x")
        cand = r28.build_combo({"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_1x, d4_1x, "1x")
        joined = cand.rename({"r": "rc"}).join(base.rename({"r": "rb"}), on="ym", how="inner")
        if joined.height > 6 and st.pstdev(joined["rc"].to_list()) > 0 and st.pstdev(joined["rb"].to_list()) > 0:
            corrs.append(st.correlation(joined["rc"].to_list(), joined["rb"].to_list()))
    corr = round(st.mean(corrs), 3) if corrs else None

    # B80 同窗口报告
    empty_d = pl.DataFrame({"ym": [], "r": []})
    b80 = {}
    for suffix in ("1x", "2x", "3x"):
        frames = []
        for phase in phases:
            runs = r30.runs_for(phase, suffix)
            frames.append(r28.build_combo({"B80": 1.0}, phase, runs, empty_d, empty_d, suffix))
        b80[suffix] = {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}

    three = candidate["3x"]
    p1 = parity["rows"] > 10_000 and all(
        parity[name]["max_abs_diff"] is not None and parity[name]["max_abs_diff"] < 1e-9
        for name in ("ocf_ni", "accrual", "ocf_debt")
    )
    p2 = bool(
        audit["null_symbol"] == 0
        and audit["null_period_end"] == 0
        and audit["lag_median"] <= 120
        and audit["lag_gt_180_share"] == 0
        and min(audit["hs300_coverage"].values()) >= 0.70
    )
    p3 = {
        "P3a": three["metrics"]["sharpe_mean"] >= 0.90,
        "P3b": three["rolling"]["positive_rate"] is not None
        and three["rolling"]["positive_rate"] >= 0.90
        and three["rolling"]["worst"] >= -0.08,
        "P3c": bool(
            15 <= d1_stats["members_mean"] <= 35
            and 15 <= d4_stats["members_mean"] <= 35
            and d1_stats["top_industry_share_mean"] <= 0.40
            and d4_stats["top_industry_share_mean"] <= 0.40
        ),
        "P3d": max(d1_stats["turnover_mean"], d4_stats["turnover_mean"]) <= 0.25,
        "P3e": corr is not None and corr < 0.95,
    }
    decision = {"P1": p1, "P2": p2, **p3}
    decision["all_pass"] = all(decision.values())

    control = json.loads((EV / "r31_d1_industry.json").read_text(encoding="utf-8"))
    result = {
        "window": {"start": "2023-01", "end": str(END), "phases": [str(p) for p in phases]},
        "pit_audit": audit,
        "parity": parity,
        "monthly_ic": monthly_ic,
        "d1_ind": {**d1_stats, "standalone_2x": r28.stats(d1_frame_2x), "first_ym": d1_frame_2x["ym"][0], "last_ym": d1_frame_2x["ym"][-1]},
        "d4": {**d4_stats, "standalone_2x": r28.stats(d4_frame_2x), "first_ym": d4_frame_2x["ym"][0], "last_ym": d4_frame_2x["ym"][-1]},
        "candidates": {"C6_IND": candidate},
        "corr_with_v20": {"C6_IND": corr},
        "baseline_b80_report_only": b80,
        "control_r31_stale_pit": {
            "C6_IND": {k: control["candidates"]["C6_IND"][k] for k in ("1x", "2x", "3x")},
            "d1_ind": control["d1_ind"],
            "corr": control["corr_with_v20"]["C6_IND"],
        },
        "decision": decision,
        "final_recommendation": "C6_IND" if decision["all_pass"] else None,
        "final_note": "P1/P2 + P3 全过 → 修正口径被接受, C6_IND 保持部署候选; 否则降级并维持 B80",
    }
    path = EV / "r32_pit_recheck.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, flush=True)
    print("decision:", decision, "| corr:", corr, "| final:", result["final_recommendation"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
