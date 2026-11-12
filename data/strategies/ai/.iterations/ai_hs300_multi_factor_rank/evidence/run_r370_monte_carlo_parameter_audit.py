"""Monte Carlo and parameter-neighbourhood audit for frozen research candidates."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import statistics as st
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
POLICY_PATH = EVIDENCE / "gate_policy_v2.json"
PREREGISTRATION = EVIDENCE / "r370_mc_parameter_preregistration.json"
R360_RESULT = EVIDENCE / "r360_industry_fade_backtest.json"
R102D_STOP_SCAN = EVIDENCE / "r102d_stop_loss_scan.json"
OUTPUT = EVIDENCE / "r370_monte_carlo_parameter_audit.json"
RESULT_MD = EVIDENCE / "r370_result.md"
AUDIT_DATE = date(2026, 11, 10)
RECENT_START = date(2025, 11, 5)
END = date(2026, 11, 5)

sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("empty quantile input")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _path_metrics(returns: list[float]) -> dict[str, float]:
    value = 1.0
    peak = 1.0
    mdd = 0.0
    for daily_return in returns:
        value *= 1.0 + daily_return
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    volatility = st.pstdev(returns)
    sharpe = st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0
    return {"total_return": value - 1.0, "mdd": mdd, "sharpe": sharpe}


def _bootstrap_summary(path_rows: list[dict[str, float]], *, block_days: int, horizon: int) -> dict[str, Any]:
    totals = [row["total_return"] for row in path_rows]
    mdds = [row["mdd"] for row in path_rows]
    sharpes = [row["sharpe"] for row in path_rows]
    return {
        "paths": len(path_rows),
        "block_days": block_days,
        "horizon_days": horizon,
        "blocks_needed": math.ceil(horizon / block_days),
        "positive_rate": sum(value > 0.0 for value in totals) / len(totals),
        "return_p05": quantile(totals, 0.05),
        "return_p25": quantile(totals, 0.25),
        "return_median": quantile(totals, 0.50),
        "return_p75": quantile(totals, 0.75),
        "return_p95": quantile(totals, 0.95),
        "return_worst": min(totals),
        "return_best": max(totals),
        "mdd_p05": quantile(mdds, 0.05),
        "mdd_median": quantile(mdds, 0.50),
        "mdd_worst": min(mdds),
        "sharpe_p05": quantile(sharpes, 0.05),
        "sharpe_median": quantile(sharpes, 0.50),
    }


def circular_block_bootstrap(
    returns: list[float] | tuple[float, ...],
    *,
    block_days: int = 20,
    horizon: int = 252,
    paths: int = 1000,
    seed: int = 7,
) -> dict[str, Any]:
    """Resample contiguous blocks from a circular return history."""
    values = [float(value) for value in returns]
    if not values or block_days <= 0 or horizon <= 0 or paths <= 0:
        raise ValueError("returns and bootstrap dimensions must be positive")
    if any(not math.isfinite(value) or value <= -1.0 for value in values):
        raise ValueError("returns must be finite and greater than -100%")
    rng = random.Random(seed)
    count = len(values)
    path_rows: list[dict[str, float]] = []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < horizon:
            start = rng.randrange(count)
            sampled.extend(values[(start + offset) % count] for offset in range(block_days))
        path_rows.append(_path_metrics(sampled[:horizon]))
    summary = _bootstrap_summary(path_rows, block_days=block_days, horizon=horizon)
    summary.update({"method": "circular_block", "seed": seed})
    return summary


def stationary_block_bootstrap(
    returns: list[float] | tuple[float, ...],
    *,
    mean_block_days: float = 20.0,
    horizon: int = 252,
    paths: int = 1000,
    seed: int = 7,
) -> dict[str, Any]:
    """Politis-Romano stationary bootstrap with geometric block lengths."""
    values = [float(value) for value in returns]
    if not values or mean_block_days <= 0.0 or horizon <= 0 or paths <= 0:
        raise ValueError("returns and bootstrap dimensions must be positive")
    if any(not math.isfinite(value) or value <= -1.0 for value in values):
        raise ValueError("returns must be finite and greater than -100%")
    rng = random.Random(seed)
    count = len(values)
    continuation_probability = 1.0 - 1.0 / mean_block_days
    path_rows: list[dict[str, float]] = []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < horizon:
            index = rng.randrange(count)
            while len(sampled) < horizon:
                sampled.append(values[index % count])
                index += 1
                if rng.random() >= continuation_probability:
                    break
        path_rows.append(_path_metrics(sampled[:horizon]))
    summary = _bootstrap_summary(path_rows, block_days=round(mean_block_days), horizon=horizon)
    summary.update({"method": "stationary_block", "mean_block_days": mean_block_days, "seed": seed})
    return summary


def _block_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metric_value(block: Any, key: str) -> float | None:
    data = _block_mapping(block)
    raw = data.get(key)
    return float(raw) if isinstance(raw, (int, float)) and math.isfinite(float(raw)) else None


def _normalise_cell(cell: dict[str, Any]) -> dict[str, Any]:
    main = _block_mapping(cell.get("metrics_2x") or cell.get("metrics") or cell)
    stress_block = main.get("stress") or cell.get("stress_3x") or cell.get("metrics_5x")
    stress = _block_mapping(stress_block)
    if "stats" in stress:
        stress = _block_mapping(stress["stats"])
    recent_block = main.get("recent")
    recent = (
        float(recent_block.get("total_return"))
        if isinstance(recent_block, dict) and isinstance(recent_block.get("total_return"), (int, float))
        else float(recent_block)
        if isinstance(recent_block, (int, float))
        else None
    )
    return {
        "stats": _block_mapping(main.get("stats")),
        "stress": stress,
        "yearly": _block_mapping(main.get("yearly")),
        "bootstrap": _block_mapping(main.get("bootstrap")),
        "recent": recent,
    }


def evaluate_parameter_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Apply the layered v2 thresholds; 5x remains diagnostic unless supplied as stress."""
    normalised = _normalise_cell(cell)
    stats = normalised["stats"]
    stress = normalised["stress"]
    yearly = normalised["yearly"]
    bootstrap = normalised["bootstrap"]
    annual = _metric_value(stats, "annual")
    sharpe = _metric_value(stats, "sharpe")
    mdd = _metric_value(stats, "mdd")
    year_2022 = _metric_value(yearly, "2022")
    year_2023 = _metric_value(yearly, "2023")
    recent = normalised["recent"]
    point_checks = {
        "annual": annual is not None and annual >= 0.08,
        "sharpe": sharpe is not None and sharpe >= 1.0,
        "mdd": mdd is not None and mdd >= -0.20,
        "year_2022": year_2022 is not None and year_2022 >= -0.05,
        "year_2023": year_2023 is not None and year_2023 >= 0.0,
        "recent": recent is not None and recent > 0.0,
    }
    if stress:
        point_checks.update(
            {
                "stress_annual": (_metric_value(stress, "annual") or -math.inf) >= 0.05,
                "stress_sharpe": (_metric_value(stress, "sharpe") or -math.inf) >= 0.8,
                "stress_mdd": (_metric_value(stress, "mdd") or -math.inf) >= -0.25,
            }
        )
    bootstrap_positive = _metric_value(bootstrap, "positive_rate")
    bootstrap_mdd_p05 = _metric_value(bootstrap, "mdd_p05")
    bootstrap_checks = {
        "positive_rate": bootstrap_positive is not None and bootstrap_positive >= 0.80,
        "mdd_p05": bootstrap_mdd_p05 is not None and bootstrap_mdd_p05 >= -0.20,
    }
    point_pass = all(point_checks.values())
    bootstrap_pass = all(bootstrap_checks.values())
    return {
        "point_performance_pass": point_pass,
        "bootstrap_pass": bootstrap_pass,
        "joint_pass": point_pass and bootstrap_pass,
        "point_checks": point_checks,
        "bootstrap_checks": bootstrap_checks,
        "failed": [name for name, passed in {**point_checks, **bootstrap_checks}.items() if not passed],
    }


def _compact_metrics(cell: dict[str, Any]) -> dict[str, Any]:
    normalised = _normalise_cell(cell)
    stats = normalised["stats"]
    stress = normalised["stress"]
    return {
        "annual": _metric_value(stats, "annual"),
        "sharpe": _metric_value(stats, "sharpe"),
        "mdd": _metric_value(stats, "mdd"),
        "recent": normalised["recent"],
        "stress_annual": _metric_value(stress, "annual"),
        "stress_sharpe": _metric_value(stress, "sharpe"),
        "stress_mdd": _metric_value(stress, "mdd"),
        "bootstrap_positive_rate": _metric_value(normalised["bootstrap"], "positive_rate"),
        "bootstrap_mdd_p05": _metric_value(normalised["bootstrap"], "mdd_p05"),
    }


def _family_audit(cells: dict[str, Any], *, minimum_pass_rate: float) -> dict[str, Any]:
    audited: dict[str, Any] = {}
    for name, cell in cells.items():
        evaluation = evaluate_parameter_cell(cell)
        parameters = {
            key: cell.get(key)
            for key in ("lookback", "overlay_target", "equity_target", "cap")
            if key in cell
        }
        audited[name] = {
            "parameters": parameters,
            "metrics": _compact_metrics(cell),
            "evaluation": evaluation,
        }
    passed = sum(row["evaluation"]["joint_pass"] for row in audited.values())
    count = len(audited)
    annuals = [row["metrics"]["annual"] for row in audited.values() if row["metrics"]["annual"] is not None]
    sharpes = [row["metrics"]["sharpe"] for row in audited.values() if row["metrics"]["sharpe"] is not None]
    mdds = [row["metrics"]["mdd"] for row in audited.values() if row["metrics"]["mdd"] is not None]
    pass_rate = passed / count if count else 0.0
    return {
        "cells": audited,
        "cell_count": count,
        "pass_count": passed,
        "pass_rate": pass_rate,
        "plateau_pass": count >= 3 and pass_rate >= minimum_pass_rate,
        "annual_range": [min(annuals), max(annuals)] if annuals else None,
        "sharpe_range": [min(sharpes), max(sharpes)] if sharpes else None,
        "mdd_range": [min(mdds), max(mdds)] if mdds else None,
    }


def _parameter_lineages(minimum_pass_rate: float) -> dict[str, Any]:
    sources = {
        "r344": EVIDENCE / "r344_extended_window.json",
        "r350": EVIDENCE / "r350_sleeve_ensemble.json",
        "r354": EVIDENCE / "r354_factor_agreement_gate_backtest.json",
    }
    output: dict[str, Any] = {}
    for name, path in sources.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        families = {
            family: _family_audit(payload[family], minimum_pass_rate=minimum_pass_rate)
            for family in ("overlay_grid", "joint_target_grid", "cap_plateau")
        }
        output[name] = {
            "artifact": path.relative_to(ROOT).as_posix(),
            "artifact_sha256": sha256(path),
            "legacy_all_pass": payload.get("all_pass"),
            "legacy_selected": payload.get("selected"),
            "classification": payload.get("classification"),
            "families": families,
            "v2_plateau_pass": all(family["plateau_pass"] for family in families.values()),
        }
    return output


def _curve_stats(curve: pl.DataFrame) -> dict[str, Any]:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def _year_returns(curve: pl.DataFrame) -> dict[str, float]:
    by_year: dict[int, list[float]] = {}
    for row in curve.iter_rows(named=True):
        by_year.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous_end: float | None = None
    for year in sorted(by_year):
        values = by_year[year]
        start_value = previous_end if previous_end is not None else values[0]
        output[str(year)] = values[-1] / start_value - 1.0
        previous_end = values[-1]
    return output


def _segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    segment = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if segment.height < 2:
        return None
    return float(segment["value"][-1] / segment["value"][0] - 1.0)


def _r360_reconstruction() -> tuple[pl.DataFrame, dict[str, Any]]:
    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r370_r331")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r370_r344")
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r370_r359")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r370_r112")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r370_r114")
    a = (
        pl.read_parquet(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    b = (
        pl.read_parquet(RESEARCH / "r360_cache/2022-05-05_2x.parquet")
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    cohort_starts = [date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1)]
    cohort_curves = [
        pl.read_parquet(
            RESEARCH
            / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet"
        )
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
        for start in cohort_starts
    ]
    c = r359.combine_cohort_curves(cohort_curves, cohort_starts)
    risk, _ = r344._combine_three((a, b, c), r112.ROUND_TRIP_COST, 0.10)
    r114.TARGET_VOL = 0.15
    r114.ROUND_TRIP_COST = 0.0052
    equity = r114._vol_target(risk)["curve"]
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, 0.001)
    r331.LOOKBACK = 60
    r331.TARGET_VOL = 0.15
    r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
    final = r331.build_three_asset_bucket(
        equity,
        gold_proxy,
        treasury,
        dividend,
        round_trip_cost=0.001,
    )["curve"]
    stats = _curve_stats(final)
    frozen = json.loads(R360_RESULT.read_text(encoding="utf-8"))
    frozen_stats = frozen["performance"]["candidate"]["2x"]
    matches = all(
        math.isclose(stats[key], float(frozen_stats[key]), rel_tol=0.0, abs_tol=1e-9)
        for key in ("annual", "sharpe", "mdd", "total_return")
    ) and stats["days"] == int(frozen_stats["days"])
    return final, {
        "stats": stats,
        "matches_frozen_result": matches,
        "frozen_result": frozen_stats,
        "original_preregistered_all_gates_pass": frozen["all_gates_pass"],
        "original_failed_gates": [name for name, passed in frozen["gates"].items() if not passed],
        "reconstruction_inputs": {
            "a": "data/research/r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet",
            "b": "data/research/r360_cache/2022-05-05_2x.parquet",
            "c": [
                f"data/research/r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet"
                for start in cohort_starts
            ],
        },
    }


def _probabilistic_sharpe_ratio(returns: list[float]) -> dict[str, Any]:
    count = len(returns)
    mean = st.mean(returns)
    volatility = st.pstdev(returns)
    if count < 3 or volatility == 0.0:
        return {"available": False, "reason": "insufficient_variation"}
    sharpe = mean / volatility
    skew = sum(((value - mean) / volatility) ** 3 for value in returns) / count
    kurtosis = sum(((value - mean) / volatility) ** 4 for value in returns) / count
    denominator_sq = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2
    if denominator_sq <= 0.0:
        return {"available": False, "reason": "non_positive_variance_adjustment"}
    z_score = sharpe * math.sqrt(count - 1) / math.sqrt(denominator_sq)
    probability = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
    return {
        "available": True,
        "probability_sharpe_above_zero": probability,
        "daily_sharpe": sharpe,
        "skew": skew,
        "kurtosis": kurtosis,
        "observations": count,
        "interpretation": "PSR vs zero only; this is not a deflated Sharpe ratio.",
    }


def _path_monte_carlo(curve: pl.DataFrame, prereg: dict[str, Any]) -> dict[str, Any]:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    methods = prereg["frozen_methods"]
    circular = {
        str(block): circular_block_bootstrap(
            returns,
            block_days=block,
            horizon=methods["path_horizon_days"],
            paths=methods["paths_per_setting"],
            seed=methods["path_seed"],
        )
        for block in methods["circular_block_days"]
    }
    stationary = {
        str(mean_block): stationary_block_bootstrap(
            returns,
            mean_block_days=mean_block,
            horizon=methods["path_horizon_days"],
            paths=methods["paths_per_setting"],
            seed=methods["path_seed"],
        )
        for mean_block in methods["stationary_mean_block_days"]
    }
    seed_sensitivity = [
        circular_block_bootstrap(
            returns,
            block_days=20,
            horizon=methods["path_horizon_days"],
            paths=methods["seed_sensitivity_paths"],
            seed=seed,
        )["positive_rate"]
        for seed in methods["seed_sensitivity_seeds"]
    ]
    return {
        "circular_blocks": circular,
        "stationary_mean_blocks": stationary,
        "seed_sensitivity_block20_positive_rates": seed_sensitivity,
        "seed_sensitivity_range": [min(seed_sensitivity), max(seed_sensitivity)],
        "probabilistic_sharpe_ratio": _probabilistic_sharpe_ratio(returns),
    }


def _latin_hypercube(samples: int, dimensions: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in range(dimensions):
        strata = list(range(samples))
        rng.shuffle(strata)
        columns.append([(strata[index] + rng.random()) / samples for index in range(samples)])
    return [[columns[column][row] for column in range(dimensions)] for row in range(samples)]


def _parameter_surface_monte_carlo(prereg: dict[str, Any]) -> dict[str, Any]:
    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r370_lhs_r331")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r370_lhs_r344")
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r370_lhs_r359")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r370_lhs_r112")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r370_lhs_r114")
    a = (
        pl.read_parquet(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    b = (
        pl.read_parquet(RESEARCH / "r360_cache/2022-05-05_2x.parquet")
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    cohort_starts = [date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1)]
    cohort_curves = [
        pl.read_parquet(
            RESEARCH
            / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet"
        )
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
        for start in cohort_starts
    ]
    c = r359.combine_cohort_curves(cohort_curves, cohort_starts)
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, 0.001)
    ranges = prereg["frozen_methods"]["lhs_ranges"]
    samples = _latin_hypercube(
        prereg["frozen_methods"]["lhs_samples"],
        len(ranges),
        prereg["frozen_methods"]["lhs_seed"],
    )
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    rows: list[dict[str, Any]] = []
    try:
        r114.ROUND_TRIP_COST = 0.0052
        for index, unit_sample in enumerate(samples):
            c_cap = ranges["c_satellite_cap"][0] + unit_sample[0] * (
                ranges["c_satellite_cap"][1] - ranges["c_satellite_cap"][0]
            )
            equity_target = ranges["equity_target_vol"][0] + unit_sample[1] * (
                ranges["equity_target_vol"][1] - ranges["equity_target_vol"][0]
            )
            lookback = round(
                ranges["overlay_lookback_days"][0]
                + unit_sample[2] * (ranges["overlay_lookback_days"][1] - ranges["overlay_lookback_days"][0])
            )
            overlay_target = ranges["overlay_target_vol"][0] + unit_sample[3] * (
                ranges["overlay_target_vol"][1] - ranges["overlay_target_vol"][0]
            )
            equity_weight_max = ranges["overlay_equity_weight_max"][0] + unit_sample[4] * (
                ranges["overlay_equity_weight_max"][1] - ranges["overlay_equity_weight_max"][0]
            )
            risk, _ = r344._combine_three((a, b, c), r112.ROUND_TRIP_COST, c_cap)
            r114.TARGET_VOL = equity_target
            equity = r114._vol_target(risk)["curve"]
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = equity_weight_max
            final = r331.build_three_asset_bucket(
                equity,
                gold_proxy,
                treasury,
                dividend,
                round_trip_cost=0.001,
            )["curve"]
            stats = _curve_stats(final)
            yearly = _year_returns(final)
            recent = _segment_return(final, RECENT_START)
            point_pass = (
                stats["annual"] >= 0.08
                and stats["sharpe"] >= 1.0
                and stats["mdd"] >= -0.20
                and yearly.get("2022", -1.0) >= -0.05
                and yearly.get("2023", -1.0) >= 0.0
                and recent is not None
                and recent > 0.0
            )
            rows.append(
                {
                    "sample": index,
                    "parameters": {
                        "c_satellite_cap": c_cap,
                        "equity_target_vol": equity_target,
                        "overlay_lookback_days": lookback,
                        "overlay_target_vol": overlay_target,
                        "overlay_equity_weight_max": equity_weight_max,
                    },
                    "stats": stats,
                    "yearly": yearly,
                    "recent": recent,
                    "v2_point_pass": point_pass,
                }
            )
    finally:
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331
    annuals = [row["stats"]["annual"] for row in rows]
    sharpes = [row["stats"]["sharpe"] for row in rows]
    mdds = [row["stats"]["mdd"] for row in rows]
    pass_rate = sum(row["v2_point_pass"] for row in rows) / len(rows)
    return {
        "method": "Latin-hypercube perturbation of five portfolio-level parameters",
        "samples": len(rows),
        "seed": prereg["frozen_methods"]["lhs_seed"],
        "ranges": ranges,
        "summary": {
            "v2_point_pass_rate": pass_rate,
            "annual_p05_median_p95": [quantile(annuals, 0.05), quantile(annuals, 0.50), quantile(annuals, 0.95)],
            "sharpe_p05_median_p95": [quantile(sharpes, 0.05), quantile(sharpes, 0.50), quantile(sharpes, 0.95)],
            "mdd_p05_median_worst": [quantile(mdds, 0.05), quantile(mdds, 0.50), min(mdds)],
            "annual_worst": min(annuals),
            "sharpe_worst": min(sharpes),
        },
        "rows": rows,
        "limits": [
            "The underlying stock and sleeve return curves are fixed; this perturbs portfolio construction only.",
            "LHS spans were chosen for audit breadth after the original experiments and are not a new pre-registered alpha test.",
        ],
    }


def _stop_loss_scan_evidence() -> dict[str, Any]:
    payload = json.loads(R102D_STOP_SCAN.read_text(encoding="utf-8"))
    rows = {}
    for name, variant in payload["variants"].items():
        stats = variant["full"]["stats"]
        rows[name] = {
            "stop_loss": variant["stop_loss"],
            "annual": stats["annual_return"],
            "sharpe": stats["sharpe"],
            "mdd": stats["max_drawdown"],
        }
    return {
        "artifact": R102D_STOP_SCAN.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(R102D_STOP_SCAN),
        "scan_points": len(rows),
        "rows": rows,
    }


def _inventory(parameter_lineages: dict[str, Any]) -> list[dict[str, Any]]:
    overlay_qualified = all(
        lineage["families"]["overlay_grid"]["plateau_pass"]
        for lineage in parameter_lineages.values()
    )
    joint_qualified = all(
        lineage["families"]["joint_target_grid"]["plateau_pass"]
        for lineage in parameter_lineages.values()
    )
    cap_qualified = all(
        lineage["families"]["cap_plateau"]["plateau_pass"]
        for lineage in parameter_lineages.values()
    )
    stop_scan = _stop_loss_scan_evidence()
    return [
        {
            "parameter": "stock_rel_industry_lookback_days",
            "surface_value": 20,
            "used_by": "R360 B sleeve score and fade condition",
            "status": "not_monte_carlo_tested",
            "evidence": "Only one frozen value was backtested; no adjacent lookback grid exists.",
        },
        {
            "parameter": "industry_fade_change_days",
            "surface_value": 5,
            "used_by": "R360 fade exit",
            "status": "not_monte_carlo_tested",
            "evidence": "Only one frozen five-session change was backtested.",
        },
        {
            "parameter": "scoring_weights",
            "surface_value": [0.35, 0.15, 0.30, 0.20],
            "used_by": "R101/R360 B sleeve",
            "status": "not_monte_carlo_tested",
            "evidence": "The current weights were inherited, not randomly perturbed in this audit.",
        },
        {
            "parameter": "basic_filter_thresholds",
            "surface_value": {
                "price_min": 3,
                "price_max": 500,
                "market_cap_min": 1_000_000_000,
                "amount_min": 20_000_000,
                "exclude_new_days": 60,
            },
            "used_by": "R360 and earlier B sleeves",
            "status": "not_monte_carlo_tested",
            "evidence": "No adjacent threshold sensitivity experiment was retained.",
        },
        {
            "parameter": "max_hold_days",
            "surface_value": 20,
            "used_by": "R360 B sleeve",
            "status": "historical_scan_only",
            "evidence": "Prior holding studies exist, but no Monte Carlo path-plus-parameter audit around the final R360 portfolio.",
        },
        {
            "parameter": "stop_loss",
            "surface_value": -0.20,
            "used_by": "R360 B sleeve",
            "status": "historical_scan_only",
            "evidence": stop_scan,
        },
        {
            "parameter": "gold_trend_lookback_days",
            "surface_value": 200,
            "used_by": "final defensive gold proxy",
            "status": "not_monte_carlo_tested",
            "evidence": "A single 200-day rule was retained; its neighbouring grid was not tested.",
        },
        {
            "parameter": "regime_gate_states",
            "surface_value": ["weak"],
            "used_by": "A sleeve regime gate",
            "status": "not_monte_carlo_tested",
            "evidence": "The state mapping was frozen before this audit; threshold uncertainty was not sampled.",
        },
        {
            "parameter": "risk_parity_lookback_days",
            "surface_value": 60,
            "grid": [40, 60, 90],
            "used_by": "final defensive bucket",
            "status": "qualified_plateau" if overlay_qualified else "partially_qualified",
            "evidence": "40/60/90-day cells pass v2 point and bootstrap checks in R344, R350 and R354.",
        },
        {
            "parameter": "overlay_target_vol",
            "surface_value": 0.15,
            "grid": [0.12, 0.15, 0.18],
            "used_by": "final defensive bucket",
            "status": "qualified_plateau" if overlay_qualified else "partially_qualified",
            "evidence": "All 12/15/18% overlay cells pass across the three frozen lineages.",
        },
        {
            "parameter": "joint_equity_target_vol",
            "surface_value": 0.15,
            "grid": [0.12, 0.15, 0.18],
            "used_by": "joint equity and overlay risk target",
            "status": "qualified_plateau" if joint_qualified else "partially_qualified",
            "evidence": "The three joint target-vol cells pass v2 re-evaluation in every lineage.",
        },
        {
            "parameter": "c_satellite_cap",
            "surface_value": 0.10,
            "grid": [0.05, 0.10, 0.15, 0.20],
            "used_by": "C-sleeve satellite weight",
            "status": "qualified_plateau" if cap_qualified else "partially_qualified",
            "evidence": "The four cap cells pass v2 re-evaluation in every lineage; legacy 12% gates had rejected them.",
        },
        {
            "parameter": "max_positions",
            "surface_value": 10,
            "grid": [10, 20],
            "used_by": "stock sleeves",
            "status": "historical_scan_only",
            "evidence": "R368 compared 10 versus 20 positions, but did not run a path-bootstrap parameter audit.",
        },
    ]


def build_monte_carlo_audit() -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    minimum_pass_rate = float(prereg["evaluation_thresholds"]["plateau"]["minimum_pass_rate"])
    lineages = _parameter_lineages(minimum_pass_rate)
    curve, reconstruction = _r360_reconstruction()
    path_mc = _path_monte_carlo(curve, prereg)
    surface_mc = _parameter_surface_monte_carlo(prereg)
    inventory = _inventory(lineages)
    stop_scan = _stop_loss_scan_evidence()
    current_cell = {
        "metrics": {
            "stats": reconstruction["stats"],
            "recent": {"total_return": _segment_return(curve, RECENT_START)},
            "yearly": _year_returns(curve),
            "stress": json.loads(R360_RESULT.read_text(encoding="utf-8"))["performance"]["candidate"]["5x"],
            "bootstrap": path_mc["circular_blocks"]["20"],
        }
    }
    current_evaluation = evaluate_parameter_cell(current_cell)
    qualified = [row["parameter"] for row in inventory if row["status"] == "qualified_plateau"]
    partially = [row["parameter"] for row in inventory if row["status"] == "partially_qualified"]
    untested = [row["parameter"] for row in inventory if row["status"] == "not_monte_carlo_tested"]
    scan_only = [row["parameter"] for row in inventory if row["status"] == "historical_scan_only"]
    references = [
        {
            "title": "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality",
            "authors": "Bailey & Lopez de Prado",
            "doi": "10.2139/ssrn.2460551",
            "url": "https://doi.org/10.2139/ssrn.2460551",
        },
        {
            "title": "The Probability of Backtest Overfitting",
            "authors": "Bailey, Borwein, Lopez de Prado & Zhu",
            "doi": "10.2139/ssrn.2326253",
            "url": "https://doi.org/10.2139/ssrn.2326253",
        },
        {
            "title": "The Stationary Bootstrap",
            "authors": "Politis & Romano",
            "doi": "10.1080/01621459.1994.10476870",
            "url": "https://doi.org/10.1080/01621459.1994.10476870",
        },
        {
            "title": "A Reality Check for Data Snooping",
            "authors": "White",
            "doi": "10.1111/1468-0262.00152",
            "url": "https://doi.org/10.1111/1468-0262.00152",
        },
        {
            "title": "A Test for Superior Predictive Ability",
            "authors": "Hansen",
            "doi": "10.1198/073500105000000063",
            "url": "https://doi.org/10.1198/073500105000000063",
        },
        {
            "title": "A comparison of Latin hypercube and simple random sampling",
            "authors": "McKay, Beckman & Conover",
            "doi": "10.1080/00401757.1979.10489755",
            "url": "https://doi.org/10.1080/00401757.1979.10489755",
        },
        {
            "title": "Global sensitivity indices for nonlinear mathematical models and their Monte Carlo estimates",
            "authors": "Sobol",
            "doi": "10.1016/S0378-4754(00)00270-6",
            "url": "https://doi.org/10.1016/S0378-4754(00)00270-6",
        },
        {
            "title": "A survey of cross-validation procedures for model selection",
            "authors": "Arlot & Celisse",
            "doi": "10.1214/09-SS054",
            "url": "https://doi.org/10.1214/09-SS054",
        },
    ]
    return {
        "round": "R370",
        "audit_date": str(AUDIT_DATE),
        "classification": prereg["classification"],
        "methodology": {
            "preregistration": PREREGISTRATION.relative_to(ROOT).as_posix(),
            "preregistration_sha256": sha256(PREREGISTRATION),
            "policy": POLICY_PATH.relative_to(ROOT).as_posix(),
            "policy_sha256": sha256(POLICY_PATH),
            "layers": [
                "daily-return path bootstrap",
                "adjacent-grid v2 re-evaluation",
                "Latin-hypercube portfolio-parameter perturbation",
                "multiple-testing limitations recorded explicitly",
            ],
            "references": references,
        },
        "verdict": {
            "all_parameters_qualified": False,
            "status": "not_all_parameters_qualified",
            "qualified_plateau": qualified,
            "partially_qualified": partially,
            "historical_scan_only": scan_only,
            "not_monte_carlo_tested": untested,
            "r360_current_metrics_pass_v2": current_evaluation["joint_pass"],
            "r360_original_preregistered_gates_pass": reconstruction["original_preregistered_all_gates_pass"],
            "recommended_scope": "research_only_paper_review",
        },
        "r360_reconstruction": reconstruction,
        "r360_current_v2_evaluation": current_evaluation,
        "r360_path_monte_carlo": path_mc,
        "overlay_parameter_latin_hypercube": surface_mc,
        "parameter_lineages": lineages,
        "parameter_inventory": inventory,
        "stop_loss_scan": stop_scan,
        "multiple_testing": {
            "reported_grid_cells": sum(
                family["cell_count"]
                for lineage in lineages.values()
                for family in lineage["families"].values()
            ),
            "trial_ledger_complete": False,
            "pbo_computed": False,
            "deflated_sharpe_computed": False,
            "reason": "The full sequential trial ledger and non-identical trial count are unavailable; correlated grid cells cannot be treated as independent.",
            "probabilistic_sharpe_ratio": path_mc["probabilistic_sharpe_ratio"],
        },
        "decision": {
            "selected_parameter_set": None,
            "no_best_cell_selected": True,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"]
        + [
            "The current R360 metrics pass v2 thresholds, but the original R360 preregistered 2022 nonnegative gate failed.",
            "Portfolio-level parameter robustness does not validate stock-level factor windows or scoring weights.",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    path = report["r360_path_monte_carlo"]
    surface = report["overlay_parameter_latin_hypercube"]
    recon = report["r360_reconstruction"]
    lines = [
        "# R370 · 因子与策略参数蒙特卡洛审计",
        "",
        "结论：**并非所有参数都已合格**。组合层的风险平价、目标波动和 C 卫星权重形成了稳定平台；R360 的行业相对强弱窗口、五日衰减、评分权重等核心 alpha 参数仍没有邻域实验，不能称为合格。",
        "",
        "## R360 重建与路径风险",
        "",
        f"- 冻结 2x 净值重建：年化 {recon['stats']['annual']:.2%}、Sharpe {recon['stats']['sharpe']:.3f}、MDD {recon['stats']['mdd']:.2%}；与冻结结果一致：{recon['matches_frozen_result']}。",
        f"- v2 点估计与 bootstrap 门槛：{report['r360_current_v2_evaluation']['joint_pass']}。",
        f"- 原 R360 预注册门槛：{recon['original_preregistered_all_gates_pass']}，未通过项为 {', '.join(recon['original_failed_gates'])}。",
        "",
        "| 循环块长度 | 252日正收益概率 | 收益P05 | 收益中位数 | MDD P05 |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for block, row in path["circular_blocks"].items():
        lines.append(
            f"| {block} | {row['positive_rate']:.2%} | {row['return_p05']:.2%} | {row['return_median']:.2%} | {row['mdd_p05']:.2%} |"
        )
    lines.extend(
        [
            f"- 20日块正收益概率的多种子范围：{path['seed_sensitivity_range'][0]:.2%} 至 {path['seed_sensitivity_range'][1]:.2%}。",
            f"- PSR（Sharpe>0 的非正态调整概率）：{path['probabilistic_sharpe_ratio'].get('probability_sharpe_above_zero', float('nan')):.4f}。这不是 DSR。",
            "",
            "## 组合参数拉丁超立方扰动",
            "",
            f"- 96 组参数中，v2 点估计通过率：{surface['summary']['v2_point_pass_rate']:.2%}。",
            f"- 年化 P05/中位数/P95：{surface['summary']['annual_p05_median_p95'][0]:.2%} / {surface['summary']['annual_p05_median_p95'][1]:.2%} / {surface['summary']['annual_p05_median_p95'][2]:.2%}。",
            f"- Sharpe P05/中位数/P95：{surface['summary']['sharpe_p05_median_p95'][0]:.3f} / {surface['summary']['sharpe_p05_median_p95'][1]:.3f} / {surface['summary']['sharpe_p05_median_p95'][2]:.3f}。",
            f"- MDD P05/中位数/最差：{surface['summary']['mdd_p05_median_worst'][0]:.2%} / {surface['summary']['mdd_p05_median_worst'][1]:.2%} / {surface['summary']['mdd_p05_median_worst'][2]:.2%}。",
            "",
            "## 参数状态",
            "",
            "| 参数 | 状态 | 结论 |",
            "| --- | --- | --- |",
        ]
    )
    for row in report["parameter_inventory"]:
        lines.append(f"| `{row['parameter']}` | `{row['status']}` | {row['evidence']} |")
    lines.extend(
        [
            "",
            "## 方法边界",
            "",
            "- 蒙特卡洛不能替代样本外检验；块 bootstrap 只重排历史路径，不能制造新的市场状态。",
            "- 现有 3×3 和 cap 网格中的单元格高度相关，不能当作独立试验。完整试验台账缺失，因此本轮不声称 PBO 或 DSR 已通过。",
            "- R360 的行业分类是当前快照回填，不是 point-in-time 数据；历史成分与 ETF 复权限制仍然存在。",
            "- 本轮不选最佳参数、不加杠杆、不生成订单或生产目标。",
            "",
            "## 参考",
            "",
        ]
    )
    for reference in report["methodology"]["references"]:
        lines.append(f"- {reference['authors']} ({reference['title']}). {reference['url']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT_MD, RESULT_MD.with_name(RESULT_MD.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R370 output exists; refusing overwrite: {path}")
    report = build_monte_carlo_audit()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    markdown = _markdown(report)
    RESULT_MD.write_text(markdown, encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(
        f"{sha256(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "r360_stats": report["r360_reconstruction"]["stats"],
                "path_bootstrap": report["r360_path_monte_carlo"]["circular_blocks"],
                "lhs_summary": report["overlay_parameter_latin_hypercube"]["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
