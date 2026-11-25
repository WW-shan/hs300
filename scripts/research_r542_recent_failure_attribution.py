#!/usr/bin/env python3
"""R542: diagnose the frozen R539C recent-year failure with formal evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r542_recent_failure_attribution_preregistration.json"
)
RESEARCH_ID = "R542"
RUNNER = "scripts/research_r542_recent_failure_attribution.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R539_RESULT = RESEARCH / "r539_exit_risk_grid_result.json"
R539_PREREG = RESEARCH / "r539_exit_risk_grid_preregistration.json"
R536_RESULT = RESEARCH / "r536a_finalist_cluster_fdr_result.json"
R536_PREREG = RESEARCH / "r536a_finalist_cluster_fdr_preregistration.json"
MEMBERSHIP = (
    RESEARCH / "r526_unified_pit_bundle_window" / "daily_snapshots.parquet"
)
INDUSTRY = RESEARCH / "r526_unified_pit_bundle_window" / "industry_history.parquet"
REGIME = DATA / "regime_history" / "part.parquet"
PREREG = RESEARCH / "r542_recent_failure_attribution_preregistration.json"
RESULT = RESEARCH / "r542_recent_failure_attribution_result.json"
MARKDOWN = RESEARCH / "r542_recent_failure_attribution_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
STOP_LOSS = -0.12
EXPECTED_FACTORS = (
    "amount_level",
    "bollinger_width",
    "dividend_yield_quantile",
    "fund_count_quantile",
    "fund_holding_ratio_absolute",
    "lhb_inst_net_buy_event",
    "lhb_net_buy_holding",
    "market_down_beta",
    "max_return",
    "repurchase_event_decay",
    "repurchase_holding",
    "turnover_level",
    "vol_of_vol",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _load_module(name: str) -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"immutable research artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def curve_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    values = [float(row["value"]) for row in curve]
    return [
        (
            str(curve[index]["date"])[:10],
            values[index] / values[index - 1] - 1.0,
        )
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]


def drawdown_episode(curve: list[dict[str, Any]]) -> dict[str, Any]:
    if len(curve) < 2:
        raise ValueError("drawdown curve requires at least two points")
    values = [float(row["value"]) for row in curve]
    dates = [str(row["date"])[:10] for row in curve]
    running_peak = values[0]
    current_peak_index = 0
    worst = 0.0
    peak_index = 0
    trough_index = 0
    for index, value in enumerate(values):
        if value > running_peak:
            running_peak = value
            current_peak_index = index
        drawdown = value / running_peak - 1.0 if running_peak > 0 else 0.0
        if drawdown < worst:
            worst = drawdown
            peak_index = current_peak_index
            trough_index = index
    recovery_index = None
    peak_value = values[peak_index]
    for index in range(trough_index + 1, len(values)):
        if values[index] >= peak_value:
            recovery_index = index
            break
    return {
        "peak_date": dates[peak_index],
        "trough_date": dates[trough_index],
        "recovery_date": dates[recovery_index] if recovery_index is not None else None,
        "peak_value": round(values[peak_index], 6),
        "trough_value": round(values[trough_index], 6),
        "max_drawdown": round(worst, 8),
        "recovered": recovery_index is not None,
    }


def portfolio_beta(
    curve: list[dict[str, Any]],
    benchmark: dict[str, float],
) -> dict[str, Any]:
    if not benchmark:
        raise ValueError("benchmark returns are empty")
    strategy = dict(curve_returns(curve))
    days = sorted(set(strategy).intersection(benchmark))
    if len(days) < 3:
        raise ValueError("benchmark/strategy overlap requires at least three days")
    x = np.array([benchmark[day] for day in days], dtype=float)
    y = np.array([strategy[day] for day in days], dtype=float)
    variance = float(np.var(x, ddof=1))
    beta = float(np.cov(x, y, ddof=1)[0, 1] / variance) if variance > 0 else 0.0
    correlation = float(np.corrcoef(x, y)[0, 1]) if variance > 0 and np.var(y) > 0 else 0.0
    alpha_daily = float(np.mean(y) - beta * np.mean(x))
    return {
        "beta": round(beta, 6),
        "correlation": round(correlation, 6),
        "alpha_annualized": round(alpha_daily * 252.0, 6),
        "strategy_annualized_vol": round(float(np.std(y, ddof=1)) * math.sqrt(252.0), 6),
        "benchmark_annualized_vol": round(float(np.std(x, ddof=1)) * math.sqrt(252.0), 6),
        "n_days": len(days),
    }


def quintile_spread(
    panel: pl.DataFrame,
    factor_column: str,
    return_column: str,
    *,
    direction: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    required = {"date", "symbol", factor_column, return_column}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"quintile panel missing columns: {missing}")
    if direction not in {"high", "low"}:
        raise ValueError("direction must be high or low")
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    work = (
        panel.select(["date", "symbol", factor_column, return_column])
        .with_columns(pl.col("date").cast(pl.Utf8).str.to_date())
        .filter(
            (pl.col("date") >= start_date)
            & (pl.col("date") <= end_date)
            & pl.col(factor_column).is_not_null()
            & pl.col(factor_column).is_finite()
            & pl.col(return_column).is_not_null()
            & pl.col(return_column).is_finite()
        )
    )
    if work.is_empty():
        return {
            "n_dates": 0,
            "top_mean": None,
            "bottom_mean": None,
            "spread": None,
        }
    directional = (
        pl.col(factor_column)
        if direction == "high"
        else -pl.col(factor_column)
    )
    grouped = (
        work.with_columns(directional.alias("_directional"))
        .with_columns(
            (
                (pl.col("_directional").rank("average").over("date") - 1.0)
                / pl.len().over("date")
                * 5.0
            )
            .floor()
            .clip(0, 4)
            .cast(pl.Int8)
            .alias("_quintile")
        )
        .group_by(["date", "_quintile"])
        .agg(pl.col(return_column).mean().alias("_mean_return"))
    )
    top = grouped.filter(pl.col("_quintile") == 4).select("date", pl.col("_mean_return").alias("top"))
    bottom = grouped.filter(pl.col("_quintile") == 0).select("date", pl.col("_mean_return").alias("bottom"))
    daily = top.join(bottom, on="date", how="inner").sort("date")
    if daily.is_empty():
        return {
            "n_dates": 0,
            "top_mean": None,
            "bottom_mean": None,
            "spread": None,
        }
    return {
        "n_dates": daily.height,
        "top_mean": round(float(daily["top"].mean()), 8),
        "bottom_mean": round(float(daily["bottom"].mean()), 8),
        "spread": round(float((daily["top"] - daily["bottom"]).mean()), 8),
    }


def loo_contribution(baseline_return: float, without_return: float) -> float:
    return float(baseline_return) - float(without_return)


def regime_attribution(
    curve: list[dict[str, Any]],
    benchmark: dict[str, float],
    regime: pl.DataFrame,
) -> dict[str, Any]:
    if not benchmark:
        raise ValueError("benchmark returns are empty")
    if regime.is_empty() or not {"date", "state"}.issubset(regime.columns):
        raise ValueError("regime frame must contain date and state")
    strategy = dict(curve_returns(curve))
    regime_map = {
        str(row["date"])[:10]: row
        for row in regime.select(
            [column for column in ("date", "state", "phase") if column in regime.columns]
        ).iter_rows(named=True)
    }
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for day in sorted(set(strategy).intersection(benchmark).intersection(regime_map)):
        row = regime_map[day]
        grouped[str(row["state"])].append(
            (strategy[day], benchmark[day])
        )
    states = []
    for state, values in sorted(grouped.items()):
        strategy_values = [value[0] for value in values]
        benchmark_values = [value[1] for value in values]
        excess = [left - right for left, right in values]
        states.append({
            "state": state,
            "days": len(values),
            "mean_strategy_daily": round(float(st.mean(strategy_values)), 8),
            "mean_benchmark_daily": round(float(st.mean(benchmark_values)), 8),
            "mean_excess_daily": round(float(st.mean(excess)), 8),
            "strategy_compounded": round(
                float(math.prod(1.0 + value for value in strategy_values) - 1.0),
                8,
            ),
            "benchmark_compounded": round(
                float(math.prod(1.0 + value for value in benchmark_values) - 1.0),
                8,
            ),
        })
    return {
        "coverage": {
            "strategy_days": len(strategy),
            "benchmark_days": len(benchmark),
            "regime_days": len(regime_map),
            "matched_days": sum(row["days"] for row in states),
            "regime_start": min(regime_map) if regime_map else None,
            "regime_end": max(regime_map) if regime_map else None,
        },
        "states": states,
    }


def monthly_attribution(
    curve: list[dict[str, Any]],
    hs300: dict[str, float],
    equal_weight: dict[str, float],
) -> list[dict[str, Any]]:
    strategy = dict(curve_returns(curve))
    output = []
    for month in sorted({day[:7] for day in strategy}):
        days = sorted(day for day in strategy if day.startswith(month))
        strategy_return = math.prod(1.0 + strategy[day] for day in days) - 1.0
        hs_days = [hs300[day] for day in days if day in hs300]
        ew_days = [equal_weight[day] for day in days if day in equal_weight]
        hs_return = math.prod(1.0 + value for value in hs_days) - 1.0 if hs_days else None
        ew_return = math.prod(1.0 + value for value in ew_days) - 1.0 if ew_days else None
        output.append({
            "month": month,
            "strategy": round(strategy_return, 8),
            "hs300": round(hs_return, 8) if hs_return is not None else None,
            "equal_weight": round(ew_return, 8) if ew_return is not None else None,
            "excess_hs300": round(strategy_return - hs_return, 8) if hs_return is not None else None,
            "excess_equal_weight": round(strategy_return - ew_return, 8) if ew_return is not None else None,
        })
    return output


def _period_return(returns: dict[str, float], start: date, end: date) -> float:
    days = [day for day in sorted(returns) if start < date.fromisoformat(day) <= end]
    return round(float(math.prod(1.0 + returns[day] for day in days) - 1.0), 8)


def hs300_returns() -> dict[str, float]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    closes = [float(value) for value in frame["close"].to_list()]
    return {
        dates[index]: closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(dates))
    }


def equal_weight_returns(study: dict[str, Any]) -> dict[str, float]:
    prices = study["context"].prices.select(["symbol", "date", "close"])
    membership = study["membership"].select(["symbol", "date"])
    frame = (
        prices.join(membership, on=["symbol", "date"], how="inner")
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("return")
        )
        .filter(pl.col("return").is_not_null() & pl.col("return").is_finite())
        .group_by("date")
        .agg(pl.col("return").mean().alias("return"))
        .sort("date")
    )
    return {
        str(row["date"])[:10]: float(row["return"])
        for row in frame.iter_rows(named=True)
    }


def scientific_design() -> dict[str, Any]:
    return {
        "model": "frozen_project_portfolio_attribution",
        "representatives": list(EXPECTED_FACTORS),
        "single_leg_runs": len(EXPECTED_FACTORS) * 2,
        "leave_one_out_runs": len(EXPECTED_FACTORS) * 2,
        "baseline_runs": 2,
        "no_stop_control_runs": 2,
        "formal_runs": len(EXPECTED_FACTORS) * 4 + 4,
        "windows": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "industry_cap": INDUSTRY_CAP,
            "stop_loss": STOP_LOSS,
            "trailing_stop": None,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "selection_performed": False,
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    prereg = root / RESEARCH_PREREGISTRATION
    if prereg.exists() or prereg.with_name(prereg.name + ".sha256").exists():
        raise FileExistsError(f"R542 preregistration already exists: {prereg}")
    sources = {
        "r539_result": root / R539_RESULT.relative_to(ROOT),
        "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
        "r536_result": root / R536_RESULT.relative_to(ROOT),
        "r536_preregistration": root / R536_PREREG.relative_to(ROOT),
        "r526_membership": root / MEMBERSHIP.relative_to(ROOT),
        "r526_industry": root / INDUSTRY.relative_to(ROOT),
        "regime_history": root / REGIME.relative_to(ROOT),
    }
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Which factor, portfolio, exposure, execution, and market-regime "
            "components explain the frozen R539C recent-year loss?"
        ),
        "hypothesis": (
            "The recent loss may be driven by market beta and exposure rather "
            "than a uniform collapse in all 13 factor ICs."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in sources.items()},
        "parameters": design,
        "sample": design["windows"],
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "baseline_full_reproduced": True,
            "baseline_recent_reproduced": True,
            "formal_runs": design["formal_runs"],
            "single_leg_runs": design["single_leg_runs"],
            "leave_one_out_runs": design["leave_one_out_runs"],
            "factor_ic_diagnostics": len(EXPECTED_FACTORS),
            "selection_performed": False,
            "diagnostic_only_attribution": True,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "formal_evidence_saved_lookahead_t1",
            "baseline_reproduces_r539c_full_and_recent",
            "all_13_factor_ic_and_quintiles",
            "all_single_leg_and_leave_one_out_runs",
            "no_factor_or_weight_selection",
            "regime_coverage_gap_is_explicit",
            "equal_weight_benchmark_is_costless_diagnostic",
            "no_orders_or_active_version",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(prereg, raw)
    return prereg


def _verify_runner_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R542 runner hash no longer matches preregistration")
    for name, expected in prereg["input_hashes"].items():
        path = {
            "r539_result": root / R539_RESULT.relative_to(ROOT),
            "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
            "r536_result": root / R536_RESULT.relative_to(ROOT),
            "r536_preregistration": root / R536_PREREG.relative_to(ROOT),
            "r526_membership": root / MEMBERSHIP.relative_to(ROOT),
            "r526_industry": root / INDUSTRY.relative_to(ROOT),
            "regime_history": root / REGIME.relative_to(ROOT),
        }[name]
        if sha256(path) != expected:
            raise RuntimeError(f"R542 input hash changed: {name}")


def load_runtime(root: Path) -> dict[str, Any]:
    r537 = _load_module("research_r537_expanded_factor_portfolio")
    r539 = _load_module("research_r539_exit_risk_grid")
    runtime = r537.load_runtime(root)
    r539.load_source(root)
    representatives = runtime["representatives"]
    if tuple(sorted(row["family_id"] for row in representatives)) != tuple(sorted(EXPECTED_FACTORS)):
        raise RuntimeError("R542 representative set changed")
    runtime["industries"] = runtime["runtime"]["industries"]
    runtime["r539"] = r539
    study = runtime["runtime"]["study"]
    runtime["frames"] = {
        row["family_id"]: study["r530"]._factor_frame(
            runtime["candidates"][row["family_id"]].candidate,
            runtime["candidates"][row["family_id"]].candidate.parameter,
            study["context"],
        )
        for row in representatives
    }
    return runtime


def _variant(runtime: dict[str, Any], variant_id: str) -> dict[str, Any]:
    return next(row for row in runtime["r539"].exit_grid() if row["variant_id"] == variant_id)


def build_member_panel(
    runtime: dict[str, Any],
    members: list[dict[str, Any]],
) -> pl.DataFrame:
    study = runtime["runtime"]["study"]
    core = study["r531"]._core_frame(
        members,
        {row["family_id"]: runtime["candidates"][row["family_id"]] for row in members},
        {row["family_id"]: runtime["frames"][row["family_id"]] for row in members},
        study["membership"],
    )
    score = runtime["r533"].scaled_score_panel(core)
    control = runtime["panel"].select("symbol", "date", "core_score")
    return score.join(control, on=["symbol", "date"], how="inner").sort(["date", "symbol"])


def run_formal_window(
    p5: Any,
    service: Any,
    prepared: Any,
    runtime: dict[str, Any],
    variant: dict[str, Any],
    *,
    label: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    r533 = runtime["r533"]
    config = r533.build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        max_hold_days=MAX_HOLD_DAYS,
        cost_label="2x",
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": variant["stop_loss"],
        "trailing_stop": variant["trailing_stop"],
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": INDUSTRY_CAP,
    }
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R542:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R542:{label}")
    if result.error:
        raise RuntimeError(f"R542:{label}: {result.error}")
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "stats": p5._select_stats(result.stats),
        "trade_count": len(result.trades),
        "curve": list(result.equity_curve),
        "trades": [
            trade if isinstance(trade, dict) else service._trade_to_dict(trade)
            for trade in result.trades
        ],
        "formal_evidence": r533._evidence_summary(evidence),
    }


def run_panel(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    label_prefix: str,
    *,
    variant: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    output = {}
    local_runtime = {**runtime, "panel": panel}
    for window, start, end in (
        ("full", TRAIN_START, END),
        ("recent", RECENT_START, END),
    ):
        prepared = runtime["r539"].prepare_exit_window(
            p5,
            service,
            local_runtime,
            variant,
            start=start,
            end=end,
        )
        run = run_formal_window(
            p5,
            service,
            prepared,
            local_runtime,
            variant,
            label=f"{label_prefix}_{window}",
            start=start,
            end=end,
        )
        output[window] = run
        print(
            label_prefix,
            window,
            run["stats"]["total_return"],
            run["stats"]["max_drawdown"],
            run["trade_count"],
            flush=True,
        )
    return output


def factor_ic_diagnostics(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    study = runtime["runtime"]["study"]
    context = study["context"]
    r530 = study["r530"]
    p3 = context.p3
    output = []
    for representative in runtime["representatives"]:
        family_id = representative["family_id"]
        candidate = runtime["candidates"][family_id].candidate
        joined, coverage = r530._evaluation_frame(
            candidate,
            candidate.parameter,
            context,
            study["membership"],
        )
        sign = -1 if candidate.family["direction"] == "low" else 1
        raw = p3.calculate_rank_ic(
            joined,
            "factor_value",
            "forward_return_5d",
            minimum=30,
        )
        neutral = p3._neutralized_ic(
            joined,
            ["industry", "size"],
            minimum_cross_section=30,
        )
        raw_blocks = p3._ic_block(raw, TRAIN_START, END, sign)
        neutral_blocks = p3._ic_block(neutral, TRAIN_START, END, sign)
        spreads = {
            "train": quintile_spread(
                joined,
                "factor_value",
                "forward_return_5d",
                direction=candidate.family["direction"],
                start=str(TRAIN_START),
                end=str(TRAIN_END),
            ),
            "validation": quintile_spread(
                joined,
                "factor_value",
                "forward_return_5d",
                direction=candidate.family["direction"],
                start=str(VALIDATION_START),
                end=str(VALIDATION_END),
            ),
            "recent": quintile_spread(
                joined,
                "factor_value",
                "forward_return_5d",
                direction=candidate.family["direction"],
                start=str(RECENT_START),
                end=str(END),
            ),
            "full": quintile_spread(
                joined,
                "factor_value",
                "forward_return_5d",
                direction=candidate.family["direction"],
                start=str(TRAIN_START),
                end=str(END),
            ),
        }
        train_ic = raw_blocks["train"]["directional_ic"]
        recent_ic = raw_blocks["recent"]["directional_ic"]
        output.append({
            "family_id": family_id,
            "parameter": candidate.parameter,
            "direction": candidate.family["direction"],
            "coverage": coverage,
            "train_ic": train_ic,
            "validation_ic": raw_blocks["test"]["directional_ic"],
            "recent_ic": recent_ic,
            "full_ic": raw_blocks["full"]["directional_ic"],
            "train_neutral_ic": neutral_blocks["train"]["directional_ic"],
            "validation_neutral_ic": neutral_blocks["test"]["directional_ic"],
            "recent_neutral_ic": neutral_blocks["recent"]["directional_ic"],
            "recent_minus_train_ic": (
                round(float(recent_ic) - float(train_ic), 8)
                if recent_ic is not None and train_ic is not None
                else None
            ),
            "recent_ic_positive": bool(recent_ic is not None and recent_ic > 0),
            "recent_direction_changed": bool(
                recent_ic is not None
                and train_ic is not None
                and np.sign(float(recent_ic)) != np.sign(float(train_ic))
            ),
            "quintile_spread": spreads,
        })
    return output


def baseline_trade_diagnostics(
    runtime: dict[str, Any],
    recent_run: dict[str, Any],
    hs300: dict[str, float],
    equal_weight: dict[str, float],
) -> dict[str, Any]:
    study = runtime["runtime"]["study"]
    context = study["context"]
    trades = recent_run["trades"]
    if not trades:
        raise RuntimeError("recent baseline produced no trades")
    trade_frame = pl.DataFrame({
        "date": [date.fromisoformat(str(trade["entry_date"])[:10]) for trade in trades],
        "symbol": [str(trade["symbol"]) for trade in trades],
    }).with_columns(pl.col("date").alias("entry_date"))
    recent_prices = context.prices.filter(
        (pl.col("date") >= RECENT_START) & (pl.col("date") <= END)
    ).join(study["membership"], on=["symbol", "date"], how="inner")
    size_panel = recent_prices.select(["date", "symbol", "log_float_mv"]).with_columns(
        (
            (pl.col("log_float_mv").rank("average").over("date") - 1.0)
            / pl.len().over("date")
        ).alias("size_percentile")
    )
    size_values = {
        (str(row["date"])[:10], str(row["symbol"])): float(row["size_percentile"])
        for row in size_panel.iter_rows(named=True)
        if row["size_percentile"] is not None
    }
    factor_exposure = {}
    for representative in runtime["representatives"]:
        family_id = representative["family_id"]
        candidate = runtime["candidates"][family_id].candidate
        frame = runtime["frames"][family_id].filter(
            (pl.col("date") >= RECENT_START) & (pl.col("date") <= END)
        )
        rank_column = (
            (pl.col("factor_value").rank("average").over("date") - 1.0)
            / pl.len().over("date")
        )
        if candidate.family["direction"] == "low":
            rank_column = 1.0 - rank_column
        ranked = frame.with_columns(rank_column.alias("factor_percentile")).join(
            trade_frame,
            left_on=["symbol", "date"],
            right_on=["symbol", "entry_date"],
            how="inner",
        )
        factor_exposure[family_id] = (
            round(float(ranked["factor_percentile"].mean()), 6)
            if ranked.height
            else None
        )
    enriched = []
    for trade in trades:
        key = (str(trade["entry_date"])[:10], str(trade["symbol"]))
        row = dict(trade)
        row["industry"] = runtime["industries"].get(str(trade["symbol"]), "unclassified")
        row["size_percentile"] = size_values.get(key)
        enriched.append(row)
    industry_pnl: dict[str, float] = defaultdict(float)
    for trade in enriched:
        industry_pnl[str(trade["industry"])] += float(trade.get("pnl_amount") or 0.0)
    pnl = sorted((float(trade.get("pnl_amount") or 0.0) for trade in enriched), reverse=True)
    total_pnl = sum(pnl)
    exit_reasons = Counter(str(trade.get("exit_reason") or "unknown") for trade in enriched)
    durations = [float(trade.get("duration") or 0.0) for trade in enriched]
    return {
        "trade_count": len(enriched),
        "positive_trades": sum(value > 0 for value in pnl),
        "negative_trades": sum(value < 0 for value in pnl),
        "total_pnl": round(total_pnl, 6),
        "top1_pnl": round(sum(pnl[:1]), 6),
        "top5_pnl": round(sum(pnl[:5]), 6),
        "top10_pnl": round(sum(pnl[:10]), 6),
        "worst_trade": round(min(pnl), 6),
        "exit_reasons": dict(sorted(exit_reasons.items())),
        "holding_days_mean": round(float(st.mean(durations)), 6),
        "holding_days_median": round(float(st.median(durations)), 6),
        "industry_pnl": sorted(
            (
                {"industry": industry, "pnl": round(value, 6)}
                for industry, value in industry_pnl.items()
            ),
            key=lambda row: row["pnl"],
            reverse=True,
        ),
        "mean_size_percentile": (
            round(float(np.mean([row["size_percentile"] for row in enriched if row["size_percentile"] is not None])), 6)
            if any(row["size_percentile"] is not None for row in enriched)
            else None
        ),
        "factor_exposure_percentile": factor_exposure,
        "beta_hs300": portfolio_beta(recent_run["curve"], hs300),
        "beta_equal_weight": portfolio_beta(recent_run["curve"], equal_weight),
        "drawdown_episode": drawdown_episode(recent_run["curve"]),
    }


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def run_study(root: Path) -> dict[str, Any]:
    _verify_runner_binding(root)
    runtime = load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    stop_variant = _variant(runtime, "stop_12")
    no_stop_variant = _variant(runtime, "no_stop")

    baseline = run_panel(
        p5,
        service,
        runtime,
        runtime["panel"],
        "baseline",
        variant=stop_variant,
    )
    no_stop = run_panel(
        p5,
        service,
        runtime,
        runtime["panel"],
        "no_stop",
        variant=no_stop_variant,
    )
    single = {}
    leave_one_out = {}
    for representative in runtime["representatives"]:
        family_id = representative["family_id"]
        single[family_id] = run_panel(
            p5,
            service,
            runtime,
            build_member_panel(runtime, [representative]),
            f"single_{family_id}",
            variant=stop_variant,
        )
        remaining = [
            row for row in runtime["representatives"]
            if row["family_id"] != family_id
        ]
        leave_one_out[family_id] = run_panel(
            p5,
            service,
            runtime,
            build_member_panel(runtime, remaining),
            f"loo_{family_id}",
            variant=stop_variant,
        )

    source = json.loads((root / R539_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    source_full = source["metrics"]["confirmation"]["full"]["2x"]
    source_recent = source["metrics"]["confirmation"]["recent"]["2x"]
    baseline_full = baseline["full"]["stats"]
    baseline_recent = baseline["recent"]["stats"]
    reproduced_full = (
        abs(float(baseline_full["total_return"]) - float(source_full["stats"]["total_return"])) <= 1e-6
        and abs(float(baseline_full["max_drawdown"]) - float(source_full["stats"]["max_drawdown"])) <= 1e-6
    )
    reproduced_recent = (
        abs(float(baseline_recent["total_return"]) - float(source_recent["stats"]["total_return"])) <= 1e-6
        and abs(float(baseline_recent["max_drawdown"]) - float(source_recent["stats"]["max_drawdown"])) <= 1e-6
    )
    formal_runs = [
        *baseline.values(),
        *no_stop.values(),
        *(run for rows in single.values() for run in rows.values()),
        *(run for rows in leave_one_out.values() for run in rows.values()),
    ]
    if len(formal_runs) != 56:
        raise RuntimeError(f"R542 expected 56 formal runs, got {len(formal_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in formal_runs
    )

    hs300 = hs300_returns()
    equal_weight = equal_weight_returns(runtime["runtime"]["study"])
    factor_ic = factor_ic_diagnostics(runtime)
    contributions = []
    for family_id in EXPECTED_FACTORS:
        contributions.append({
            "family_id": family_id,
            "single": {
                "full": _public_run(single[family_id]["full"]),
                "recent": _public_run(single[family_id]["recent"]),
            },
            "leave_one_out": {
                "full": _public_run(leave_one_out[family_id]["full"]),
                "recent": _public_run(leave_one_out[family_id]["recent"]),
            },
            "loo_contribution_full": round(
                loo_contribution(
                    float(baseline_full["total_return"]),
                    float(leave_one_out[family_id]["full"]["stats"]["total_return"]),
                ),
                8,
            ),
            "loo_contribution_recent": round(
                loo_contribution(
                    float(baseline_recent["total_return"]),
                    float(leave_one_out[family_id]["recent"]["stats"]["total_return"]),
                ),
                8,
            ),
        })
    contributions.sort(key=lambda row: row["loo_contribution_recent"], reverse=True)
    recent_monthly = monthly_attribution(
        baseline["recent"]["curve"],
        hs300,
        equal_weight,
    )
    full_monthly = monthly_attribution(
        baseline["full"]["curve"],
        hs300,
        equal_weight,
    )
    diagnostics = baseline_trade_diagnostics(
        runtime,
        baseline["recent"],
        hs300,
        equal_weight,
    )
    regime = pl.read_parquet(REGIME)
    regime_result = regime_attribution(
        baseline["recent"]["curve"],
        hs300,
        regime,
    )
    diagnostics["regime"] = regime_result
    benchmarks = {
        "hs300": {
            "full_total_return": _period_return(hs300, TRAIN_START, END),
            "recent_total_return": _period_return(hs300, RECENT_START, END),
        },
        "equal_weight_pit_members": {
            "full_total_return": _period_return(equal_weight, TRAIN_START, END),
            "recent_total_return": _period_return(equal_weight, RECENT_START, END),
        },
    }
    baseline_payload = {
        "full": _public_run(baseline["full"]),
        "recent": _public_run(baseline["recent"]),
        "no_stop_full": _public_run(no_stop["full"]),
        "no_stop_recent": _public_run(no_stop["recent"]),
        "stop_effect_full": round(
            float(baseline_full["total_return"])
            - float(no_stop["full"]["stats"]["total_return"]),
            8,
        ),
        "stop_effect_recent": round(
            float(baseline_recent["total_return"])
            - float(no_stop["recent"]["stats"]["total_return"]),
            8,
        ),
    }
    gate_results = {
        "baseline_full_reproduced": reproduced_full,
        "baseline_recent_reproduced": reproduced_recent,
        "formal_run_count": len(formal_runs) == 56,
        "formal_evidence_saved_lookahead_t1": evidence_ok,
        "factor_ic_diagnostics_complete": len(factor_ic) == 13,
        "single_leg_runs_complete": len(single) == 13,
        "leave_one_out_runs_complete": len(leave_one_out) == 13,
        "selection_performed": False,
        "recent_absolute_return_positive": float(baseline_recent["total_return"]) > 0,
    }
    gate_results["diagnostic_complete"] = all(
        gate_results[key]
        for key in (
            "baseline_full_reproduced",
            "baseline_recent_reproduced",
            "formal_run_count",
            "formal_evidence_saved_lookahead_t1",
            "factor_ic_diagnostics_complete",
            "single_leg_runs_complete",
            "leave_one_out_runs_complete",
        )
    )
    if not gate_results["diagnostic_complete"]:
        raise RuntimeError(f"R542 diagnostics incomplete: {gate_results}")
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "source_r539": {
            "full_2x": source_full["stats"],
            "recent_2x": source_recent["stats"],
        },
        "baseline": baseline_payload,
        "benchmarks": benchmarks,
        "factor_ic": factor_ic,
        "factor_contributions": contributions,
        "risk_and_trades": diagnostics,
        "monthly": {
            "recent": recent_monthly,
            "full": full_monthly,
        },
        "formal_runs": [_public_run(run) for run in formal_runs],
        "gate_results": gate_results,
        "conclusion": "rejected",
        "known_limits": [
            "This is a diagnostic attribution of the frozen R539C candidate, not a new selection round.",
            "Leave-one-out contribution is a portfolio marginal effect, not causal attribution.",
            "The equal-weight PIT benchmark is a costless diagnostic approximation.",
            "The local regime history ends before the frozen end date; unmatched days are reported rather than extrapolated.",
            "R539C recent-year results were observed repeatedly and are not an independent discovery sample.",
            "No leverage, orders, paper targets, production enablement or active version are produced.",
        ],
        "negative_knowledge": [
            "R539C remains rejected under the existing absolute recent-return gate.",
            "A positive relative excess does not automatically satisfy the absolute recent-return requirement.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _fmt(value: Any, pattern: str = ".2%") -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return format(float(value), pattern)
    return str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    baseline = payload["baseline"]
    benchmarks = payload["benchmarks"]
    full = baseline["full"]["stats"]
    recent = baseline["recent"]["stats"]
    hs300 = benchmarks["hs300"]
    equal = benchmarks["equal_weight_pit_members"]
    lines = [
        "# R542 R539C 最近一年失败归因",
        "",
        "> 本轮只诊断冻结候选，不选新因子、不调权重、不产生 active version。",
        "",
        "## 摘要",
        "",
        f"- Full 2x：总收益 {_fmt(full['total_return'])}，Sharpe {full['sharpe']}，MDD {_fmt(full['max_drawdown'])}。",
        f"- Recent 2x：总收益 {_fmt(recent['total_return'])}，Sharpe {recent['sharpe']}，MDD {_fmt(recent['max_drawdown'])}。",
        f"- 同期沪深300：full {_fmt(hs300['full_total_return'])}，recent {_fmt(hs300['recent_total_return'])}。",
        f"- 同期 PIT 成分等权：full {_fmt(equal['full_total_return'])}，recent {_fmt(equal['recent_total_return'])}。",
        f"- Recent 对沪深300超额：{_fmt(float(recent['total_return']) - float(hs300['recent_total_return']))}。",
        f"- Recent 对 PIT 等权超额：{_fmt(float(recent['total_return']) - float(equal['recent_total_return']))}。",
        f"- Recent 组合 beta（沪深300）：{payload['risk_and_trades']['beta_hs300']['beta']:.3f}。",
        f"- 止损对 recent 的收益差：{_fmt(baseline['stop_effect_recent'])}；对 full：{_fmt(baseline['stop_effect_full'])}。",
        "",
        "## 因子 IC",
        "",
        "| 因子 | 方向 | train IC | validation IC | recent IC | recent 中性 IC | recent-train | recent 五分位 spread |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["factor_ic"]:
        lines.append(
            f"| `{row['family_id']}` | {row['direction']} | {_fmt(row['train_ic'], '.4f')} | "
            f"{_fmt(row['validation_ic'], '.4f')} | {_fmt(row['recent_ic'], '.4f')} | "
            f"{_fmt(row['recent_neutral_ic'], '.4f')} | {_fmt(row['recent_minus_train_ic'], '+.4f')} | "
            f"{_fmt(row['quintile_spread']['recent']['spread'], '.2%')} |"
        )
    lines += [
        "",
        "## 留一法边际贡献",
        "",
        "`贡献 = baseline 总收益 - 去掉该因子后的总收益`，正数表示该因子有正贡献。",
        "",
        "| 因子 | recent 贡献 | full 贡献 | single recent | LOO recent |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["factor_contributions"]:
        lines.append(
            f"| `{row['family_id']}` | {_fmt(row['loo_contribution_recent'], '+.2%')} | "
            f"{_fmt(row['loo_contribution_full'], '+.2%')} | "
            f"{_fmt(row['single']['recent']['stats']['total_return'])} | "
            f"{_fmt(row['leave_one_out']['recent']['stats']['total_return'])} |"
        )
    risk = payload["risk_and_trades"]
    drawdown = risk["drawdown_episode"]
    lines += [
        "",
        "## 风险、交易和回撤",
        "",
        f"- 最近一年交易：{risk['trade_count']}，正/负：{risk['positive_trades']}/{risk['negative_trades']}。",
        f"- 总 PnL：{risk['total_pnl']:.2f}；最差单笔：{risk['worst_trade']:.2f}。",
        f"- 平均/中位持有天数：{risk['holding_days_mean']:.2f}/{risk['holding_days_median']:.2f}。",
        f"- 平均规模分位：{_fmt(risk['mean_size_percentile'])}。",
        f"- 最大回撤区间：{drawdown['peak_date']} → {drawdown['trough_date']}，"
        f"{_fmt(drawdown['max_drawdown'])}，恢复日 {drawdown['recovery_date'] or '未恢复'}。",
        f"- 出场原因：{json.dumps(risk['exit_reasons'], ensure_ascii=False, sort_keys=True)}。",
        "",
        "### 行业 PnL",
        "",
        "| 行业 | PnL |",
        "| --- | ---: |",
    ]
    for row in risk["industry_pnl"]:
        lines.append(f"| {row['industry']} | {row['pnl']:+.2f} |")
    lines += [
        "",
        "### 因子暴露（持仓方向分位均值，50%=中性）",
        "",
        "| 因子 | 持仓分位 |",
        "| --- | ---: |",
    ]
    for factor, value in sorted(risk["factor_exposure_percentile"].items()):
        lines.append(f"| `{factor}` | {_fmt(value)} |")
    lines += [
        "",
        "## 市场 regime",
        "",
        f"- 覆盖：{risk['regime']['coverage']}。",
        "",
        "| State | 天数 | 策略日均 | 基准日均 | 日均超额 | 策略累计 | 基准累计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in risk["regime"]["states"]:
        lines.append(
            f"| {row['state']} | {row['days']} | {_fmt(row['mean_strategy_daily'], '.4%')} | "
            f"{_fmt(row['mean_benchmark_daily'], '.4%')} | {_fmt(row['mean_excess_daily'], '+.4%')} | "
            f"{_fmt(row['strategy_compounded'])} | {_fmt(row['benchmark_compounded'])} |"
        )
    lines += [
        "",
        "## 月度收益（最近一年）",
        "",
        "| 月份 | 策略 | 沪深300 | PIT 等权 | 对沪深300 | 对等权 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["monthly"]["recent"]:
        lines.append(
            f"| {row['month']} | {_fmt(row['strategy'])} | {_fmt(row['hs300'])} | "
            f"{_fmt(row['equal_weight'])} | {_fmt(row['excess_hs300'], '+.2%')} | "
            f"{_fmt(row['excess_equal_weight'], '+.2%')} |"
        )
    lines += [
        "",
        "## 结论边界",
        "",
        f"- 诊断完成：{payload['gate_results']['diagnostic_complete']}。",
        f"- 结论：`{payload['conclusion']}`。",
        "- 最近一年绝对收益为负，但同期沪深300和 PIT 等权也不同程度下跌；不能把相对抗跌误写成绝对盈利。",
        "- 留一法只代表组合边际贡献，不能单独证明某个因子的因果 alpha。",
        "",
        "## 已知边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def publish_result(payload: dict[str, Any]) -> dict[str, Any]:
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "runner": RUNNER,
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["gate_results"],
        "metrics": {
            "design": payload["design"],
            "source_r539": payload["source_r539"],
            "baseline": payload["baseline"],
            "benchmarks": payload["benchmarks"],
            "factor_ic": payload["factor_ic"],
            "factor_contributions": payload["factor_contributions"],
            "risk_and_trades": payload["risk_and_trades"],
            "monthly": payload["monthly"],
            "formal_runs": payload["formal_runs"],
        },
        "source_result_hashes": [
            sha256(R539_RESULT),
            sha256(R536_RESULT),
        ],
        "formal_evidence": payload["baseline"]["recent"]["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r542_recent_failure_attribution_published",
    )
    return published.__dict__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--freeze-preregistration", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R542 results already exist; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "diagnostic_complete": payload["gate_results"]["diagnostic_complete"],
        "baseline_recent": payload["baseline"]["recent"]["stats"]["total_return"],
        "hs300_recent": payload["benchmarks"]["hs300"]["recent_total_return"],
        "equal_weight_recent": payload["benchmarks"]["equal_weight_pit_members"]["recent_total_return"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
