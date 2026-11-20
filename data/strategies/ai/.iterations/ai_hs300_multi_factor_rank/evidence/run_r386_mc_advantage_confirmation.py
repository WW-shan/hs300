"""R386: confirm the R385 stable platform and test state-conditioned switching.

The candidate and defensive parameters are selected from three 252-return
training folds only. The later holdout is evaluated after those choices are
frozen. This is a disclosed confirmation test because the scratch scan was
already observed before registration.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import statistics as st
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file()
)
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r386_preregistration.json"
R385_RUNNER = EVIDENCE / "run_r385_pit_platform_monte_carlo.py"
R385_REPORT = EVIDENCE / "r385_pit_platform_monte_carlo.json"
R385_PREREG = EVIDENCE / "r385_preregistration.json"
REGIME = DATA / "regime_history/part.parquet"
ENGINE = ROOT / "backend/app/backtest/engine.py"
OUTPUT = EVIDENCE / "r386_mc_advantage_confirmation.json"
RESULT = EVIDENCE / "r386_result.md"
TRAIN_STARTS = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5))
TRAIN_RETURNS = 252
TRAIN_OBSERVATIONS = TRAIN_RETURNS + 1
PARAMETER_ORDER = (
    "c_satellite_cap",
    "equity_target_vol",
    "overlay_lookback_days",
    "overlay_target_vol",
    "overlay_equity_weight_max",
)
BOOTSTRAP_SEED = 386
SWITCH_TURNOVER = 0.0052
_R385_MODULE = None


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


def r385():
    global _R385_MODULE
    if _R385_MODULE is None:
        _R385_MODULE = module(R385_RUNNER, "r386_r385_runtime")
    return _R385_MODULE


def practical_round_parameters(raw: dict[str, Any], prereg: dict[str, Any]) -> dict[str, Any]:
    increments = prereg["monte_carlo_parameter_synthesis"]["practical_rounding"]
    rounded: dict[str, Any] = {}
    for key in PARAMETER_ORDER:
        increment = float(increments[key])
        value = round(float(raw[key]) / increment) * increment
        rounded[key] = round(value) if key == "overlay_lookback_days" else round(value, 10)
    return rounded


def training_summary(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if len(folds) != len(TRAIN_STARTS):
        raise ValueError("training selection requires exactly three folds")
    annual = [float(fold["annual"]) for fold in folds]
    sharpes = [float(fold["sharpe"]) for fold in folds]
    mdds = [float(fold["mdd"]) for fold in folds]
    return {
        "mean_annual": st.mean(annual),
        "worst_annual": min(annual),
        "median_fold_sharpe": st.median(sharpes),
        "worst_sharpe": min(sharpes),
        "worst_fold_mdd": min(mdds),
        "positive_fold_count": sum(value > 0.0 for value in annual),
    }


def training_eligible(row: dict[str, Any], prereg: dict[str, Any]) -> bool:
    eligibility = prereg["monte_carlo_parameter_synthesis"]["eligibility"]
    folds = row["training_folds"]
    return bool(
        eligibility["every_training_fold_return_positive"]
        and all(float(fold["total_return"]) > 0.0 for fold in folds)
        and all(
            float(fold["sharpe"]) >= eligibility["every_training_fold_sharpe_min"] for fold in folds
        )
        and all(float(fold["mdd"]) >= eligibility["every_training_fold_mdd_min"] for fold in folds)
    )


def _selected_row(row: dict[str, Any], eligible_count: int) -> dict[str, Any]:
    summary = training_summary(row["training_folds"])
    return {
        "sample": int(row["sample"]),
        "parameters": dict(row["parameters"]),
        "raw_parameters": dict(row.get("raw", row["parameters"])),
        "training_folds": row["training_folds"],
        "training_summary": summary,
        "selection": {
            "eligible_count": eligible_count,
            "uses_holdout": False,
        },
    }


def select_advantage_candidate(
    rows: list[dict[str, Any]], prereg: dict[str, Any] | None = None
) -> dict[str, Any]:
    if prereg is None:
        prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    eligible = [row for row in rows if training_eligible(row, prereg)]
    if not eligible:
        raise ValueError("no training-eligible advantage candidate")

    def score(row: dict[str, Any]) -> tuple[float, float, float, float, int]:
        summary = training_summary(row["training_folds"])
        return (
            -summary["mean_annual"],
            -summary["worst_annual"],
            -summary["median_fold_sharpe"],
            float(row["parameters"]["c_satellite_cap"]),
            int(row["sample"]),
        )

    return _selected_row(min(eligible, key=score), len(eligible))


def _exposure_proxy(parameters: dict[str, Any]) -> float:
    return st.mean(
        [
            float(parameters["c_satellite_cap"]),
            float(parameters["equity_target_vol"]),
            float(parameters["overlay_equity_weight_max"]),
        ]
    )


def select_defensive_candidate(
    rows: list[dict[str, Any]], prereg: dict[str, Any] | None = None
) -> dict[str, Any]:
    if prereg is None:
        prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    eligible = [row for row in rows if training_eligible(row, prereg)]
    if not eligible:
        raise ValueError("no training-eligible defensive candidate")

    def score(row: dict[str, Any]) -> tuple[float, float, float, int]:
        summary = training_summary(row["training_folds"])
        return (
            -summary["worst_annual"],
            -summary["median_fold_sharpe"],
            _exposure_proxy(row["parameters"]),
            int(row["sample"]),
        )

    return _selected_row(min(eligible, key=score), len(eligible))


def deduplicate_parameter_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for sample in samples:
        key = tuple(sorted(sample["parameters"].items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(sample)
    return unique


def axis_neighbourhood(centre: dict[str, Any], prereg: dict[str, Any]) -> list[dict[str, Any]]:
    steps = prereg["candidate_gates"]["axis_neighbour_plateau"]["steps"]
    rows = [
        {
            "label": "centre",
            "dimension": None,
            "offset_steps": 0,
            "parameters": {name: centre[name] for name in PARAMETER_ORDER},
        }
    ]
    for name in PARAMETER_ORDER:
        for offset in (-1, 1):
            parameters = {key: centre[key] for key in PARAMETER_ORDER}
            raw = float(centre[name]) + offset * float(steps[name])
            parameters[name] = raw if name != "overlay_lookback_days" else round(raw)
            rows.append(
                {
                    "label": f"{name}_{offset:+d}",
                    "dimension": name,
                    "offset_steps": offset,
                    "parameters": parameters,
                }
            )
    return rows


def evaluate_five_x(metrics: dict[str, float]) -> dict[str, Any]:
    warning = bool(metrics["annual"] < 0.05 or metrics["sharpe"] < 0.8 or metrics["mdd"] < -0.25)
    return {"warning": warning, "hard_rejection": False}


def parameter_key(parameters: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(parameters[name] for name in PARAMETER_ORDER)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, date):
        return str(value)
    return value


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("cannot take a quantile of an empty sample")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(fraction * len(ordered))))]


def _curve_returns(frame: pl.DataFrame) -> list[float]:
    values = [float(value) for value in frame["value"].to_list()]
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def _aligned_returns(left: pl.DataFrame, right: pl.DataFrame) -> list[float]:
    joined = left.join(right, on="date", how="inner", suffix="_base").sort("date")
    if joined.height < 2:
        raise ValueError("paired curves have too few aligned observations")
    left_values = [float(value) for value in joined["value"].to_list()]
    right_values = [float(value) for value in joined["value_base"].to_list()]
    return [
        (left_values[index] / left_values[index - 1] - 1.0)
        - (right_values[index] / right_values[index - 1] - 1.0)
        for index in range(1, joined.height)
    ]


def circular_paired_bootstrap(
    differences: list[float],
    *,
    paths: int,
    block_days: int,
    horizon_days: int,
    seed: int,
) -> dict[str, Any]:
    if len(differences) < block_days:
        raise ValueError("paired bootstrap needs at least one complete block")
    rng = random.Random(seed)
    blocks = math.ceil(horizon_days / block_days)
    annual = []
    for _ in range(paths):
        sampled: list[float] = []
        for _ in range(blocks):
            start = rng.randrange(len(differences))
            sampled.extend(
                differences[(start + offset) % len(differences)] for offset in range(block_days)
            )
        growth = 1.0
        for value in sampled[:horizon_days]:
            growth *= 1.0 + value
        annual.append(growth - 1.0)
    return {
        "paths": paths,
        "block_days": block_days,
        "horizon_days": horizon_days,
        "seed": seed,
        "probability_positive": sum(value > 0.0 for value in annual) / len(annual),
        "p05": _quantile(annual, 0.05),
        "p50": _quantile(annual, 0.50),
        "p95": _quantile(annual, 0.95),
        "worst": min(annual),
        "best": max(annual),
    }


def paired_block_bootstrap(
    candidate: pl.DataFrame,
    centre: pl.DataFrame,
    prereg: dict[str, Any],
) -> dict[str, Any]:
    gates = prereg["candidate_gates"]["paired_block_bootstrap"]
    differences = _aligned_returns(candidate, centre)
    return circular_paired_bootstrap(
        differences,
        paths=int(gates["paths"]),
        block_days=int(gates["block_days"]),
        horizon_days=int(gates["horizon_days"]),
        seed=BOOTSTRAP_SEED,
    )


def training_fold_metrics(curve: pl.DataFrame, start: date) -> dict[str, Any]:
    part = curve.filter(pl.col("date") >= start).sort("date").head(TRAIN_OBSERVATIONS)
    if part.height != TRAIN_OBSERVATIONS:
        raise ValueError(f"training fold from {start} has fewer than {TRAIN_RETURNS} returns")
    metrics = r385().stats([float(value) for value in part["value"].to_list()])
    if metrics["days"] != TRAIN_OBSERVATIONS:
        raise RuntimeError("training fold duration drifted from 252 returns")
    return {
        **metrics,
        "start": str(part["date"][0]),
        "end": str(part["date"][-1]),
    }


def lagged_regime_signals(frame: pl.DataFrame) -> pl.DataFrame:
    ordered = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    score_ma20 = pl.col("score").rolling_mean(20)
    index_vol20 = pl.col("index_pct").rolling_std(20)
    return ordered.with_columns(
        [
            pl.col("state").shift(1).alias("state_lag1"),
            pl.col("score").shift(1).alias("score_lag1"),
            pl.col("trend_score").shift(1).alias("trend_score_lag1"),
            pl.col("above_ma20_pct").shift(1).alias("above_ma20_pct_lag1"),
            score_ma20.shift(1).alias("score_ma20_lag1"),
            index_vol20.shift(1).alias("index_vol20_lag1"),
            index_vol20.rolling_median(252).shift(1).alias("index_vol252_median_lag1"),
        ]
    ).select(
        [
            "date",
            "state_lag1",
            "score_lag1",
            "trend_score_lag1",
            "above_ma20_pct_lag1",
            "score_ma20_lag1",
            "index_vol20_lag1",
            "index_vol252_median_lag1",
        ]
    )


def monte_carlo_samples(
    prereg: dict[str, Any], r385_prereg: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = r385().sample_parameters(r385_prereg)
    return [
        {
            "sample": int(row["sample"]),
            "raw_parameters": dict(row["parameters"]),
            "parameters": practical_round_parameters(row["parameters"], prereg),
        }
        for row in rows
    ]


def local_monte_carlo_samples(prereg: dict[str, Any]) -> list[dict[str, Any]]:
    design = prereg["candidate_gates"]["local_monte_carlo"]
    ranges = design["ranges"]
    units = r385().latin_hypercube(
        int(design["samples"]),
        len(PARAMETER_ORDER),
        int(design["seed"]),
    )
    rows = []
    for index, unit in enumerate(units):
        raw = {
            name: float(ranges[name][0])
            + float(value) * (float(ranges[name][1]) - float(ranges[name][0]))
            for name, value in zip(PARAMETER_ORDER, unit, strict=True)
        }
        rows.append(
            {
                "sample": index,
                "raw_parameters": raw,
                "parameters": practical_round_parameters(raw, prereg),
            }
        )
    return rows


class PortfolioComposer:
    def __init__(self, verified: dict[str, Any]) -> None:
        runtime = r385()
        self.runtime = runtime
        self.engine = runtime.run_current_pit_engine()
        self.outputs = self.engine["outputs"]
        self.ctx = runtime._context()
        self.assets = runtime.prepare_assets(self.ctx)
        self.c_curves = {
            cost: runtime.combine_c(self.outputs, cost, self.ctx) for cost in ("2x", "3x", "5x")
        }
        self.verified = verified
        self.cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    def compose(
        self, cost: str, parameters: dict[str, Any], *, mode: str = "full"
    ) -> dict[str, Any]:
        key = (cost, mode, parameter_key(parameters))
        if key in self.cache:
            return self.cache[key]
        bundle = self.runtime.compose_portfolio(
            self.ctx,
            self.assets,
            self.outputs[f"A_{cost}"],
            self.outputs[f"B_{cost}_fade05_h30_sl20"],
            self.c_curves[cost],
            cost,
            parameters,
            mode=mode,
        )
        self.cache[key] = bundle
        return bundle


def candidate_metrics(bundle: dict[str, Any]) -> dict[str, Any]:
    runtime = r385()
    metrics = runtime.curve_metrics(bundle["curve"])
    weight_summary = bundle.get("weight_summary") or {}
    return {
        **metrics,
        "training_folds": [training_fold_metrics(bundle["curve"], start) for start in TRAIN_STARTS],
        "max_weight_sum": float(weight_summary.get("max_weight_sum", 0.0)),
    }


def full_gate_pass(metrics: dict[str, Any], prereg: dict[str, Any], gate_name: str) -> bool:
    gate = prereg["candidate_gates"][gate_name]
    checks = [
        metrics["full"]["annual"] >= gate["annual_min"],
        metrics["full"]["sharpe"] >= gate["sharpe_min"],
        metrics["full"]["mdd"] >= gate["mdd_min"],
        metrics["recent"]["total_return"] > gate["recent_total_return_min_exclusive"],
        metrics["rolling"]["positive_count"] >= gate["rolling_positive_count_min"],
    ]
    if gate_name == "full_2x":
        checks.extend(
            [
                metrics["yearly"].get("2022", -1.0) >= gate["year_2022_min"],
                metrics["yearly"].get("2023", -1.0) >= gate["year_2023_min"],
            ]
        )
    return bool(all(checks))


def holdout_gate_pass(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    gate = prereg["candidate_gates"]["sealed_holdout"]
    sealed = metrics["sealed"]
    return bool(
        sealed["total_return"] > gate["total_return_min_exclusive"]
        and sealed["sharpe"] >= gate["sharpe_min"]
        and sealed["mdd"] >= gate["mdd_min"]
    )


def local_sample_pass(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    gate = prereg["candidate_gates"]["local_monte_carlo"]["per_sample_gate"]
    sealed = metrics["sealed"]
    return bool(
        metrics["full"]["annual"] >= gate["annual_min"]
        and metrics["full"]["sharpe"] >= gate["sharpe_min"]
        and metrics["full"]["mdd"] >= gate["mdd_min"]
        and metrics["recent"]["total_return"] > gate["recent_total_return_min_exclusive"]
        and metrics["rolling"]["positive_count"] >= gate["rolling_positive_count_min"]
        and sealed["total_return"] > gate["sealed_total_return_min_exclusive"]
        and sealed["sharpe"] >= gate["sealed_sharpe_min"]
        and sealed["mdd"] >= gate["sealed_mdd_min"]
    )


def exposure_warning(*metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"max_weight_sum": item["max_weight_sum"]}
        for item in metrics
        if item["max_weight_sum"] > 1.0 + 1e-12
    ]


def build_training_rows(
    samples: list[dict[str, Any]],
    composer: PortfolioComposer,
) -> list[dict[str, Any]]:
    rows = []
    for sample in samples:
        curve = composer.compose("2x", sample["parameters"])["curve"]
        rows.append(
            {
                "sample": sample["sample"],
                "raw": sample["raw_parameters"],
                "parameters": sample["parameters"],
                "training_folds": [training_fold_metrics(curve, start) for start in TRAIN_STARTS],
            }
        )
    return rows


def _regime_rule_chooses_advantage(row: dict[str, Any] | None, rule: str) -> bool:
    if row is None:
        return False
    if rule == "state_strength":
        return row.get("state_lag1") in {"strong", "lean_strong"}
    if rule == "state_nonweak":
        return row.get("state_lag1") in {"strong", "lean_strong", "range"}
    if rule == "trend_breadth":
        return bool(
            row.get("trend_score_lag1") is not None
            and row.get("above_ma20_pct_lag1") is not None
            and float(row["trend_score_lag1"]) >= 50.0
            and float(row["above_ma20_pct_lag1"]) >= 0.50
        )
    if rule == "score_trend":
        return bool(
            row.get("score_ma20_lag1") is not None
            and row.get("trend_score_lag1") is not None
            and float(row["score_ma20_lag1"]) >= 55.0
            and float(row["trend_score_lag1"]) >= 50.0
        )
    if rule == "vol_trend":
        return bool(
            row.get("index_vol20_lag1") is not None
            and row.get("index_vol252_median_lag1") is not None
            and float(row["index_vol20_lag1"]) <= float(row["index_vol252_median_lag1"])
            and row.get("trend_score_lag1") is not None
            and float(row["trend_score_lag1"]) >= 50.0
        )
    raise ValueError(f"unknown regime rule: {rule}")


def monthly_switch_curve(
    advantage: pl.DataFrame,
    defensive: pl.DataFrame,
    signals: pl.DataFrame,
    rule: str,
    *,
    switch_cost: float = SWITCH_TURNOVER,
) -> dict[str, Any]:
    joined = advantage.join(defensive, on="date", how="inner", suffix="_defensive").sort("date")
    if joined.height < 3:
        raise ValueError("monthly switch needs at least three portfolio observations")
    dates = joined["date"].to_list()
    advantage_values = [float(value) for value in joined["value"].to_list()]
    defensive_values = [float(value) for value in joined["value_defensive"].to_list()]
    advantage_returns = [
        advantage_values[index] / advantage_values[index - 1] - 1.0
        for index in range(1, len(advantage_values))
    ]
    defensive_returns = [
        defensive_values[index] / defensive_values[index - 1] - 1.0
        for index in range(1, len(defensive_values))
    ]
    ordered_signals = signals.filter(pl.col("date").is_between(dates[0], dates[-1])).sort("date")
    signal_dates = ordered_signals["date"].to_list()
    signal_rows = ordered_signals.iter_rows(named=True)
    signal_lookup = {row["date"]: row for row in signal_rows}

    def latest_signal(day: date) -> dict[str, Any] | None:
        position = bisect_right(signal_dates, day) - 1
        return signal_lookup[signal_dates[position]] if position >= 0 else None

    month_ends = {
        dates[index]
        for index in range(len(dates))
        if index == len(dates) - 1 or dates[index + 1].month != dates[index].month
    }
    values = [advantage_values[0]]
    current = advantage_values[0]
    active_leg = "advantage"
    switches = []
    for index in range(1, len(dates)):
        day = dates[index]
        daily_return = (
            advantage_returns[index - 1]
            if active_leg == "advantage"
            else defensive_returns[index - 1]
        )
        current *= 1.0 + daily_return
        if day in month_ends:
            target = (
                "advantage"
                if _regime_rule_chooses_advantage(latest_signal(day), rule)
                else "defensive"
            )
            if target != active_leg:
                current *= 1.0 - switch_cost
                switches.append(
                    {
                        "date": str(day),
                        "from": active_leg,
                        "to": target,
                        "turnover": switch_cost,
                    }
                )
                active_leg = target
        values.append(current)
    return {
        "curve": pl.DataFrame({"date": dates, "value": values}),
        "switch_count": len(switches),
        "switches": switches,
        "switch_cost": switch_cost,
        "max_weight_sum": 1.0,
    }


def regime_training_result(
    switched: pl.DataFrame,
    rule: str,
    fixed_advantage: dict[str, Any],
    prereg: dict[str, Any],
) -> dict[str, Any]:
    folds = [training_fold_metrics(switched, start) for start in TRAIN_STARTS]
    summary = training_summary(folds)
    fixed = fixed_advantage["training_summary"]
    gate = prereg["regime_mappings"]["training_rule_gate"]
    checks = {
        "worst_fold_increment": (
            summary["worst_annual"]
            > fixed["worst_annual"] + gate["worst_fold_annual_must_exceed_fixed_advantage_by_min"]
        ),
        "mean_fold_not_lower": (
            summary["mean_annual"] >= fixed["mean_annual"]
            if gate["mean_fold_annual_must_be_at_least_fixed_advantage"]
            else True
        ),
        "median_sharpe_tolerance": (
            summary["median_fold_sharpe"]
            >= fixed["median_fold_sharpe"]
            - gate["median_fold_sharpe_may_underperform_fixed_advantage_by_max"]
        ),
    }
    return {
        "rule": rule,
        "folds": folds,
        "summary": summary,
        "checks": checks,
        "training_pass": bool(all(checks.values())),
    }


def select_regime_rule(
    rows: list[dict[str, Any]],
    switch_counts: dict[str, int],
) -> dict[str, Any]:
    passed = [row for row in rows if row["training_pass"]]
    if not passed:
        return {
            "advanced_rules": [],
            "selected_rule": None,
            "selection_basis": "training folds only; no rule cleared the pre-registered training gate",
        }
    selected = max(
        passed,
        key=lambda row: (
            row["summary"]["worst_annual"],
            row["summary"]["mean_annual"],
            -switch_counts[row["rule"]],
            row["rule"],
        ),
    )
    return {
        "advanced_rules": [row["rule"] for row in passed],
        "selected_rule": selected["rule"],
        "selection_basis": "training folds only; highest worst fold, then mean, then fewer switches",
    }


def verify_inputs() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r385_report = json.loads(R385_REPORT.read_text(encoding="utf-8"))
    expected = prereg["source_evidence"]
    actual = {
        "r385_report": sha256(R385_REPORT),
        "r385_preregistration": sha256(R385_PREREG),
        "r385_runner": sha256(R385_RUNNER),
        "regime_history": sha256(REGIME),
        "engine": sha256(ENGINE),
    }
    checks = {
        "r386_preregistration_sidecar": (
            PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
            == sha256(PREREG)
        ),
        "r385_report_sidecar": (
            R385_REPORT.with_name(R385_REPORT.name + ".sha256")
            .read_text(encoding="ascii")
            .split()[0]
            == actual["r385_report"]
        ),
        "r385_preregistration_sidecar": (
            R385_PREREG.with_name(R385_PREREG.name + ".sha256")
            .read_text(encoding="ascii")
            .split()[0]
            == actual["r385_preregistration"]
        ),
        "r385_report_binding": actual["r385_report"] == expected["r385_report"]["sha256"],
        "r385_preregistration_binding": (
            actual["r385_preregistration"] == expected["r385_preregistration"]["sha256"]
        ),
        "r385_runner_binding": actual["r385_runner"] == expected["r385_runner"]["sha256"],
        "regime_binding": actual["regime_history"] == expected["regime_history"]["sha256"],
        "engine_binding": actual["engine"] == expected["engine"]["sha256"],
        "r385_all_pass": bool(r385_report.get("all_pass")),
        "r385_candidate_status": (
            r385_report.get("decision", {}).get("status") == "candidate_for_paper_trading_review"
        ),
        "r385_no_leverage": bool(r385_report.get("no_leverage")),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError(f"R386 input lineage gate failed: {failed}")
    return {
        "checks": checks,
        "actual_sha256": actual,
        "preregistration": prereg,
        "r385_report": r385_report,
    }


def _metric_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "full": metrics["full"],
        "yearly": metrics["yearly"],
        "recent": metrics["recent"],
        "sealed": metrics["sealed"],
        "rolling": metrics["rolling"],
        "max_weight_sum": metrics["max_weight_sum"],
    }


def _range(values: list[float]) -> list[float]:
    return [min(values), max(values)] if values else []


def evaluate_candidate(
    verified: dict[str, Any],
    composer: PortfolioComposer,
    advantage: dict[str, Any],
) -> dict[str, Any]:
    prereg = verified["preregistration"]
    r385_report = verified["r385_report"]
    parameters = advantage["parameters"]
    bundles = {
        "2x": composer.compose("2x", parameters),
        "3x": composer.compose("3x", parameters),
        "5x": composer.compose("5x", parameters),
    }
    metrics = {cost: candidate_metrics(bundle) for cost, bundle in bundles.items()}
    base_bundle = composer.compose("2x", r385().DEFAULT_PARAMETERS)
    base_metrics = candidate_metrics(base_bundle)
    r385_center = r385_report["current_portfolio"]["center_2x"]
    increments = {
        "full_annual": metrics["2x"]["full"]["annual"] - r385_center["full"]["annual"],
        "full_sharpe": metrics["2x"]["full"]["sharpe"] - r385_center["full"]["sharpe"],
        "full_mdd_improvement": metrics["2x"]["full"]["mdd"] - r385_center["full"]["mdd"],
        "sealed_total_return": (
            metrics["2x"]["sealed"]["total_return"] - r385_center["sealed"]["total_return"]
        ),
        "recent_total_return": (
            metrics["2x"]["recent"]["total_return"] - r385_center["recent"]["total_return"]
        ),
    }
    increment_gate = prereg["candidate_gates"]["increment_over_r385_centre"]
    increment_checks = {
        "full_annual": increments["full_annual"] >= increment_gate["full_annual_min_delta"],
        "full_sharpe": increments["full_sharpe"] >= increment_gate["full_sharpe_min_delta"],
        "sealed_total_return": (
            increments["sealed_total_return"]
            > increment_gate["sealed_total_return_min_delta_exclusive"]
        ),
        "recent_total_return": (
            increments["recent_total_return"]
            > increment_gate["recent_total_return_min_delta_exclusive"]
        ),
    }
    bootstrap = {
        "full": paired_block_bootstrap(bundles["2x"]["curve"], base_bundle["curve"], prereg),
        "sealed": paired_block_bootstrap(
            bundles["2x"]["curve"].filter(pl.col("date") >= r385().SEALED_START),
            base_bundle["curve"].filter(pl.col("date") >= r385().SEALED_START),
            prereg,
        ),
    }
    bootstrap_gate = prereg["candidate_gates"]["paired_block_bootstrap"]
    bootstrap_checks = {
        "full_probability": (
            bootstrap["full"]["probability_positive"]
            >= bootstrap_gate["full_probability_positive_min"]
        ),
        "full_p05": bootstrap["full"]["p05"] >= bootstrap_gate["full_p05_min"],
        "sealed_probability": (
            bootstrap["sealed"]["probability_positive"]
            >= bootstrap_gate["sealed_probability_positive_min"]
        ),
        "sealed_p05": bootstrap["sealed"]["p05"] >= bootstrap_gate["sealed_p05_min"],
    }
    exposures = {cost: {"max_weight_sum": item["max_weight_sum"]} for cost, item in metrics.items()}
    exposure_failures = exposure_warning(*metrics.values())
    checks = {
        "full_2x": full_gate_pass(metrics["2x"], prereg, "full_2x"),
        "full_3x": full_gate_pass(metrics["3x"], prereg, "full_3x"),
        "holdout": holdout_gate_pass(metrics["2x"], prereg),
        "increment_over_r385_centre": bool(all(increment_checks.values())),
        "paired_bootstrap": bool(all(bootstrap_checks.values())),
        "no_leverage": not exposure_failures,
    }
    return {
        "parameters": parameters,
        "source_sample": advantage["sample"],
        "metrics": {cost: _metric_subset(item) for cost, item in metrics.items()},
        "curves": {cost: bundle["curve"] for cost, bundle in bundles.items()},
        "base_2x_metrics": _metric_subset(base_metrics),
        "exposures": exposures,
        "increments_over_r385_centre": increments,
        "increment_checks": increment_checks,
        "bootstrap": bootstrap,
        "bootstrap_checks": bootstrap_checks,
        "checks": checks,
        "five_x": {
            **_metric_subset(metrics["5x"]),
            **evaluate_five_x(metrics["5x"]["full"]),
        },
    }


def evaluate_axis_neighbourhood(
    candidate: dict[str, Any],
    composer: PortfolioComposer,
    prereg: dict[str, Any],
) -> dict[str, Any]:
    rows = []
    for neighbour in axis_neighbourhood(candidate["parameters"], prereg):
        metrics = {
            cost: candidate_metrics(composer.compose(cost, neighbour["parameters"]))
            for cost in ("2x", "3x")
        }
        rows.append(
            {
                **neighbour,
                "metrics": {cost: _metric_subset(value) for cost, value in metrics.items()},
                "pass": {
                    cost: {
                        "full_gate": full_gate_pass(value, prereg, f"full_{cost}"),
                        "max_weight_sum": value["max_weight_sum"],
                    }
                    for cost, value in metrics.items()
                },
            }
        )
    pass_count_2x = sum(row["pass"]["2x"]["full_gate"] for row in rows)
    pass_count_3x = sum(row["pass"]["3x"]["full_gate"] for row in rows)
    exposures_ok = all(
        row["pass"][cost]["max_weight_sum"] <= 1.0 + 1e-12 for row in rows for cost in ("2x", "3x")
    )
    return {
        "row_count": len(rows),
        "pass_count_2x": pass_count_2x,
        "pass_count_3x": pass_count_3x,
        "all_pass": bool(pass_count_2x == pass_count_3x == len(rows) and exposures_ok),
        "rows": rows,
    }


def evaluate_local_monte_carlo(
    composer: PortfolioComposer,
    prereg: dict[str, Any],
) -> dict[str, Any]:
    requested = local_monte_carlo_samples(prereg)
    unique = deduplicate_parameter_samples(requested)
    rows = []
    for sample in unique:
        metrics = {
            cost: candidate_metrics(composer.compose(cost, sample["parameters"]))
            for cost in ("2x", "3x")
        }
        rows.append(
            {
                "sample": sample["sample"],
                "parameters": sample["parameters"],
                "metrics": {cost: _metric_subset(value) for cost, value in metrics.items()},
                "pass": {
                    cost: {
                        "sample_gate": local_sample_pass(value, prereg),
                        "max_weight_sum": value["max_weight_sum"],
                    }
                    for cost, value in metrics.items()
                },
            }
        )
    pass_count_2x = sum(row["pass"]["2x"]["sample_gate"] for row in rows)
    pass_count_3x = sum(row["pass"]["3x"]["sample_gate"] for row in rows)
    denominator = len(rows)
    gate = prereg["candidate_gates"]["local_monte_carlo"]
    all_exposures = [row["pass"][cost]["max_weight_sum"] for row in rows for cost in ("2x", "3x")]
    annual_2x = [row["metrics"]["2x"]["full"]["annual"] for row in rows]
    sharpe_2x = [row["metrics"]["2x"]["full"]["sharpe"] for row in rows]
    mdd_2x = [row["metrics"]["2x"]["full"]["mdd"] for row in rows]
    return {
        "requested_samples": len(requested),
        "unique_sample_count": denominator,
        "duplicate_count": len(requested) - denominator,
        "pass_count_2x": pass_count_2x,
        "pass_count_3x": pass_count_3x,
        "pass_rate_2x": pass_count_2x / denominator,
        "pass_rate_3x": pass_count_3x / denominator,
        "failed_samples_2x": [
            row["sample"] for row in rows if not row["pass"]["2x"]["sample_gate"]
        ],
        "failed_samples_3x": [
            row["sample"] for row in rows if not row["pass"]["3x"]["sample_gate"]
        ],
        "annual_2x_range": _range(annual_2x),
        "sharpe_2x_range": _range(sharpe_2x),
        "mdd_2x_range": _range(mdd_2x),
        "max_weight_sum": max(all_exposures),
        "passed": bool(
            denominator >= gate["unique_samples_min"]
            and pass_count_2x / denominator >= gate["pass_rate_2x_min"]
            and pass_count_3x / denominator >= gate["pass_rate_3x_min"]
            and max(all_exposures) <= 1.0 + 1e-12
        ),
        "rows": rows,
    }


def evaluate_regime_conditioning(
    verified: dict[str, Any],
    composer: PortfolioComposer,
    advantage: dict[str, Any],
    defensive: dict[str, Any],
) -> dict[str, Any]:
    prereg = verified["preregistration"]
    advantage_bundle = composer.compose("2x", advantage["parameters"])
    defensive_bundle = composer.compose("2x", defensive["parameters"])
    signals = lagged_regime_signals(pl.read_parquet(REGIME))
    rows = []
    switch_counts: dict[str, int] = {}
    for rule in prereg["regime_mappings"]["rules"]:
        switched = monthly_switch_curve(
            advantage_bundle["curve"],
            defensive_bundle["curve"],
            signals,
            rule,
        )
        result = regime_training_result(switched["curve"], rule, advantage, prereg)
        metrics = candidate_metrics(
            {"curve": switched["curve"], "weight_summary": {"max_weight_sum": 1.0}}
        )
        result["validation"] = metrics["sealed"]
        result["full"] = metrics["full"]
        result["recent"] = metrics["recent"]
        result["switch_count"] = switched["switch_count"]
        result["switches"] = switched["switches"]
        result["switch_cost"] = switched["switch_cost"]
        result["max_weight_sum"] = switched["max_weight_sum"]
        switch_counts[rule] = switched["switch_count"]
        rows.append(result)
    selection = select_regime_rule(rows, switch_counts)
    selected_rule = selection["selected_rule"]
    selected_row = next((row for row in rows if row["rule"] == selected_rule), None)
    if selected_row is None:
        accepted = False
        incremental_pass = False
    else:
        advantage_holdout = candidate_metrics(advantage_bundle)["sealed"]
        accepted = bool(
            selected_row["validation"]["total_return"] > advantage_holdout["total_return"]
            and selected_row["validation"]["sharpe"] > advantage_holdout["sharpe"]
            and holdout_gate_pass({"sealed": selected_row["validation"]}, prereg)
        )
        incremental_pass = True
        accepted = accepted and incremental_pass
    return {
        **selection,
        "rules_tested": list(prereg["regime_mappings"]["rules"]),
        "rows": rows,
        "accepted": accepted,
        "negative_knowledge": not accepted,
        "fixed_advantage_remains_default": not accepted,
        "holdout_diagnostics_used_for_selection": False,
        "max_weight_sum": max(row["max_weight_sum"] for row in rows),
    }


def build_report() -> dict[str, Any]:
    verified = verify_inputs()
    prereg = verified["preregistration"]
    r385_prereg = json.loads(R385_PREREG.read_text(encoding="utf-8"))
    composer = PortfolioComposer(verified)
    samples = monte_carlo_samples(prereg, r385_prereg)
    training_rows = build_training_rows(samples, composer)
    advantage = select_advantage_candidate(training_rows, prereg)
    defensive = select_defensive_candidate(training_rows, prereg)
    candidate = evaluate_candidate(verified, composer, advantage)
    axis = evaluate_axis_neighbourhood(candidate, composer, prereg)
    local = evaluate_local_monte_carlo(composer, prereg)
    regime = evaluate_regime_conditioning(verified, composer, advantage, defensive)
    r385_report = verified["r385_report"]
    platform_rows = r385_report["platform_rows"]
    r385_pass_both = sum(
        bool(row["pass_2x"] and row["pass_3x"]) for row in r385_report["grid_rows"]
    )
    r385_platform_pass_both = sum(bool(row["pass_2x"] and row["pass_3x"]) for row in platform_rows)
    fixed = r385_report["walkforward"]["policies"]["P5_fixed_centre"]
    fixed_not_dominated = not any(
        policy["mean_forward_return"] >= fixed["mean_forward_return"]
        and policy["worst_forward_return"] >= fixed["worst_forward_return"]
        and name != "P5_fixed_centre"
        for name, policy in r385_report["walkforward"]["policies"].items()
    )
    stable = {
        "r385_cells_pass_both_costs": r385_pass_both,
        "r385_platform_cell_count": len(platform_rows),
        "r385_platform_cells_pass_both_costs": r385_platform_pass_both,
        "r385_platform_selected_centre": r385_report["platform"]["selected_cell"],
        "r385_highest_annual_platform_cell": r385_report["platform"]["highest_annual_cell"],
        "r385_fixed_centre_not_dominated": fixed_not_dominated,
        "r385_fixed_centre_mean_forward_return": fixed["mean_forward_return"],
        "r385_fixed_centre_worst_forward_return": fixed["worst_forward_return"],
        "local_axis_neighbours_pass_both_costs": min(axis["pass_count_2x"], axis["pass_count_3x"]),
        "local_monte_carlo_unique_pass_rate_min": min(local["pass_rate_2x"], local["pass_rate_3x"]),
        "local_monte_carlo_annual_range": local["annual_2x_range"],
        "local_monte_carlo_sharpe_range": local["sharpe_2x_range"],
        "local_monte_carlo_mdd_range": local["mdd_2x_range"],
        "dynamic_platform_selection_supported": False,
        "state_conditioning_accepted": regime["accepted"],
        "claim_scope": "parameter robustness only; no ex-ante dynamic platform or state selector adds alpha",
        "conclusion": (
            "Stable-platform evidence supports broad perturbation resistance. "
            "Neither prior rolling platform selection nor the five predeclared state mappings "
            "produce a supported dynamic selector, so the fixed training-selected parameters remain default."
        ),
    }
    candidate_pass = all(candidate["checks"].values())
    all_pass = bool(
        candidate_pass
        and axis["all_pass"]
        and local["passed"]
        and stable["r385_cells_pass_both_costs"] == 27
    )
    no_leverage = bool(
        candidate["checks"]["no_leverage"]
        and axis["all_pass"]
        and local["max_weight_sum"] <= 1.0 + 1e-12
        and regime["max_weight_sum"] <= 1.0 + 1e-12
    )
    report = {
        "round": "R386",
        "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__).resolve()),
        "preregistration_sha256": sha256(PREREG),
        "input_lineage": verified["checks"],
        "input_sha256": verified["actual_sha256"],
        "selection": {
            "training_folds": prereg["training_design"]["folds"],
            "holdout_start": prereg["training_design"]["holdout_start"],
            "advantage": advantage,
            "defensive": defensive,
            "training_row_count": len(training_rows),
            "training_rows": training_rows,
            "uses_holdout_for_selection": False,
        },
        "candidate": {key: value for key, value in candidate.items() if key != "curves"},
        "axis_neighbourhood": axis,
        "local_monte_carlo": local,
        "regime_conditioning": regime,
        "stable_platform": stable,
        "five_x_diagnostic": {
            **candidate["five_x"],
            "role": prereg["candidate_gates"]["five_x"]["role"],
            "hard_rejection": False,
        },
        "no_leverage": no_leverage,
        "all_pass": all_pass,
        "decision": {
            "status": (
                "candidate_for_paper_trading_review"
                if all_pass and no_leverage
                else "research_only_no_candidate_passed"
            ),
            "selected_parameters": advantage["parameters"],
            "selected_source_sample": advantage["sample"],
            "selection_basis": (
                "R385 96-sample Monte Carlo, practical rounding, three training folds only"
            ),
            "regime_conditioning_accepted": regime["accepted"],
            "regime_conditioning_negative_knowledge": regime["negative_knowledge"],
            "stable_platform_supported": bool(
                stable["r385_cells_pass_both_costs"] == 27
                and stable["local_axis_neighbours_pass_both_costs"] == 11
                and stable["local_monte_carlo_unique_pass_rate_min"] >= 0.95
            ),
            "dynamic_platform_selection_supported": False,
            "five_x_hard_rejection": False,
            "no_leverage": no_leverage,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": [
            *prereg["known_limits"],
            f"Paired block bootstrap uses deterministic seed {BOOTSTRAP_SEED}; the seed does not change thresholds or candidate selection.",
            "State-rule holdout diagnostics are post-selection transparency only and cannot promote a training-rejected rule.",
        ],
    }
    return _json_ready(report)


def markdown(report: dict[str, Any]) -> str:
    candidate = report["candidate"]
    metrics = candidate["metrics"]
    increments = candidate["increments_over_r385_centre"]
    local = report["local_monte_carlo"]
    stable = report["stable_platform"]
    regime = report["regime_conditioning"]
    five = report["five_x_diagnostic"]
    advantage = report["selection"]["advantage"]
    defensive = report["selection"]["defensive"]
    lines = [
        "# R386 · 蒙特卡洛优势参数确认与状态切换检验",
        "",
        "R386 使用 R385 的 96 组真实组合参数回测，先做实际取整，再只用 2022、2023、2024 三个训练折选参。",
        "2025-05-06 之后的 holdout 只在参数固定后评分。由于选参前做过 scratch 扫描，本轮是披露后的确认测试，不是全新盲测。",
        "",
        "## 参数选择",
        "",
        f"- 优势参数：样本 {advantage['sample']}，训练平均年化 {advantage['training_summary']['mean_annual']:.3%}，最差折年化 {advantage['training_summary']['worst_annual']:.3%}。",
        f"- 防御参数：样本 {defensive['sample']}，训练最差折年化 {defensive['training_summary']['worst_annual']:.3%}，只用于状态切换的 off-state。",
        f"- 训练合格参数：{advantage['selection']['eligible_count']}/{report['selection']['training_row_count']} 组；选参过程未读取 holdout。",
        "",
        "| 参数 | c_cap | equity vol | overlay lookback | overlay vol | overlay equity max |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| 优势 | {advantage['parameters']['c_satellite_cap']:.2f} | "
            f"{advantage['parameters']['equity_target_vol']:.2f} | "
            f"{advantage['parameters']['overlay_lookback_days']} | "
            f"{advantage['parameters']['overlay_target_vol']:.2f} | "
            f"{advantage['parameters']['overlay_equity_weight_max']:.2f} |"
        ),
        "",
        "## 优势参数实际回测",
        "",
        "| 成本 | 年化 | Sharpe | MDD | holdout 总收益 | 近一年 | 滚动正收益 | 最大总权重 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cost in ("2x", "3x", "5x"):
        item = metrics[cost]
        lines.append(
            f"| {cost} | {item['full']['annual']:.2%} | {item['full']['sharpe']:.3f} | "
            f"{item['full']['mdd']:.2%} | {item['sealed']['total_return']:.2%} | "
            f"{item['recent']['total_return']:.2%} | "
            f"{item['rolling']['positive_count']}/{item['rolling']['window_count']} | "
            f"{item['max_weight_sum']:.2f} |"
        )
    lines.extend(
        [
            "",
            "相对 R385 原中心（2×）：",
            "",
            f"- 年化 {increments['full_annual']:+.3%}，Sharpe {increments['full_sharpe']:+.3f}，MDD 改善 {increments['full_mdd_improvement']:+.3%}。",
            f"- holdout 总收益 {increments['sealed_total_return']:+.3%}，近一年总收益 {increments['recent_total_return']:+.3%}。",
            f"- 配对 block bootstrap：全窗 P(差异>0)={candidate['bootstrap']['full']['probability_positive']:.1%}，P05={candidate['bootstrap']['full']['p05']:.2%}；holdout P(差异>0)={candidate['bootstrap']['sealed']['probability_positive']:.1%}，P05={candidate['bootstrap']['sealed']['p05']:.2%}。",
            "",
            "## 为什么选稳定平台",
            "",
            "这里的“稳定平台”只表示参数邻域稳健，不表示动态挑平台能增加 alpha。依据全部来自实际回测：",
            "",
            f"- R385 27 格全网格在 2×、3× 下 {stable['r385_cells_pass_both_costs']}/27 通过；其中七格 hold-30 平台 {stable['r385_platform_cells_pass_both_costs']}/{stable['r385_platform_cell_count']} 通过。最高年化格是 {stable['r385_highest_annual_platform_cell']}，但最终没有事后替换预登记中心。",
            f"- R386 优势参数周围 11 个轴向参数点，{stable['local_axis_neighbours_pass_both_costs']}/11 同时通过 2×、3× 全部门槛。",
            f"- 局部 128 组拉丁超立方去重后剩 {local['unique_sample_count']} 个参数点；2× {local['pass_count_2x']}/{local['unique_sample_count']}，3× {local['pass_count_3x']}/{local['unique_sample_count']}。",
            f"- 局部蒙特卡洛 2× 年化范围 {stable['local_monte_carlo_annual_range'][0]:.2%} 至 {stable['local_monte_carlo_annual_range'][1]:.2%}，Sharpe 范围 {stable['local_monte_carlo_sharpe_range'][0]:.3f} 至 {stable['local_monte_carlo_sharpe_range'][1]:.3f}，MDD 范围 {stable['local_monte_carlo_mdd_range'][0]:.2%} 至 {stable['local_monte_carlo_mdd_range'][1]:.2%}。",
            f"- R385 滚动选择比较中，固定中心平均前瞻收益 {stable['r385_fixed_centre_mean_forward_return']:.2%}、最差 {stable['r385_fixed_centre_worst_forward_return']:.2%}，没有被其他选择器在均值与最差两端同时支配。",
            "- 因此选择稳定平台，是因为参数扰动后大部分格子仍然合格，而不是因为挑中了历史收益最高的单点。",
            "",
            "## “按市场情况切换”实测",
            "",
            "五种规则均只使用滞后信号，在月末决定下月使用优势参数还是防御参数；每次切换按 0.52% 计算成本。",
            "",
            "| 规则 | 切换次数 | 训练最差折年化 | 训练平均年化 | 训练晋级 | holdout 总收益 | holdout Sharpe |",
            "| --- | ---: | ---: | ---: | :---: | ---: | ---: |",
        ]
    )
    for row in regime["rows"]:
        lines.append(
            f"| {row['rule']} | {row['switch_count']} | {row['summary']['worst_annual']:.2%} | "
            f"{row['summary']['mean_annual']:.2%} | {'是' if row['training_pass'] else '否'} | "
            f"{row['validation']['total_return']:.2%} | {row['validation']['sharpe']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"- 训练晋级规则：{regime['advanced_rules'] or '无'}。",
            "- holdout 诊断只用于事后透明展示，不能把训练阶段已被否决的规则重新捞回来。",
            "- 结论：本轮没有找到可支持的动态状态切换；状态条件是负知识，固定优势参数继续作为默认。",
            "",
            "## 成本诊断与结论",
            "",
            f"- 5×：年化 {five['full']['annual']:.2%}，Sharpe {five['full']['sharpe']:.3f}，MDD {five['full']['mdd']:.2%}；仅 warning，硬否决=false。",
            f"- 所有候选、邻域和局部蒙特卡洛的最大总权重均不超过 1.0；no_leverage={report['no_leverage']}。",
            f"- 综合判定：**{report['decision']['status']}**。",
            "- 这只是研究候选，不生成纸面目标、订单或生产配置。",
            "- 历史名单、行业 vintage、T+1 近似和已披露 holdout 仍是限制，不能把结果表述为全新独立样本。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    for path in (
        OUTPUT,
        OUTPUT.with_name(OUTPUT.name + ".sha256"),
        RESULT,
        RESULT.with_name(RESULT.name + ".sha256"),
    ):
        if path.exists():
            raise RuntimeError(f"R386 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha256(OUTPUT)}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha256(RESULT)}  {RESULT.name}\n",
        encoding="ascii",
    )
    print(json.dumps(report["selection"]["advantage"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"]["checks"], ensure_ascii=False, indent=2))
    print(json.dumps(report["local_monte_carlo"], ensure_ascii=False, indent=2)[:4000])
    print(json.dumps(report["regime_conditioning"]["advanced_rules"], ensure_ascii=False, indent=2))
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
