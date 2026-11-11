#!/usr/bin/env python3
"""P3 parameter scan for event, industry, and market conditioner windows."""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.extended_parameter_data import (
    compare_factor_frames,
    load_event_panels,
    load_existing_ext_factors,
    load_market_series,
)
from app.factor_research.extended_parameter_evaluators import (
    EXTENDED_EVALUATORS,
    rank_score_series,
)
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.parameter_mc import (
    FDR_THRESHOLD,
    annotate_plateau,
    assign_family_q_values,
    bootstrap_ic,
    bootstrap_seed,
    choose_recommended_candidate,
    final_ci_low,
    initial_failure_counts,
    select_robust_candidates,
    stats_for_period,
)
from app.factor_research.parameters import (
    ParameterFamily,
    classify_factor_parameters,
    current_family_factor_id,
    detect_parameter_window,
    load_parameter_registry,
    validate_parameter_registry,
)
from app.factor_research.signal_scan import (
    block_bootstrap_mean_ci,
    calculate_rank_ic,
    time_series_rank_regression,
)
from app.services.industry_map import load_industry_l1
from app.strategy.scoring import neutralize_scoring_columns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p3_extended_parameter_mc_2026-11-11_v2.json"
)
P1_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p1_parameter_mc_2026-11-11.json"
)
P2_RESULTS = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p2_builtin_parameter_mc_2026-11-11_v2.json",
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p2_builtin_parameter_mc_2026-11-11.json",
)
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
RESEARCH_LOAD_START = date(2019, 1, 1)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
METHOD_VERSION = "extended_parameter_mc_v2_sparse_zero_filled"
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
INITIAL_BOOTSTRAP_RUNS = 1000
FINAL_BOOTSTRAP_RUNS = 5000
PRIMARY_BLOCK_SIZE = 20
FINAL_BLOCK_SIZES = (10, 20, 40)
OPERATION = "factor_parameter_mc_published"
SUPERSEDES_RESEARCH_ID = (
    "sha256:f75077fee50b760462f40c77e430d9d44123c96eb5fa456b9e6ae2ddde965eb1"
)

INDUSTRY_COLUMN = "industry_l1"
SIZE_COLUMN = "log_float_mv"
MARKET_ROLE = "conditioner"
MARKET_USE = "market-regime conditioner research only"

P3_CURRENT_PARITY_FAMILIES = (
    "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_industry_mom_60d",
    "ext_alt_hs300_industry_rps_20d",
    "ext_alt_hs300_industry_breadth_20d",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_insider_buy_20d",
    "ext_alt_hs300_insider_net_buy_20d",
    "ext_alt_hs300_insider_sell_20d_low",
    "ext_alt_hs300_lhb_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_inst_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_event_count_20d_low",
    "ext_alt_hs300_repurchase_event_60d",
    "ext_alt_hs300_block_trade_ratio_20d_low",
    "ext_alt_hs300_block_premium_20d",
    "ext_alt_hs300_analyst_rating_score_20d",
    "ext_alt_hs300_analyst_rating_change_20d",
    "ext_alt_hs300_bond_mom_20d",
    "ext_alt_hs300_bond_tf_mom_20d",
    "ext_alt_hs300_hsi_mom_20d",
    "ext_alt_hs300_hsciei_mom_20d",
    "ext_alt_hs300_hsi_rel_hs300_20d",
    "ext_alt_hs300_etf_share_chg_1m",
    "ext_alt_hs300_etf_share_chg_3m",
    "ext_alt_hs300_qvix_z_252",
    "ext_alt_hs300_market_skew_60d_low",
)
SPARSE_REFERENCE_FACTORS = frozenset(
    {
        "ext_alt_hs300_insider_buy_20d",
        "ext_alt_hs300_insider_net_buy_20d",
        "ext_alt_hs300_insider_sell_20d_low",
        "ext_alt_hs300_analyst_rating_change_20d",
    }
)


def load_p3_families(data_dir: Path) -> list[ParameterFamily]:
    data_dir = Path(data_dir)
    catalog = load_latest_catalog(data_dir)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    if catalog.source_completeness != "complete":
        raise RuntimeError(
            f"factor catalog source completeness is {catalog.source_completeness}"
        )
    registry = load_parameter_registry(data_dir)
    errors = validate_parameter_registry(
        registry,
        {factor.factor_id for factor in catalog.factors},
    )
    if errors:
        raise ValueError("; ".join(errors))
    classifications = classify_factor_parameters(catalog.factors, registry)
    unclassified = [
        row.factor_id for row in classifications if row.status == "unclassified"
    ]
    if unclassified:
        raise ValueError(f"factors remain unclassified: {unclassified}")
    families = [
        family
        for family in registry.families
        if family.status == "tunable" and family.evaluator in EXTENDED_EVALUATORS
    ]
    if len(families) != 23:
        raise ValueError(f"expected 23 P3 families, got {len(families)}")
    return families


def p3_research_registry_payload(
    families: Sequence[ParameterFamily],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "families": [
            {
                "family_id": family.family_id,
                "label": family.label,
                "data_class": family.data_class,
                "cross_section_type": family.cross_section_type,
                "direction": family.direction,
                "evaluator": family.evaluator,
                "parameter_name": family.parameter_name,
                "parameter_grid": family.parameter_grid,
                "current_parameter": family.current_parameter,
                "minimum_history": family.minimum_history,
                "neutralization": family.neutralization,
                "member_factor_ids": family.member_factor_ids,
                "status": family.status,
                "formula_template": family.formula_template,
            }
            for family in families
        ],
    }


def variant_id(family_id: str, parameter: int) -> str:
    return f"{family_id}__p_{parameter}"


def _directional_sign(family: ParameterFamily) -> int:
    return -1 if family.direction == "low" else 1


def _evaluator(family: ParameterFamily, context: Mapping[str, Any]):
    evaluators = context.get("evaluators", EXTENDED_EVALUATORS)
    evaluator = evaluators.get(family.evaluator)
    if evaluator is None:
        raise ValueError(f"extended evaluator is missing: {family.evaluator}")
    return evaluator


def _standard_factor_frame(raw: pl.DataFrame, output_column: str) -> pl.DataFrame:
    columns = ["date", pl.col(output_column).alias("factor_value")]
    if "symbol" in raw.columns:
        columns.insert(0, "symbol")
    return raw.select(columns)


def _neutralization_spec(names: Sequence[str]) -> dict[str, Any]:
    return {
        "industry": INDUSTRY_COLUMN if "industry" in names else None,
        "styles": [SIZE_COLUMN] if "size" in names else [],
        "exclude": [],
        "shrink": 1.0,
    }


def _neutralized_ic(
    joined: pl.DataFrame,
    names: Sequence[str],
    *,
    minimum_cross_section: int,
) -> pl.DataFrame:
    neutral_input = joined.select(
        [
            "date",
            "symbol",
            "forward_return_5d",
            "factor_value",
            INDUSTRY_COLUMN,
            SIZE_COLUMN,
        ]
    )
    neutralized = neutralize_scoring_columns(
        neutral_input,
        ["factor_value"],
        _neutralization_spec(names),
    )
    return calculate_rank_ic(
        neutralized,
        "factor_value",
        "forward_return_5d",
        minimum=minimum_cross_section,
    )


def _coverage(joined: pl.DataFrame) -> dict[str, Any]:
    valid = joined.filter(
        pl.col("factor_value").is_not_null()
        & pl.col("factor_value").is_finite()
        & pl.col("forward_return_5d").is_not_null()
        & pl.col("forward_return_5d").is_finite()
    )
    widths = valid.group_by("date").len().sort("date")["len"]
    return {
        "rows": joined.height,
        "valid_factor_rows": valid.height,
        "factor_non_null_ratio": (
            round(valid.height / joined.height, 6) if joined.height else 0.0
        ),
        "valid_dates": widths.len(),
        "minimum_cross_section": int(widths.min()) if widths.len() else 0,
        "median_cross_section": (
            round(float(widths.median()), 3) if widths.len() else 0.0
        ),
    }


def _empty_bootstrap() -> dict[str, Any]:
    return {
        "mean": None,
        "ci_low": None,
        "ci_high": None,
        "prob_positive": None,
        "n": 0,
        "block_size": None,
        "n_bootstrap": None,
    }


def _bootstrap_ic_or_empty(
    ic: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
    *,
    block_size: int,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    period = ic.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    if period.height < 2:
        return _empty_bootstrap()
    return bootstrap_ic(
        ic,
        start,
        end,
        sign,
        block_size=block_size,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )


def _ic_block(
    ic: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
) -> dict[str, Any]:
    return {
        "full": stats_for_period(
            ic,
            start,
            end,
            sign,
            horizon=HORIZON,
            include_distribution=True,
        ),
        "train": stats_for_period(
            ic,
            start,
            TRAIN_END,
            sign,
            horizon=HORIZON,
        ),
        "test": stats_for_period(
            ic,
            TEST_START,
            end,
            sign,
            horizon=HORIZON,
        ),
        "recent": stats_for_period(
            ic,
            RECENT_START,
            end,
            sign,
            horizon=HORIZON,
        ),
    }


def _gate_ic(
    raw_ic: pl.DataFrame,
    industry_ic: pl.DataFrame,
    size_ic: pl.DataFrame,
    combined_ic: pl.DataFrame,
    neutralization: Sequence[str],
) -> pl.DataFrame:
    names = set(neutralization)
    if names == {"industry", "size"}:
        return combined_ic
    if names == {"industry"}:
        return industry_ic
    if names == {"size"}:
        return size_ic
    if not names:
        return raw_ic
    raise ValueError(f"unsupported parameter neutralization: {sorted(names)}")


def primary_block_ci(trial: dict[str, Any]) -> float | None:
    value = final_ci_low(trial)
    return float(value) if isinstance(value, (int, float)) else None


def _recommendation_reasons(
    recommended: dict[str, Any] | None,
    finalist_count: int,
) -> list[str]:
    if recommended is not None:
        return []
    if finalist_count:
        return ["final_5000_block20_ci_not_positive"]
    return ["no_parameter_passed_initial_robustness_gate"]


def scan_stock_family(
    family: ParameterFamily,
    context: Mapping[str, Any],
    research_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    prices = context["prices"]
    minimum_cross_section = int(
        context.get("minimum_cross_section", MINIMUM_CROSS_SECTION)
    )
    evaluator = _evaluator(family, context)
    sign = _directional_sign(family)
    current_factor_id = current_family_factor_id(family)
    gate_ic_by_trial: dict[tuple[str, int], pl.DataFrame] = {}
    trials: list[dict[str, Any]] = []

    joined_base = prices.select(
        [
            "symbol",
            "date",
            "forward_return_5d",
            INDUSTRY_COLUMN,
            SIZE_COLUMN,
        ]
    )
    for parameter in family.parameter_grid:
        raw, output_column, formula_text = evaluator(context, parameter)
        if family.formula_template is not None:
            expected_formula = family.formula_template.format(parameter=parameter)
            if formula_text != expected_formula:
                raise ValueError(
                    f"formula template drift for {family.family_id}: "
                    f"{formula_text!r} != {expected_formula!r}"
                )
        factor_frame = _standard_factor_frame(raw, output_column)
        joined = (
            joined_base.join(factor_frame, on=["symbol", "date"], how="inner")
            .filter(pl.col("forward_return_5d").is_not_null())
            .sort(["date", "symbol"])
        )
        raw_ic = calculate_rank_ic(
            joined,
            "factor_value",
            "forward_return_5d",
            minimum=minimum_cross_section,
        )
        industry_ic = _neutralized_ic(
            joined,
            ["industry"],
            minimum_cross_section=minimum_cross_section,
        )
        size_ic = _neutralized_ic(
            joined,
            ["size"],
            minimum_cross_section=minimum_cross_section,
        )
        combined_ic = _neutralized_ic(
            joined,
            ["industry", "size"],
            minimum_cross_section=minimum_cross_section,
        )
        gate_ic = _gate_ic(
            raw_ic,
            industry_ic,
            size_ic,
            combined_ic,
            family.neutralization,
        )
        gate_ic_by_trial[(family.family_id, parameter)] = gate_ic
        gate_block = _ic_block(gate_ic, start, end, sign)
        seed = bootstrap_seed(
            research_id,
            family.family_id,
            parameter,
            PRIMARY_BLOCK_SIZE,
            INITIAL_BOOTSTRAP_RUNS,
        )
        trial = {
            "variant_id": variant_id(family.family_id, parameter),
            "family_id": family.family_id,
            "source_factor_id": current_factor_id,
            "parameter": parameter,
            "is_current": parameter == family.current_parameter,
            "formula_text": formula_text,
            "definition_fingerprint": sha256_json(
                {
                    "method_version": METHOD_VERSION,
                    "data_fingerprint": context.get("data_fingerprint"),
                    "family_id": family.family_id,
                    "parameter": parameter,
                    "formula_text": formula_text,
                }
            ),
            "coverage": _coverage(joined),
            **gate_block,
            "gate_neutralization": list(family.neutralization),
            "gate_neutral": gate_block,
            "industry_neutral": _ic_block(industry_ic, start, end, sign),
            "size_neutral": _ic_block(size_ic, start, end, sign),
            "industry_size_neutral": _ic_block(combined_ic, start, end, sign),
            "raw_bootstrap_full": _bootstrap_ic_or_empty(
                raw_ic,
                start,
                end,
                sign,
                block_size=PRIMARY_BLOCK_SIZE,
                n_bootstrap=INITIAL_BOOTSTRAP_RUNS,
                seed=seed,
            ),
            "bootstrap_full": _bootstrap_ic_or_empty(
                gate_ic,
                start,
                end,
                sign,
                block_size=PRIMARY_BLOCK_SIZE,
                n_bootstrap=INITIAL_BOOTSTRAP_RUNS,
                seed=seed,
            ),
            "q_value": None,
            "final_bootstrap": {},
        }
        trials.append(trial)

    annotate_plateau(trials)
    assign_family_q_values(trials)
    initial_finalists = select_robust_candidates(trials)
    for trial in initial_finalists:
        final_bootstrap: dict[str, Any] = {}
        gate_ic = gate_ic_by_trial[
            (family.family_id, int(trial["parameter"]))
        ]
        for block_size in FINAL_BLOCK_SIZES:
            final_bootstrap[str(block_size)] = _bootstrap_ic_or_empty(
                gate_ic,
                start,
                end,
                sign,
                block_size=block_size,
                n_bootstrap=FINAL_BOOTSTRAP_RUNS,
                seed=bootstrap_seed(
                    research_id,
                    family.family_id,
                    int(trial["parameter"]),
                    block_size,
                    FINAL_BOOTSTRAP_RUNS,
                ),
            )
        trial["final_bootstrap"] = final_bootstrap

    recommended = choose_recommended_candidate(
        initial_finalists,
        current_parameter=family.current_parameter,
    )
    current = next(trial for trial in trials if trial["is_current"])
    sensitivity_positive = None
    if recommended is not None:
        sensitivity_positive = all(
            recommended["final_bootstrap"][str(block)]["ci_low"] is not None
            and recommended["final_bootstrap"][str(block)]["ci_low"] > 0.0
            for block in FINAL_BLOCK_SIZES
        )
    return {
        "label": family.label,
        "evaluator": family.evaluator,
        "role": "stock_alpha_research",
        "stock_alpha_eligible": True,
        "use": "stock alpha parameter research candidate only",
        "cross_section_type": family.cross_section_type,
        "direction": family.direction,
        "parameter_name": family.parameter_name,
        "parameter_grid": family.parameter_grid,
        "current_parameter": family.current_parameter,
        "current_factor_id": current_factor_id,
        "member_factor_ids": family.member_factor_ids,
        "formula_template": family.formula_template,
        "gate_neutralization": family.neutralization,
        "recommended_parameter": (
            int(recommended["parameter"]) if recommended is not None else None
        ),
        "recommended_variant_id": (
            recommended["variant_id"] if recommended is not None else None
        ),
        "recommendation_status": (
            "recommended" if recommended is not None else "rejected"
        ),
        "final_block_sensitivity_positive": sensitivity_positive,
        "initial_finalist_count": len(initial_finalists),
        "initial_failure_counts": initial_failure_counts(trials),
        "rejection_reasons": _recommendation_reasons(
            recommended,
            len(initial_finalists),
        ),
        "current_trial": current,
        "recommended_trial": recommended,
        "trials": trials,
    }


def _market_stats_for_period(
    frame: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
) -> dict[str, Any]:
    period = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    result = time_series_rank_regression(
        period,
        "factor_value",
        "forward_return_5d",
        lag=HORIZON,
    )
    beta = result["beta"]
    directional_beta = (
        round(float(beta) * sign, 6) if isinstance(beta, (int, float)) else None
    )
    return {
        "n_dates": int(result["n"]),
        "directional_beta": directional_beta,
        "directional_ic": directional_beta,
        "nw_t": result["nw_t"],
        "p_value": result["p_value"],
        "average_cross_section": 1.0 if result["n"] else None,
    }


def _market_block(
    frame: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
) -> dict[str, Any]:
    return {
        "full": _market_stats_for_period(frame, start, end, sign),
        "train": _market_stats_for_period(frame, start, TRAIN_END, sign),
        "test": _market_stats_for_period(frame, TEST_START, end, sign),
        "recent": _market_stats_for_period(frame, RECENT_START, end, sign),
    }


def _market_score_frame(joined: pl.DataFrame, sign: int) -> pl.DataFrame:
    clean = (
        joined.select(["date", "factor_value", "forward_return_5d"])
        .filter(
            pl.col("factor_value").is_not_null()
            & pl.col("factor_value").is_finite()
            & pl.col("forward_return_5d").is_not_null()
            & pl.col("forward_return_5d").is_finite()
        )
        .sort("date")
    )
    if clean.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "score": pl.Float64})
    scores = rank_score_series(
        clean,
        "factor_value",
        "forward_return_5d",
        sign,
    )
    return scores.with_columns(clean["date"].alias("date")).select(
        ["date", "score"]
    )


def _bootstrap_market_or_empty(
    scores: pl.DataFrame,
    start: date,
    end: date,
    *,
    block_size: int,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    period = scores.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    if period.height < 2:
        return _empty_bootstrap()
    return block_bootstrap_mean_ci(
        period["score"].to_numpy(),
        block_size=block_size,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )


def _assign_market_q_values(trials: list[dict[str, Any]]) -> None:
    q_values = stats_v2.bh_fdr_qvalues(
        [trial["full"].get("p_value") for trial in trials]
    )
    for trial, q_value in zip(trials, q_values, strict=True):
        trial["q_value"] = (
            round(float(q_value), 8) if q_value is not None else None
        )


def market_initial_eligibility(trial: dict[str, Any]) -> bool:
    q_value = trial.get("q_value")
    ci_low = trial.get("bootstrap_full", {}).get("ci_low")
    return (
        bool(trial.get("robust_plateau"))
        and all(
            isinstance(trial[period].get("directional_beta"), (int, float))
            and trial[period]["directional_beta"] > 0.0
            for period in ("full", "train", "test")
        )
        and isinstance(q_value, (int, float))
        and float(q_value) <= FDR_THRESHOLD
        and isinstance(ci_low, (int, float))
        and float(ci_low) > 0.0
    )


def _choose_market_candidate(
    trials: list[dict[str, Any]],
    *,
    current_parameter: int,
) -> dict[str, Any] | None:
    eligible = [
        trial
        for trial in trials
        if market_initial_eligibility(trial)
        and (primary_block_ci(trial) is not None and primary_block_ci(trial) > 0.0)
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda trial: (
            min(
                float(trial["train"]["directional_beta"]),
                float(trial["test"]["directional_beta"]),
            ),
            float(primary_block_ci(trial)),
            -abs(int(trial["parameter"]) - current_parameter),
        ),
    )


def scan_market_family(
    family: ParameterFamily,
    context: Mapping[str, Any],
    research_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    market = context["market"]
    evaluator = _evaluator(family, context)
    sign = _directional_sign(family)
    current_factor_id = current_family_factor_id(family)
    scores_by_trial: dict[tuple[str, int], pl.DataFrame] = {}
    trials: list[dict[str, Any]] = []

    for parameter in family.parameter_grid:
        raw, output_column, formula_text = evaluator(context, parameter)
        if family.formula_template is not None:
            expected_formula = family.formula_template.format(parameter=parameter)
            if formula_text != expected_formula:
                raise ValueError(
                    f"formula template drift for {family.family_id}: "
                    f"{formula_text!r} != {expected_formula!r}"
                )
        signal = _standard_factor_frame(raw, output_column)
        if "symbol" in signal.columns:
            signal = signal.group_by("date").agg(
                pl.col("factor_value").drop_nulls().first().alias("factor_value")
            )
        joined = (
            market.select(["date", "forward_return_5d"])
            .join(signal.select(["date", "factor_value"]), on="date", how="inner")
            .filter(pl.col("forward_return_5d").is_not_null())
            .sort("date")
        )
        scores = _market_score_frame(joined, sign)
        scores_by_trial[(family.family_id, parameter)] = scores
        seed = bootstrap_seed(
            research_id,
            family.family_id,
            parameter,
            PRIMARY_BLOCK_SIZE,
            INITIAL_BOOTSTRAP_RUNS,
        )
        trial = {
            "variant_id": variant_id(family.family_id, parameter),
            "family_id": family.family_id,
            "source_factor_id": current_factor_id,
            "parameter": parameter,
            "is_current": parameter == family.current_parameter,
            "formula_text": formula_text,
            "definition_fingerprint": sha256_json(
                {
                    "method_version": METHOD_VERSION,
                    "data_fingerprint": context.get("data_fingerprint"),
                    "family_id": family.family_id,
                    "parameter": parameter,
                    "formula_text": formula_text,
                }
            ),
            "coverage": _coverage(joined),
            **_market_block(joined, start, end, sign),
            "bootstrap_full": _bootstrap_market_or_empty(
                scores,
                start,
                end,
                block_size=PRIMARY_BLOCK_SIZE,
                n_bootstrap=INITIAL_BOOTSTRAP_RUNS,
                seed=seed,
            ),
            "q_value": None,
            "final_bootstrap": {},
        }
        trials.append(trial)

    annotate_plateau(trials)
    _assign_market_q_values(trials)
    initial_finalists = [
        trial for trial in trials if market_initial_eligibility(trial)
    ]
    for trial in initial_finalists:
        final_bootstrap: dict[str, Any] = {}
        scores = scores_by_trial[(family.family_id, int(trial["parameter"]))]
        for block_size in FINAL_BLOCK_SIZES:
            final_bootstrap[str(block_size)] = _bootstrap_market_or_empty(
                scores,
                start,
                end,
                block_size=block_size,
                n_bootstrap=FINAL_BOOTSTRAP_RUNS,
                seed=bootstrap_seed(
                    research_id,
                    family.family_id,
                    int(trial["parameter"]),
                    block_size,
                    FINAL_BOOTSTRAP_RUNS,
                ),
            )
        trial["final_bootstrap"] = final_bootstrap

    recommended = _choose_market_candidate(
        initial_finalists,
        current_parameter=family.current_parameter,
    )
    current = next(trial for trial in trials if trial["is_current"])
    sensitivity_positive = None
    if recommended is not None:
        sensitivity_positive = all(
            recommended["final_bootstrap"][str(block)]["ci_low"] is not None
            and recommended["final_bootstrap"][str(block)]["ci_low"] > 0.0
            for block in FINAL_BLOCK_SIZES
        )
    return {
        "label": family.label,
        "evaluator": family.evaluator,
        "role": MARKET_ROLE,
        "stock_alpha_eligible": False,
        "use": MARKET_USE,
        "cross_section_type": family.cross_section_type,
        "direction": family.direction,
        "parameter_name": family.parameter_name,
        "parameter_grid": family.parameter_grid,
        "current_parameter": family.current_parameter,
        "current_factor_id": current_factor_id,
        "member_factor_ids": family.member_factor_ids,
        "formula_template": family.formula_template,
        "recommended_parameter": (
            int(recommended["parameter"]) if recommended is not None else None
        ),
        "recommended_variant_id": (
            recommended["variant_id"] if recommended is not None else None
        ),
        "recommendation_status": (
            "recommended" if recommended is not None else "rejected"
        ),
        "final_block_sensitivity_positive": sensitivity_positive,
        "initial_finalist_count": len(initial_finalists),
        "rejection_reasons": _recommendation_reasons(
            recommended,
            len(initial_finalists),
        ),
        "current_trial": current,
        "recommended_trial": recommended,
        "trials": trials,
    }


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def _period_value(trial: dict[str, Any], period: str) -> Any:
    return trial[period].get("directional_beta", trial[period].get("directional_ic"))


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P3 扩展数据窗口参数族蒙特卡洛报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 方法版本：`{payload['method_version']}`",
        f"- 样本：`{payload['period']['start']}` 至 `{payload['period']['end']}`",
        f"- 参数族：{payload['family_count']}；参数点：{payload['trial_count']}",
        f"- 数值对齐：`{payload['parity']['status']}`；变体数：{payload['parity']['variant_count']}",
        "- 市场级因子只生成 conditioner 研究候选，不进入股票 alpha 腿、watch 或 active 版本。",
        "",
        "## 家族结论",
        "",
        "| 参数族 | 角色 | 当前 | 推荐 | 结论 | 当前全样本 | 当前检验 | 门禁中性化 | q | 决赛主 CI |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for family_id, result in payload["families"].items():
        current = result["current_trial"]
        recommended = result.get("recommended_trial")
        gate = (
            "none"
            if result["role"] == MARKET_ROLE
            else "+".join(result.get("gate_neutralization", [])) or "none"
        )
        lines.append(
            f"| `{family_id}` | {result['role']} | {result['current_parameter']} | "
            f"{result['recommended_parameter'] if result['recommended_parameter'] is not None else '-'} | "
            f"{result['recommendation_status']} | "
            f"{_fmt(_period_value(current, 'full'))} | "
            f"{_fmt(_period_value(current, 'test'))} | {gate} | "
            f"{_fmt(current.get('q_value'))} | "
            f"{_fmt(primary_block_ci(recommended) if recommended else None)} |"
        )

    lines.extend(
        [
            "",
            "## 参数点统计",
            "",
            "| 参数族 | 参数 | 全样本 | 训练 | 检验 | 近一年 | 初筛 CI 下限 | q | 平台 | 决赛主 CI |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for family_id, result in payload["families"].items():
        for trial in result["trials"]:
            lines.append(
                f"| `{family_id}` | {trial['parameter']} | "
                f"{_fmt(_period_value(trial, 'full'))} | "
                f"{_fmt(_period_value(trial, 'train'))} | "
                f"{_fmt(_period_value(trial, 'test'))} | "
                f"{_fmt(_period_value(trial, 'recent'))} | "
                f"{_fmt(trial['bootstrap_full']['ci_low'])} | "
                f"{_fmt(trial.get('q_value'))} | {trial['plateau_status']} | "
                f"{_fmt(primary_block_ci(trial))} |"
            )

    lines.extend(
        [
            "",
            "## 判定口径",
            "",
            "- 股票/事件族：按注册表声明的行业、规模中性化执行 Rank IC 门禁；初筛要求稳健平台、全样本/训练/检验方向 IC 为正、门禁中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 bootstrap `ci_low > 0`。",
            "- 市场 conditioner：使用时间序列 rank regression 的方向 beta；初筛要求稳健平台、全样本/训练/检验 beta 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 score bootstrap `ci_low > 0`。",
            "- 决赛：入围点使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。",
            "- 股票族推荐排序：先最大化 `min(train_ic, test_ic)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。",
            "- 市场族推荐排序：先最大化 `min(train_beta, test_beta)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。",
            "- 市场结论只允许用于 regime conditioner 研究，不能写成股票 alpha、watch 或 active。",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_output(value: str) -> Path:
    path = Path(value)
    allowed = (DATA / "factor_research" / "research" / "parameters").resolve()
    candidates = (
        (path,)
        if path.is_absolute()
        else (Path.cwd() / path, ROOT / path)
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(allowed):
            return resolved
    raise ValueError(
        "parameter output must stay inside data/factor_research/research/parameters"
    )


def _publish_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"parameter result already exists: {path}")
        return existing
    atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"parameter markdown already exists: {path}")
        return
    atomic_write_text(path, text)


def _manifest_group(
    data_dir: Path,
    label: str,
    relative_root: str,
    patterns: Sequence[str],
) -> dict[str, Any]:
    root = data_dir / relative_root
    paths = sorted({path for pattern in patterns for path in root.glob(pattern)})
    if not paths:
        raise FileNotFoundError(f"{label} source files are missing: {root}")
    files = [
        {
            "path": str(path.relative_to(data_dir)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]
    return {
        "label": label,
        "root": relative_root,
        "patterns": list(patterns),
        "file_count": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": sha256_json(files),
    }


def _input_manifest(data_dir: Path) -> list[dict[str, Any]]:
    return [
        _manifest_group(
            data_dir,
            "enriched_daily",
            "kline_daily_enriched",
            ["date=*/part.parquet"],
        ),
        _manifest_group(
            data_dir,
            "index_daily",
            "kline_index_daily",
            ["date=*/part.parquet"],
        ),
        _manifest_group(
            data_dir,
            "existing_extension_factors",
            "ext_data/alt_hs300/timeseries",
            ["date=*/part.parquet"],
        ),
        _manifest_group(data_dir, "industry_map", "ext_data/ext_hy_ths", ["*.parquet"]),
        _manifest_group(data_dir, "instruments", "instruments", ["*.parquet"]),
        _manifest_group(
            data_dir,
            "insider_events",
            "research/r90_insider_trade",
            ["*.parquet"],
        ),
        _manifest_group(
            data_dir,
            "lhb_events",
            "research/r43_lhb",
            ["*.parquet"],
        ),
        _manifest_group(
            data_dir,
            "repurchase_events",
            "research/external_r39",
            ["repurchase.parquet"],
        ),
        _manifest_group(
            data_dir,
            "block_trade_events",
            "research/external_r39",
            ["dzjy_*.parquet"],
        ),
        _manifest_group(
            data_dir,
            "analyst_reports",
            "research/r47_analyst",
            ["*.parquet"],
        ),
        _manifest_group(
            data_dir,
            "treasury_futures",
            "research/r193_treasury",
            ["T0.parquet", "TF0.parquet"],
        ),
        _manifest_group(
            data_dir,
            "hong_kong_indices",
            "research/r195_hk",
            ["HSI.parquet", "HSCEI.parquet"],
        ),
        _manifest_group(
            data_dir,
            "etf_shares",
            "research/r74_etf_flow",
            ["*.parquet"],
        ),
        _manifest_group(
            data_dir,
            "qvix",
            "research/r75_qvix",
            ["qvix_300etf.parquet"],
        ),
    ]


def build_research_context(
    data_dir: Path,
    symbols: Sequence[str],
    end: date,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    price_paths = sorted((data_dir / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not price_paths:
        raise FileNotFoundError(data_dir / "kline_daily_enriched")
    prices = (
        pl.scan_parquet([str(path) for path in price_paths])
        .filter(
            pl.col("symbol").is_in(list(symbols))
            & (pl.col("date") >= RESEARCH_LOAD_START)
            & (pl.col("date") <= end)
        )
        .select(["symbol", "date", "close", "volume", "turnover_rate"])
        .collect()
        .sort(["symbol", "date"])
    )
    prices = prices.with_columns(
        (
            pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0
        ).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias(SIZE_COLUMN),
    ).join(load_industry_l1(data_dir), on="symbol", how="left")

    index_paths = sorted((data_dir / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise FileNotFoundError(data_dir / "kline_index_daily")
    market = (
        pl.scan_parquet([str(path) for path in index_paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= RESEARCH_LOAD_START)
            & (pl.col("date") <= end)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    market = market.with_columns(
        (pl.col("close").shift(-HORIZON) / pl.col("close") - 1.0).alias(
            "forward_return_5d"
        )
    )
    events = load_event_panels(data_dir, list(symbols))
    return {
        "prices": prices,
        "calendar": prices.select(["symbol", "date"]),
        "market": market,
        "events": events,
        "analyst_reports": events["analyst"],
        "series": load_market_series(data_dir),
        "minimum_cross_section": MINIMUM_CROSS_SECTION,
        "evaluators": EXTENDED_EVALUATORS,
    }


def _parity_context(
    data_dir: Path,
    existing: pl.DataFrame,
) -> dict[str, Any]:
    symbols = sorted(existing["symbol"].unique().to_list())
    dates = sorted(existing["date"].unique().to_list())
    price_paths = sorted((data_dir / "kline_daily_enriched").glob("date=*/part.parquet"))
    prices = (
        pl.scan_parquet([str(path) for path in price_paths])
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_in(dates))
        .select(["symbol", "date", "close"])
        .collect()
        .sort(["symbol", "date"])
        .join(load_industry_l1(data_dir), on="symbol", how="left")
    )
    calendar = (
        pl.DataFrame({"date": dates})
        .join(pl.DataFrame({"symbol": symbols}), how="cross")
        .sort(["symbol", "date"])
    )
    index_paths = sorted((data_dir / "kline_index_daily").glob("date=*/part.parquet"))
    market = (
        pl.scan_parquet([str(path) for path in index_paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH") & pl.col("date").is_in(dates)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    events = load_event_panels(data_dir, symbols)
    return {
        "prices": prices,
        "calendar": calendar,
        "market": market,
        "events": events,
        "analyst_reports": events["analyst"],
        "series": load_market_series(data_dir),
        "evaluators": EXTENDED_EVALUATORS,
    }


def _parity_fields(families: Sequence[ParameterFamily]) -> dict[str, str]:
    return {
        factor_id: factor_id.removeprefix("ext_alt_hs300_")
        for family in families
        for factor_id in family.member_factor_ids
    }


def _fill_reference_zeros(
    generated: pl.DataFrame,
    reference: pl.DataFrame,
    key_columns: Sequence[str],
) -> pl.DataFrame:
    zero_keys = (
        reference.filter(pl.col("factor_value") == 0.0)
        .select(list(key_columns))
        .unique()
    )
    missing_zero_rows = (
        zero_keys.join(generated, on=list(key_columns), how="anti")
        .with_columns(pl.lit(0.0).alias("factor_value"))
        .select([*key_columns, "factor_value"])
    )
    return pl.concat(
        [generated, missing_zero_rows],
        how="diagonal_relaxed",
    )


def run_parity_checks(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    families = load_p3_families(data_dir)
    fields = _parity_fields(families)
    expected = set(P3_CURRENT_PARITY_FAMILIES)
    if set(fields) != expected:
        missing = sorted(expected - set(fields))
        extra = sorted(set(fields) - expected)
        raise ValueError(
            f"P3 parity registry drift: missing={missing}, extra={extra}"
        )
    existing = load_existing_ext_factors(data_dir, fields)
    context = _parity_context(data_dir, existing)
    variants: list[dict[str, Any]] = []
    for family in families:
        evaluator = EXTENDED_EVALUATORS[family.evaluator]
        for factor_id in family.member_factor_ids:
            parameter = detect_parameter_window(factor_id)
            if parameter is None:
                raise ValueError(f"parity member has no parameter window: {factor_id}")
            raw, output_column, formula_text = evaluator(context, parameter)
            if family.formula_template is not None:
                expected_formula = family.formula_template.format(parameter=parameter)
                if formula_text != expected_formula:
                    raise ValueError(
                        f"parity formula drift for {factor_id}: "
                        f"{formula_text!r} != {expected_formula!r}"
                    )
            generated = _standard_factor_frame(raw, output_column)
            if family.cross_section_type == "market_level":
                if "symbol" in generated.columns:
                    generated = generated.group_by("date").agg(
                        pl.col("factor_value")
                        .drop_nulls()
                        .first()
                        .alias("factor_value")
                    )
                reference = existing.select(
                    ["date", pl.col(factor_id).alias("factor_value")]
                ).group_by("date").agg(
                    pl.col("factor_value").drop_nulls().first().alias("factor_value")
                )
                key_columns = ["date"]
            else:
                reference = existing.select(
                    [
                        "symbol",
                        "date",
                        pl.col(factor_id).alias("factor_value"),
                    ]
                )
                key_columns = ["symbol", "date"]
            if factor_id in SPARSE_REFERENCE_FACTORS:
                generated = (
                    reference.filter(pl.col("factor_value").is_not_null())
                    .select(list(key_columns))
                    .unique()
                    .join(generated, on=list(key_columns), how="left")
                    .with_columns(pl.col("factor_value").fill_null(0.0))
                )
            generated = _fill_reference_zeros(
                generated,
                reference,
                key_columns,
            )
            comparison = compare_factor_frames(
                generated,
                reference,
                "factor_value",
            )
            variants.append(
                {
                    "factor_id": factor_id,
                    "family_id": family.family_id,
                    "parameter": parameter,
                    "formula_text": formula_text,
                    **comparison,
                }
            )
    status = "pass" if all(item["status"] == "pass" for item in variants) else "fail"
    return {
        "status": status,
        "variant_count": len(variants),
        "minimum_key_overlap": min(
            (float(item["key_overlap_ratio"]) for item in variants),
            default=0.0,
        ),
        "minimum_reference_overlap": min(
            (float(item["reference_overlap_ratio"]) for item in variants),
            default=0.0,
        ),
        "maximum_abs_error": max(
            (
                float(item["max_abs_error"])
                for item in variants
                if item["max_abs_error"] is not None
            ),
            default=0.0,
        ),
        "variants": variants,
    }


def compare_one_current_variant(
    family_id: str,
    *,
    data_dir: Path = DATA,
) -> dict[str, Any]:
    families = load_p3_families(data_dir)
    family = next((item for item in families if item.family_id == family_id), None)
    if family is None:
        raise ValueError(f"P3 family is unavailable: {family_id}")
    factor_id = current_family_factor_id(family)
    parity = run_parity_checks(data_dir)
    variant = next(item for item in parity["variants"] if item["factor_id"] == factor_id)
    return {**variant, "parity_status": parity["status"]}


def _research_ids() -> tuple[str, str]:
    if not P1_RESULT.exists():
        raise FileNotFoundError(P1_RESULT)
    p1_payload = json.loads(P1_RESULT.read_text(encoding="utf-8"))
    p2_path = next((path for path in P2_RESULTS if path.exists()), None)
    if p2_path is None:
        raise FileNotFoundError(P2_RESULTS[0])
    p2_payload = json.loads(p2_path.read_text(encoding="utf-8"))
    return p1_payload["research_id"], p2_payload["research_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--end", default=str(DEFAULT_END))
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT.relative_to(ROOT)),
        help="Repository-relative JSON output path.",
    )
    parser.add_argument("--parity-only", action="store_true")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end <= start or end <= TRAIN_END:
        raise ValueError("end date must be after the training window")

    parity = run_parity_checks(DATA)
    if parity["status"] != "pass":
        failed = [
            item["factor_id"] for item in parity["variants"] if item["status"] != "pass"
        ]
        raise ValueError(f"P3 current-window parity failed: {failed}")
    if args.parity_only:
        print(
            json.dumps(
                {
                    "status": parity["status"],
                    "variant_count": parity["variant_count"],
                    "minimum_key_overlap": parity["minimum_key_overlap"],
                    "minimum_reference_overlap": parity[
                        "minimum_reference_overlap"
                    ],
                    "maximum_abs_error": parity["maximum_abs_error"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    output = _resolve_output(args.output)
    markdown_path = output.with_suffix(".md")
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    families = load_p3_families(DATA)
    p1_research_id, p2_research_id = _research_ids()
    manifest = _input_manifest(DATA)
    input_manifest_sha256 = sha256_json(manifest)

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    context = build_research_context(DATA, symbols, end)
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha256,
            "symbols": sorted(symbols),
            "start": str(start),
            "end": str(end),
            "price_rows": context["prices"].height,
            "market_rows": context["market"].height,
        }
    )
    context["data_fingerprint"] = data_fingerprint
    research_id = sha256_json(
        {
            "method_version": METHOD_VERSION,
            "catalog_snapshot_id": catalog.snapshot_id,
            "parameter_registry": p3_research_registry_payload(families),
            "p1_research_id": p1_research_id,
            "p2_research_id": p2_research_id,
            "input_manifest_sha256": input_manifest_sha256,
            "start": str(start),
            "end": str(end),
            "horizon": HORIZON,
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "initial_bootstrap_runs": INITIAL_BOOTSTRAP_RUNS,
            "final_bootstrap_runs": FINAL_BOOTSTRAP_RUNS,
            "final_block_sizes": FINAL_BLOCK_SIZES,
            "fdr_threshold": FDR_THRESHOLD,
        }
    )

    family_results: dict[str, Any] = {}
    all_trials: list[dict[str, Any]] = []
    for family in families:
        if family.cross_section_type == "market_level":
            result = scan_market_family(
                family,
                context,
                research_id,
                start,
                end,
            )
        else:
            result = scan_stock_family(
                family,
                context,
                research_id,
                start,
                end,
            )
        family_results[family.family_id] = result
        all_trials.extend(result["trials"])

    payload = {
        "schema_version": 1,
        "research_id": research_id,
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "catalog_snapshot_id": catalog.snapshot_id,
        "parameter_registry_sha256": sha256_file(
            DATA / "factor_research" / "parameters" / "families.json"
        ),
        "p1_research_id": p1_research_id,
        "p2_research_id": p2_research_id,
        "data_fingerprint": data_fingerprint,
        "input_manifest_sha256": input_manifest_sha256,
        "input_manifest": manifest,
        "period": {
            "start": str(start),
            "end": str(end),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
        },
        "universe": {
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
        },
        "return_horizon_days": HORIZON,
        "minimum_cross_section": MINIMUM_CROSS_SECTION,
        "method": {
            "parameter_grid": "pre-registered registry grids only",
            "train_test": "train through 2024-12-31; test from 2025-01-01",
            "stock_statistic": "cross-sectional rank IC with declared neutralization gate",
            "market_statistic": "time-series rank regression and signed rank-score bootstrap",
            "initial_monte_carlo": {
                "runs": INITIAL_BOOTSTRAP_RUNS,
                "block_size": PRIMARY_BLOCK_SIZE,
            },
            "final_monte_carlo": {
                "runs": FINAL_BOOTSTRAP_RUNS,
                "block_sizes": list(FINAL_BLOCK_SIZES),
                "primary_block_size": PRIMARY_BLOCK_SIZE,
            },
            "multiple_testing": "BH-FDR across each family parameter grid",
            "fdr_threshold": FDR_THRESHOLD,
            "market_boundary": MARKET_USE,
            "parity": "all 25 registered P3 variants must match the existing extension table",
        },
        "parity": {
            key: value for key, value in parity.items() if key != "variants"
        }
        | {"variants": parity["variants"]},
        "family_count": len(family_results),
        "trial_count": len(all_trials),
        "finalist_count": sum(
            result["initial_finalist_count"] for result in family_results.values()
        ),
        "recommended_parameter_count": sum(
            1
            for result in family_results.values()
            if result["recommended_parameter"] is not None
        ),
        "rejected_family_count": sum(
            1
            for result in family_results.values()
            if result["recommendation_status"] == "rejected"
        ),
        "conditioner_family_count": sum(
            1
            for result in family_results.values()
            if result["role"] == MARKET_ROLE
        ),
        "recommended_conditioner_count": sum(
            1
            for result in family_results.values()
            if result["role"] == MARKET_ROLE
            and result["recommended_parameter"] is not None
        ),
        "active_version": None,
        "families": family_results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _publish_json(output, payload)
    _publish_markdown(markdown_path, render_markdown(payload))

    ledger = ResearchLedger(DATA)
    ledger.verify()
    ledger_records = ledger.records()
    existing_records = [
        record
        for record in ledger_records
        if record.operation == OPERATION
        and record.input_fingerprints.get("research_id") == payload["research_id"]
    ]
    if not existing_records:
        ledger.append(
            OPERATION,
            payload={
                "catalog_snapshot_id": payload["catalog_snapshot_id"],
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "p1_research_id": payload["p1_research_id"],
                    "p2_research_id": payload["p2_research_id"],
                    "parameter_registry_sha256": payload[
                        "parameter_registry_sha256"
                    ],
                    "input_manifest_sha256": payload["input_manifest_sha256"],
                    "data_fingerprint": payload["data_fingerprint"],
                    "parity_status": payload["parity"]["status"],
                    "output_sha256": sha256_file(output),
                    "markdown_sha256": sha256_file(markdown_path),
                },
                "evidence_refs": [
                    {
                        "kind": "factor_parameter_mc",
                        "path": str(output.relative_to(DATA)),
                    },
                    {
                        "kind": "factor_parameter_mc_markdown",
                        "path": str(markdown_path.relative_to(DATA)),
                    },
                ],
                "supersedes_record_id": next(
                    (
                        record.record_id
                        for record in ledger_records
                        if record.input_fingerprints.get("research_id")
                        == SUPERSEDES_RESEARCH_ID
                    ),
                    None,
                ),
                "metrics": {
                    "family_count": payload["family_count"],
                    "trial_count": payload["trial_count"],
                    "finalist_count": payload["finalist_count"],
                    "recommended_parameter_count": payload[
                        "recommended_parameter_count"
                    ],
                    "rejected_family_count": payload["rejected_family_count"],
                    "conditioner_family_count": payload[
                        "conditioner_family_count"
                    ],
                    "recommended_conditioner_count": payload[
                        "recommended_conditioner_count"
                    ],
                },
                "verdict": "conditioners_and_stock_candidates_only_no_active_version",
            },
        )

    print(
        json.dumps(
            {
                "research_id": payload["research_id"],
                "output": str(output.relative_to(ROOT)),
                "markdown": str(markdown_path.relative_to(ROOT)),
                "parity": payload["parity"]["status"],
                "family_count": payload["family_count"],
                "trial_count": payload["trial_count"],
                "finalist_count": payload["finalist_count"],
                "recommended_parameter_count": payload[
                    "recommended_parameter_count"
                ],
                "recommended_conditioner_count": payload[
                    "recommended_conditioner_count"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
