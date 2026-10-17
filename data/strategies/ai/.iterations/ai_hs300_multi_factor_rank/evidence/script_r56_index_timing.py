# 输入说明: R56 — HS300 指数趋势/波动率目标/回撤择时。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r56_index_timing.py
# 结果 JSON: 同目录 r56_index_timing.json。
from __future__ import annotations

import importlib.util
import json
import math
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
r30 = _load("r30", "script_r30_final.py")
END = date(2026, 10, 16)
FULL_START = date(2019, 1, 2)
LAST_YEAR_START = date(2025, 10, 14)
COST = 0.0028
CANDIDATES = ("MA200", "MOM252", "MA50_200", "VOLTARGET15", "TREND_VOL15", "DD10_HALF")


def _index() -> pl.DataFrame:
    paths = sorted((r40.DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(p) for p in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open", "close"])
        .collect()
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Date))
        .filter(pl.col("date") <= END)
    )


def _signals(df: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns([
        (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("ret"),
        pl.col("close").rolling_mean(50).alias("ma50"),
        pl.col("close").rolling_mean(200).alias("ma200"),
        (pl.col("close") / pl.col("close").shift(252) - 1.0).alias("mom252"),
        pl.col("close").rolling_max(252).alias("_high252"),
    ]).with_columns([
        pl.col("ret").rolling_std(20).alias("_vol20"),
    ]).with_columns([
        (pl.col("_vol20") * math.sqrt(252.0)).alias("vol20"),
        (pl.col("close") / pl.col("_high252") - 1.0).alias("dd252"),
    ])
    return df.with_columns([
        (pl.col("close") > pl.col("ma200")).cast(pl.Float64).fill_null(0.0).shift(1).alias("_p_ma200"),
        (pl.col("mom252") > 0).cast(pl.Float64).fill_null(0.0).shift(1).alias("_p_mom252"),
        (pl.col("ma50") > pl.col("ma200")).cast(pl.Float64).fill_null(0.0).shift(1).alias("_p_ma50_200"),
        (0.15 / pl.col("vol20")).clip(0.0, 1.0).fill_null(0.0).shift(1).alias("_p_vol"),
        (((pl.col("close") > pl.col("ma200")).cast(pl.Float64)) * (0.15 / pl.col("vol20")).clip(0.0, 1.0)).fill_null(0.0).shift(1).alias("_p_trend_vol"),
        pl.when(pl.col("dd252") <= -0.10).then(0.5).otherwise(1.0).fill_null(1.0).shift(1).alias("_p_dd10"),
    ])


def _daily_metrics(frame: pl.DataFrame, col: str) -> dict:
    vals = frame[col].drop_nulls().to_list()
    if not vals:
        return {"n": 0, "annual": None, "sharpe": None, "mdd": None, "last_year": None}
    total = 1.0
    for v in vals:
        total *= 1.0 + v
    n = len(vals)
    mean = sum(vals) / n
    sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    peak = value = 1.0
    mdd = 0.0
    for v in vals:
        value *= 1.0 + v
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    last = [v for d, v in zip(frame["date"].to_list(), vals, strict=False) if d >= LAST_YEAR_START]
    last_total = 1.0
    for v in last:
        last_total *= 1.0 + v
    return {
        "n": n,
        "annual": round(total ** (252.0 / n) - 1.0, 4),
        "sharpe": round(mean / sd * math.sqrt(252.0), 3) if sd > 0 else None,
        "mdd": round(mdd, 4),
        "last_year": round(last_total - 1.0, 4),
    }


def _monthly(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    return (
        frame.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .group_by("ym")
        .agg((pl.col(col) + 1.0).product().alias("r") - 1.0)
        .sort("ym")
    )


def _phase_metrics(frame: pl.DataFrame, col: str, phases: list[str]) -> tuple[dict, dict]:
    monthly = _monthly(frame, col)
    frames = [monthly.filter(pl.col("ym") >= p) for p in phases]
    return r30.phase_metrics(frames), r30.rolling_windows(frames)


def _positions(df: pl.DataFrame) -> dict[str, pl.Expr]:
    return {
        "MA200": pl.col("_p_ma200"),
        "MOM252": pl.col("_p_mom252"),
        "MA50_200": pl.col("_p_ma50_200"),
        "VOLTARGET15": pl.col("_p_vol"),
        "TREND_VOL15": pl.col("_p_trend_vol"),
        "DD10_HALF": pl.col("_p_dd10"),
    }


def main() -> int:
    index_raw = _signals(_index())
    raw = index_raw.filter(pl.col("date") >= FULL_START)
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]
    # Baseline return and phase metrics.
    base_daily = raw.with_columns(pl.col("ret").alias("r"))
    base_m, base_roll = _phase_metrics(base_daily, "r", phases)
    base_full = _daily_metrics(base_daily.filter(pl.col("ret").is_not_null()), "r")
    result_candidates = {}
    for name, pos in _positions(raw).items():
        # Position is already shifted by one day; previous position for cost.
        p = raw.with_columns(pos.alias("position")).with_columns(
            pl.col("position").shift(1).fill_null(0.0).alias("_prev_position")
        ).with_columns(
            (pl.col("position") - pl.col("_prev_position")).abs().alias("turnover")
        ).with_columns(
            (pl.col("position") * pl.col("ret") - COST * pl.col("turnover")).alias("r")
        )
        daily = p.filter(pl.col("ret").is_not_null())
        metrics, rolling = _phase_metrics(daily, "r", phases)
        full = _daily_metrics(daily, "r")
        years = daily.height / 252.0
        turnover = round(float(daily["turnover"].sum() / years), 2) if years else None
        cost = round(float((COST * daily["turnover"]).sum() / years), 4) if years else None
        phase_sharpes = [p0["sharpe"] for p0 in metrics["phases"]]
        base_sharpes = [p0["sharpe"] for p0 in base_m["phases"]]
        result_candidates[name] = {
            "metrics": metrics, "rolling": rolling, "full": full,
            "turnover_annual": turnover, "cost_annual": cost,
            "phase_better": sum(1 for a, b in zip(phase_sharpes, base_sharpes, strict=False) if a is not None and b is not None and a > b),
        }
    # Gate decisions are computed after all candidates so output schema is consistent.
    decision = {}
    for name, rec in result_candidates.items():
        m, full = rec["metrics"], rec["full"]
        decision[name] = {
            "G1": bool(m["sharpe_mean"] >= base_m["sharpe_mean"] + 0.15),
            "G2": bool(m["annual_mean"] >= base_m["annual_mean"]),
            "G3": bool(m["mdd_mean"] >= base_m["mdd_mean"] - 0.03),
            "G4": bool(rec["phase_better"] >= 5),
            "G5": bool(full["sharpe"] is not None and base_full["sharpe"] is not None and full["sharpe"] >= base_full["sharpe"] + 0.10 and full["mdd"] >= base_full["mdd"] - 0.03),
            "G6": bool(rec["turnover_annual"] is not None and rec["turnover_annual"] <= 8.0),
            "G7": bool(full["last_year"] is not None and base_full["last_year"] is not None and full["last_year"] >= base_full["last_year"]),
        }
    # Data quality.
    years = (END - FULL_START).days / 365.25
    expected_days = max(1.0, 252.0 * years)
    all_dates = index_raw["date"].to_list()
    all_gaps = [(all_dates[i] - all_dates[i - 1]).days for i in range(1, len(all_dates))]
    data_quality = {
        "rows_total": index_raw.height,
        "rows_after_warmup": raw.height,
        "date_min": str(index_raw["date"].min()),
        "date_max": str(index_raw["date"].max()),
        "coverage_start": str(FULL_START),
        "coverage_ratio": round(raw.height / expected_days, 4),
        "max_calendar_gap_days": max(all_gaps) if all_gaps else None,
        "D1": bool(index_raw.height >= 2000 and raw.height / expected_days >= 0.95 and (not all_gaps or max(all_gaps) <= 10)),
        "D1_holiday_corrected": bool(index_raw.height >= 2000 and raw.height / expected_days >= 0.95 and (not all_gaps or max(all_gaps) <= 15)),
    }
    for name in CANDIDATES:
        decision[name]["D1"] = data_quality["D1"]
        decision[name]["all_pass"] = all(decision[name].values())
    passed = [name for name in CANDIDATES if decision[name]["all_pass"]]
    result = {
        "window": {"full_start": str(FULL_START), "end": str(END), "phases": phases, "cost": COST},
        "data": data_quality,
        "base": {"phase_metrics": base_m, "phase_rolling": base_roll, "full": base_full},
        "candidates": result_candidates,
        "decision": decision,
        "passed": passed,
        "recommendation": "R57 stock portfolio overlay" if passed else "negative knowledge; do not deploy",
        "caveats": ["现金收益按 0 处理", "仓位上限 1, 不使用杠杆", "指数 close-to-close, 信号滞后一天", "未计 ETF 跟踪误差和冲击成本"],
    }
    path = EV / "r56_index_timing.json"
    path.write_text(json.dumps(r30.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("base", base_full, "D1", data_quality["D1"], flush=True)
    print("decision", decision, "passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
