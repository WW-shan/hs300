# 输入说明: 进程内运行 (无需后端; V20 复用 R29 月收益缓存)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r36_execution.py
# 作用: R36 — D1_IND/D4 改为 T+1 开盘成交 + 涨跌停约束, 复验 C6_IND 执行可行性。
# 结果 JSON: 同目录 r36_execution.json。
import bisect
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
START, END = date(2021, 1, 4), date(2026, 10, 14)
COSTS = {"1x": (None, None, 0.0014), "2x": (0.0004, 10.0, 0.0028), "3x": (0.0006, 15.0, 0.0042)}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r28 = _load("r28_combos")
r30 = _load("r30_final")
r31 = _load("r31_d1_industry")
r32 = _load("r32_pit_recheck")
r33 = _load("r33_parity_7phase")


def load_execution_panel(symbols: list[str]) -> pl.DataFrame:
    from app.indicators.pipeline import compute_indicators, compute_limit_signals

    lf = pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
    panel = (
        lf.filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= START)
            & (pl.col("date") <= END)
        )
        .select([
            "symbol", "date", "open", "high", "low", "close", "volume", "amount",
            "turnover_rate", "raw_close", "raw_high", "raw_low",
            "consecutive_limit_ups", "consecutive_limit_downs",
        ])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
        .sort(["symbol", "date"])
    )
    panel = compute_indicators(panel, needed={"change_pct", "vol_ratio_5d"})
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    return compute_limit_signals(
        panel, instruments, needed={"signal_limit_up", "signal_limit_down"}
    )


def t1_gross(members: pl.DataFrame, panel: pl.DataFrame) -> tuple[list[dict], dict]:
    dates = sorted(panel["date"].unique().to_list())
    open_map = {
        (row["symbol"], row["date"]): row["open"]
        for row in panel.select(["symbol", "date", "open"]).iter_rows(named=True)
    }
    up_map = {
        (row["symbol"], row["date"]): bool(row["signal_limit_up"])
        for row in panel.select(["symbol", "date", "signal_limit_up"]).iter_rows(named=True)
        if row["signal_limit_up"] is not None
    }
    down_map = {
        (row["symbol"], row["date"]): bool(row["signal_limit_down"])
        for row in panel.select(["symbol", "date", "signal_limit_down"]).iter_rows(named=True)
        if row["signal_limit_down"] is not None
    }
    by_date = {
        row["date"]: set(row["symbols"])
        for row in members.group_by("date").agg(pl.col("symbol").alias("symbols")).sort("date").iter_rows(named=True)
    }
    member_dates = sorted(by_date)
    rows = []
    prev: set[str] = set()
    total_skipped = total_blocked = 0
    for i, rebalance in enumerate(member_dates[:-1]):
        next_rebalance = member_dates[i + 1]
        entry_index = bisect.bisect_right(dates, rebalance)
        exit_index = bisect.bisect_right(dates, next_rebalance)
        if entry_index >= len(dates) or exit_index >= len(dates):
            continue
        entry_date = dates[entry_index]
        selected = by_date[rebalance]
        returns = []
        skipped = blocked = 0
        for symbol in selected:
            entry_open = open_map.get((symbol, entry_date))
            if entry_open is None or up_map.get((symbol, entry_date), False):
                skipped += 1
                continue
            exit_open = None
            for j in range(exit_index, min(exit_index + 5, len(dates))):
                exit_day = dates[j]
                if down_map.get((symbol, exit_day), False):
                    continue
                exit_open = open_map.get((symbol, exit_day))
                if exit_open is not None:
                    break
            if exit_open is None:
                blocked += 1
                continue
            returns.append(exit_open / entry_open - 1.0)
        gross = st.mean(returns) if returns else 0.0
        turnover = 1.0 if not prev else 1.0 - len(selected & prev) / max(len(selected), 1)
        rows.append({
            "ym": rebalance.strftime("%Y-%m"),
            "gross": gross,
            "turnover": turnover,
            "n": len(returns),
            "skipped_entry": skipped,
            "blocked_exit": blocked,
        })
        total_skipped += skipped
        total_blocked += blocked
        prev = selected
    stats = {
        "months": len(rows),
        "skipped_entry": total_skipped,
        "blocked_exit": total_blocked,
        "members_mean_executed": round(st.mean(r["n"] for r in rows), 1) if rows else None,
    }
    return rows, stats


def cost_series(rows: list[dict], cost: float) -> pl.DataFrame:
    return pl.DataFrame([
        {"ym": row["ym"], "r": row["gross"] - row["turnover"] * cost}
        for row in rows
    ]).sort("ym")


def compare_series(close: pl.DataFrame, opened: pl.DataFrame) -> dict:
    joined = close.rename({"r": "close"}).join(opened.rename({"r": "open"}), on="ym", how="inner")
    if joined.height < 3:
        return {"months": joined.height, "corr": None, "mean_abs_diff": None}
    diffs = (joined["open"] - joined["close"]).abs()
    mean_abs_return = float(joined["close"].abs().mean())
    mean_abs_diff = float(diffs.mean())
    sorted_diffs = sorted(diffs.to_list())
    return {
        "months": joined.height,
        "corr": round(st.correlation(joined["close"].to_list(), joined["open"].to_list()), 3),
        "mean_abs_diff": round(mean_abs_diff, 6),
        "median_abs_diff": round(st.median(sorted_diffs), 6),
        "p90_abs_diff": round(sorted_diffs[int(len(sorted_diffs) * 0.9)], 6),
        "max_abs_diff": round(sorted_diffs[-1], 6),
        "mean_abs_return": round(mean_abs_return, 6),
        "relative_diff": round(mean_abs_diff / mean_abs_return, 4) if mean_abs_return else None,
    }


def combo_frames(phases: list[date], d1: pl.DataFrame, d4: pl.DataFrame, suffix: str) -> list[pl.DataFrame]:
    frames = []
    for phase in phases:
        runs = r30.runs_for(phase, suffix)
        frames.append(r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1, d4, suffix
        ))
    return frames


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    reb, _ = r32.build_engine_rebalances()
    d1_ind = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    panel = load_execution_panel(symbols)
    print("execution panel:", panel.shape, flush=True)

    d1_rows, d1_exec = t1_gross(d1_ind, panel)
    d4_rows, d4_exec = t1_gross(d4, panel)
    print("D1_IND exec:", d1_exec, "| D4 exec:", d4_exec, flush=True)

    d1_open = {suffix: cost_series(d1_rows, COSTS[suffix][2]) for suffix in COSTS}
    d4_open = {suffix: cost_series(d4_rows, COSTS[suffix][2]) for suffix in COSTS}
    d1_close = {suffix: r31.series_and_stats(d1_ind, COSTS[suffix][2])[0] for suffix in COSTS}
    d4_close = {suffix: r31.series_and_stats(d4, COSTS[suffix][2])[0] for suffix in COSTS}

    comparison = {
        "D1_IND": compare_series(d1_close["2x"], d1_open["2x"]),
        "D4": compare_series(d4_close["2x"], d4_open["2x"]),
    }
    print("close vs T+1:", comparison, flush=True)

    phases = r33.phase_dates([
        date(2022, 5, 5), date(2022, 11, 1),
        date(2023, 1, 3), date(2023, 7, 3),
        date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2),
    ])
    t1 = {}
    close = {}
    for suffix in COSTS:
        t1_frames = combo_frames(phases, d1_open[suffix], d4_open[suffix], suffix)
        close_frames = combo_frames(phases, d1_close[suffix], d4_close[suffix], suffix)
        t1[suffix] = {"metrics": r30.phase_metrics(t1_frames), "rolling": r30.rolling_windows(t1_frames)}
        close[suffix] = {"metrics": r30.phase_metrics(close_frames), "rolling": r30.rolling_windows(close_frames)}
        print(f"[{suffix}] T+1 sharpe={t1[suffix]['metrics']['sharpe_mean']} "
              f"mdd={t1[suffix]['metrics']['mdd_mean']} rolling={t1[suffix]['rolling']}", flush=True)

    corrs = []
    for phase in phases:
        runs = r30.runs_for(phase, "3x")
        close_frame = r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs,
            d1_close["3x"], d4_close["3x"], "3x",
        )
        t1_frame = r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs,
            d1_open["3x"], d4_open["3x"], "3x",
        )
        joined = close_frame.rename({"r": "close"}).join(t1_frame.rename({"r": "open"}), on="ym", how="inner")
        if joined.height > 6 and st.pstdev(joined["close"].to_list()) > 0 and st.pstdev(joined["open"].to_list()) > 0:
            corrs.append(st.correlation(joined["close"].to_list(), joined["open"].to_list()))
    corr_t1_close = round(st.mean(corrs), 3) if corrs else None

    # D 腿集中度/换手沿用 R33 的选择统计
    _, d1_stats = r31.series_and_stats(d1_ind, COSTS["2x"][2])
    _, d4_stats = r31.series_and_stats(d4, COSTS["2x"][2])
    e1 = all(
        comparison[name]["corr"] is not None and comparison[name]["corr"] >= 0.95
        and comparison[name]["mean_abs_diff"] is not None and comparison[name]["mean_abs_diff"] <= 0.003
        for name in ("D1_IND", "D4")
    )
    three = t1["3x"]
    e2 = three["metrics"]["sharpe_mean"] >= 1.10
    e3 = three["rolling"]["positive_rate"] >= 0.90 and three["rolling"]["worst"] >= -0.08
    e4 = three["metrics"]["annual_mean"] >= 0.22 and three["metrics"]["mdd_mean"] >= -0.14
    e5 = corr_t1_close is not None and corr_t1_close >= 0.95
    e6 = bool(
        15 <= d1_stats["members_mean"] <= 35
        and 15 <= d4_stats["members_mean"] <= 35
        and d1_stats["top_industry_share_mean"] <= 0.40
        and d4_stats["top_industry_share_mean"] <= 0.40
        and max(d1_stats["turnover_mean"], d4_stats["turnover_mean"]) <= 0.25
    )
    decision = {"E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6}
    decision["all_pass"] = all(decision.values())

    result = {
        "window": {"start": "2022-05", "end": str(END), "phases": [str(p) for p in phases]},
        "execution": {"D1_IND": d1_exec, "D4": d4_exec},
        "close_vs_t1": comparison,
        "t1": t1,
        "close": close,
        "corr_t1_vs_close": corr_t1_close,
        "d1_stats": d1_stats,
        "d4_stats": d4_stats,
        "decision": decision,
        "final_recommendation": "C6_IND" if decision["all_pass"] else None,
        "final_note": "E1~E6 全过 → 具备执行可行性; 否则记录执行拖累, 保持研究候选",
    }
    path = EV / "r36_execution.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("decision:", decision, "| corr T+1 vs close:", corr_t1_close, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
