# 输入说明: 进程内运行 (无需后端; P2/V20 复用 R29 月收益缓存)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r31_d1_industry.py
# 作用: R31 预注册 — D1 行业内选 (低PB1/3→高ROE1/3→高ocf_ni1/2, 行业PB非空≥8只) → C6_IND, 同 R30 S1~S5。
# 结果 JSON: 同目录 r31_d1_industry.json。
import importlib.util
import json
import statistics as st
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
END = date(2026, 10, 14)
D1_IND_LEGS = [("pb", "low", 1 / 3), ("roe", "high", 1 / 3), ("ocf_ni", "high", 0.5)]
MIN_INDUSTRY_SIZE = 8


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")
r28 = _load("r28_combos")
r30 = _load("r30_final")

COSTS = r30.COSTS


def build_rebalances() -> pl.DataFrame:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r23.attach_pit(
        r23.load_panel(symbols),
        r23.derived(r23.to_quarterly(r23.merge_tables(symbols))).with_columns(pl.col("announce").alias("_eff")),
    )
    reb = r22.build_monthly(panel, r22.monthly_pairs(panel))
    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    return reb.join(industry, on="symbol", how="left")


def select_members(reb: pl.DataFrame, legs: list, group: list | None = None, min_size: int = 0) -> pl.DataFrame:
    d = reb
    if group:
        first_col = legs[0][0]
        keys = (
            d.filter(pl.col(first_col).is_not_null())
            .group_by(group)
            .len()
            .filter(pl.col("len") >= min_size)
            .select(group)
        )
        d = d.join(keys, on=group, how="inner")
    for col, direction, q in legs:
        d = d.filter(pl.col(col).is_not_null())
        over = group if group else ["date"]
        d = d.with_columns((pl.col(col).rank().over(over) / pl.len().over(over)).alias("_pct"))
        d = d.filter(pl.col("_pct") <= q) if direction == "low" else d.filter(pl.col("_pct") >= 1 - q)
    return d


def series_and_stats(d: pl.DataFrame, cost: float) -> tuple[pl.DataFrame, dict]:
    monthly = (
        d.filter(pl.col("fwd").is_not_null())
        .group_by("date")
        .agg([
            pl.col("fwd").mean().alias("r"),
            pl.col("symbol").alias("symbols"),
            pl.col("industry_l1").alias("inds"),
        ])
        .sort("date")
    )
    rows, sizes, shares, turns, nind = [], [], [], [], []
    prev = None
    for row in monthly.iter_rows(named=True):
        syms = set(row["symbols"])
        turn = 1.0 if prev is None else 1.0 - len(syms & prev) / max(len(syms), 1)
        rows.append({"ym": row["date"].strftime("%Y-%m"), "r": row["r"] - turn * cost})
        sizes.append(len(syms))
        turns.append(turn)
        counts = Counter(i for i in row["inds"] if i is not None)
        shares.append((max(counts.values()) if counts else 0) / max(len(syms), 1))
        nind.append(len(counts))
        prev = syms
    frame = pl.DataFrame(rows).sort("ym")
    stats = {
        "members_mean": round(st.mean(sizes), 1),
        "members_min": min(sizes),
        "members_max": max(sizes),
        "top_industry_share_mean": round(st.mean(shares), 3),
        "top_industry_share_max": round(max(shares), 3),
        "turnover_mean": round(st.mean(turns), 4),
        "industries_mean": round(st.mean(nind), 1),
        "months": len(rows),
    }
    return frame, stats


def membership_overlap(a: pl.DataFrame, b: pl.DataFrame) -> dict:
    def members(d):
        return (
            d.filter(pl.col("fwd").is_not_null())
            .group_by("date")
            .agg(pl.col("symbol").alias("symbols"))
            .sort("date")
        )

    j = members(a).join(members(b), on="date", suffix="_b")
    vals = []
    for row in j.iter_rows(named=True):
        x, y = set(row["symbols"]), set(row["symbols_b"])
        vals.append(len(x & y) / max(len(x | y), 1))
    return {"months": len(vals), "jaccard_mean": round(st.mean(vals), 3) if vals else None}


def yearly_sizes(d: pl.DataFrame) -> dict:
    m = d.filter(pl.col("fwd").is_not_null()).group_by("date").len().sort("date")
    out: dict[str, list[int]] = {}
    for row in m.iter_rows(named=True):
        out.setdefault(str(row["date"].year), []).append(row["len"])
    return {k: round(st.mean(v), 1) for k, v in sorted(out.items())}


def main() -> int:
    reb = build_rebalances()
    print("reb rows:", reb.height, flush=True)

    # 口径自检: D4 用本脚本选择+统计器重建, 必须与 R28 原实现逐月一致
    d4_ours, d4_stats = series_and_stats(select_members(reb, r28.D4_LEGS), COSTS["1x"][2])
    d4_ref = r28.monthly_d_series(r28.D4_LEGS, cost=COSTS["1x"][2])
    check = (
        d4_ours.rename({"r": "ours"}).join(d4_ref.rename({"r": "ref"}), on="ym", how="inner")
        .select((pl.col("ours") - pl.col("ref")).abs().max().alias("max_abs_diff"))
        .item()
    )
    assert check < 1e-9, f"D4 rebuild mismatch: {check}"
    print("D4 rebuild check max_abs_diff =", check, "| stats:", d4_stats, flush=True)

    d1_old = select_members(reb, r28.D1_LEGS)
    d1_ind = select_members(reb, D1_IND_LEGS, group=["date", "industry_l1"], min_size=MIN_INDUSTRY_SIZE)
    _, d1_ind_stats = series_and_stats(d1_ind, COSTS["2x"][2])
    overlap = membership_overlap(d1_old, d1_ind)
    print("D1_IND stats:", d1_ind_stats, "| overlap vs D1:", overlap, flush=True)

    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date"])
        .collect()
        .sort("date")
    )
    index_dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    starts_target = [date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    idx_dates = [next(d for d in index_dates if d >= t) for t in starts_target]

    candidate: dict[str, dict] = {}
    d1_ind_by_cost: dict[str, pl.DataFrame] = {}
    d4_by_cost: dict[str, pl.DataFrame] = {}
    for suffix in ("1x", "2x", "3x"):
        d1_frame, _ = series_and_stats(d1_ind, COSTS[suffix][2])
        d4_frame, _ = series_and_stats(select_members(reb, r28.D4_LEGS), COSTS[suffix][2])
        d1_ind_by_cost[suffix] = d1_frame
        d4_by_cost[suffix] = d4_frame
        frames = []
        for phase in idx_dates:
            runs = r30.runs_for(phase, suffix)
            frames.append(r28.build_combo({"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_frame, d4_frame, suffix))
        candidate[suffix] = {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}
        print(f"[{suffix}] C6_IND sharpe={candidate[suffix]['metrics']['sharpe_mean']} "
              f"mdd={candidate[suffix]['metrics']['mdd_mean']} | rolling={candidate[suffix]['rolling']}", flush=True)

    corrs = []
    for phase in idx_dates:
        runs = r30.runs_for(phase, "1x")
        base = r28.build_combo({"V20": 1.0}, phase, runs, d1_ind_by_cost["1x"], d4_by_cost["1x"], "1x")
        cand = r28.build_combo({"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1_ind_by_cost["1x"], d4_by_cost["1x"], "1x")
        j = cand.rename({"r": "rc"}).join(base.rename({"r": "rb"}), on="ym", how="inner")
        if j.height > 6 and st.pstdev(j["rc"].to_list()) > 0 and st.pstdev(j["rb"].to_list()) > 0:
            corrs.append(st.correlation(j["rc"].to_list(), j["rb"].to_list()))
    corr = round(st.mean(corrs), 3) if corrs else None

    # 补充对照 (仅报告, 未参与任何门槛): 同窗口 B80 的 1x/2x/3x
    empty_d = pl.DataFrame({"ym": [], "r": []})
    b80 = {}
    for suffix in ("1x", "2x", "3x"):
        frames = []
        for phase in idx_dates:
            runs = r30.runs_for(phase, suffix)
            frames.append(r28.build_combo({"B80": 1.0}, phase, runs, empty_d, empty_d, suffix))
        b80[suffix] = {"metrics": r30.phase_metrics(frames), "rolling": r30.rolling_windows(frames)}

    three = candidate["3x"]
    conc_ok = (
        15 <= d1_ind_stats["members_mean"] <= 35
        and 15 <= d4_stats["members_mean"] <= 35
        and d1_ind_stats["top_industry_share_mean"] <= 0.40
        and d4_stats["top_industry_share_mean"] <= 0.40
    )
    decision = {
        "S1": three["metrics"]["sharpe_mean"] >= 0.90,
        "S2": three["rolling"]["positive_rate"] is not None and three["rolling"]["positive_rate"] >= 0.90 and three["rolling"]["worst"] >= -0.08,
        "S3": bool(conc_ok),
        "S4": max(d1_ind_stats["turnover_mean"], d4_stats["turnover_mean"]) <= 0.25,
        "S5": corr is not None and corr < 0.95,
    }
    decision["all_pass"] = all(decision[k] for k in ("S1", "S2", "S3", "S4", "S5"))

    control = json.loads((EV / "r30_final_audit.json").read_text(encoding="utf-8"))
    result = {
        "window": {"start": "2023-01", "end": str(END), "phases": [str(p) for p in idx_dates]},
        "feasibility": {
            "min_industry_size": MIN_INDUSTRY_SIZE,
            "selection": "within [date, industry_l1]: pb low 1/3 -> roe high 1/3 -> ocf_ni high 1/2",
        },
        "d1_ind": {**d1_ind_stats, "yearly_members_mean": yearly_sizes(d1_ind), "overlap_vs_d1": overlap},
        "d4_rebuild": d4_stats,
        "candidates": {"C6_IND": candidate},
        "control_c6_r30": {k: control["candidates"]["C6_50V20_25D1_25D4"][k] for k in ("1x", "2x", "3x")},
        "corr_with_v20": {"C6_IND": corr},
        "baseline_b80_same_window_report_only": b80,
        "stress_checks": {"C6_IND": decision},
        "final_recommendation": "C6_IND" if decision["all_pass"] else None,
        "final_note": "S1~S5 全过 → C6_IND 升级为部署候选; 否则维持 B80 默认",
    }
    path = EV / "r31_d1_industry.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, flush=True)
    print("decision:", decision, "| corr:", corr, "| final:", result["final_recommendation"], flush=True)
    for suffix in ("1x", "2x", "3x"):
        bm = b80[suffix]["metrics"]
        print(f"[{suffix}] B80 report-only sharpe={bm['sharpe_mean']} annual={bm['annual_mean']} "
              f"mdd={bm['mdd_mean']} last_year_min={bm['last_year_min']} rolling={b80[suffix]['rolling']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
