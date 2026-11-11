#!/usr/bin/env python3
"""P9 financial horizon and T-1 regime robustness research."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.factor_research.automation import bind_research_evidence
from app.factor_research.fingerprints import sha256_json, utc_now
from app.factor_research.incremental import rank_score_frame
from app.factor_research.signal_scan import calculate_rank_ic
from app.services.regime_builder import load_regime_history, run_regime_batch
from app.strategy.scoring import neutralize_scoring_columns
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA
    / "factor_research"
    / "research"
    / "p9_financial_regime_2026-11-12_v3.json"
)
METHOD_VERSION = "financial_regime_p9_v3"
OPERATION = "financial_regime_scan_published"
FORWARD_HORIZONS = (5, 20, 60)
REGIME_HYPOTHESES = {
    "pb_latest": {"direction": "low", "label": "低PB"},
    "accrual": {"direction": "low", "label": "低应计"},
    "ocf_ni": {"direction": "high", "label": "经营现金流/净利润"},
}
BASE_DROP_FACTOR = "ext_alt_hs300_holder_top10_share_chg_yoy"
CANDIDATE_VARIANTS = {
    "pb_weak": ("pb_latest",),
    "accrual_weak": ("accrual",),
    "ocf_ni_weak": ("ocf_ni",),
    "pb_accrual_weak": ("pb_latest", "accrual"),
    "pb_accrual_ocf_ni_weak": ("pb_latest", "accrual", "ocf_ni"),
}
NEUTRAL_VARIANTS = ("industry", "size", "industry_size")
WEIGHT_CENTER = 1.0 / 8.0


def _load_sibling(name: str):
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p9_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sibling research module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p8 = _load_sibling("research_financial_increment_p8")
p5 = p8.p5
P8_RESULT = DATA / "factor_research" / "research" / "p8_financial_increment_2026-11-12.json"
P5_RESULT = p8.P5_RESULT
COST_WARNING_5X = p8.COST_WARNING_5X


def _variant_spec(variant: str) -> dict[str, Any]:
    return {
        "raw": {"industry": None, "styles": [], "exclude": [], "shrink": 1.0},
        "industry": {"industry": "industry_l1", "styles": [], "exclude": [], "shrink": 1.0},
        "size": {"industry": None, "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
        "industry_size": {
            "industry": "industry_l1",
            "styles": ["log_float_mv"],
            "exclude": [],
            "shrink": 1.0,
        },
    }[variant]


def _date_expr(column: str = "date") -> pl.Expr:
    return pl.col(column).cast(pl.Date, strict=False)


def regime_mask_t_minus_one(
    panel_dates: pl.DataFrame,
    regime_history: pl.DataFrame,
    *,
    require_previous: bool = True,
) -> pl.DataFrame:
    """Attach the previous trading session's regime state to each panel date."""
    if "date" not in panel_dates.columns or "date" not in regime_history.columns:
        raise ValueError("regime mask inputs need a date column")
    if "state" not in regime_history.columns:
        raise ValueError("regime history needs a state column")
    dates = (
        panel_dates.select(_date_expr("date").alias("date"))
        .drop_nulls()
        .unique()
        .sort("date")
    )
    history = (
        regime_history.select(
            _date_expr("date").alias("regime_date"),
            pl.col("state").cast(pl.Utf8).alias("state"),
        )
        .drop_nulls(["regime_date", "state"])
        .unique(subset=["regime_date"], keep="last")
        .sort("regime_date")
    )
    aligned = dates.with_columns(pl.col("date").shift(1).alias("regime_date")).join(
        history,
        on="regime_date",
        how="left",
        maintain_order="left",
    )
    if require_previous:
        # The first panel row has no in-panel predecessor. It is a warmup row
        # and is represented as a non-weak state; every later row must resolve.
        missing = aligned.filter(
            (pl.row_index() > 0) & pl.col("state").is_null()
        )
        if missing.height:
            raise ValueError(
                "regime mask has no previous trading state for "
                f"{missing['date'][0]}"
            )
    return aligned.select(
        "date",
        "regime_date",
        pl.col("state").cast(pl.Utf8),
        pl.col("state").eq("weak").fill_null(False).alias("is_weak"),
    )


def effective_regime_fingerprint(regime_mask: pl.DataFrame) -> dict[str, Any]:
    """Hash the exact T-1 states used by the conditional research panel."""
    required = {"date", "regime_date", "state", "is_weak"}
    missing = sorted(required - set(regime_mask.columns))
    if missing:
        raise ValueError(f"effective regime mask missing columns: {missing}")
    normalized = regime_mask.select(
        _date_expr("date").cast(pl.Utf8).alias("signal_date"),
        _date_expr("regime_date").cast(pl.Utf8).alias("regime_date"),
        pl.col("state").cast(pl.Utf8),
        pl.col("is_weak").cast(pl.Boolean).fill_null(False).alias("is_weak"),
    ).sort("signal_date")
    if normalized["signal_date"].n_unique() != normalized.height:
        raise ValueError("effective regime mask has duplicate signal dates")
    return {
        "algorithm": "sha256_json",
        "sha256": sha256_json(normalized.to_dicts()),
        "row_count": normalized.height,
        "weak_signal_date_count": int(normalized["is_weak"].sum()),
        "first_signal_date": normalized["signal_date"].min(),
        "last_signal_date": normalized["signal_date"].max(),
    }


def build_horizon_panel(
    panel: pl.DataFrame,
    horizons: Sequence[int] = FORWARD_HORIZONS,
) -> pl.DataFrame:
    """Add close-to-close forward returns; the final h rows stay null."""
    required = {"symbol", "date", "close"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"horizon panel missing columns: {missing}")
    out = panel.sort(["symbol", "date"])
    expressions = []
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("forward horizons must be positive")
        expressions.append(
            (
                pl.col("close").shift(-int(horizon)).over("symbol")
                / pl.col("close")
                - 1.0
            )
            .alias(f"forward_return_{int(horizon)}d")
        )
    return out.with_columns(expressions)


def neutralized_ic_grid(
    panel: pl.DataFrame,
    factor_ids: Sequence[str] = tuple(REGIME_HYPOTHESES),
    horizons: Sequence[int] = FORWARD_HORIZONS,
    variants: Sequence[str] = ("raw", *NEUTRAL_VARIANTS),
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """Calculate direction-adjusted IC across horizons and neutralization variants."""
    required = {"symbol", "date", "industry_l1", "log_float_mv"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"IC grid panel missing columns: {missing}")
    unknown = sorted(set(factor_ids) - set(REGIME_HYPOTHESES))
    if unknown:
        raise ValueError(f"unsupported P9 financial factors: {unknown}")
    grid: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for factor_id in factor_ids:
        direction = str(REGIME_HYPOTHESES[factor_id]["direction"])
        sign = -1.0 if direction == "low" else 1.0
        signed_column = f"_p9_ic_{factor_id}"
        grid[factor_id] = {}
        for horizon in horizons:
            return_column = f"forward_return_{int(horizon)}d"
            if return_column not in panel.columns:
                raise ValueError(f"IC grid missing forward return: {return_column}")
            grid[factor_id][str(int(horizon))] = {}
            for variant in variants:
                work = (
                    panel.select(
                        [
                            "date",
                            "symbol",
                            "industry_l1",
                            "log_float_mv",
                            factor_id,
                            return_column,
                        ]
                    )
                    .with_columns((pl.col(factor_id) * sign).alias(signed_column))
                )
                neutral = neutralize_scoring_columns(
                    work,
                    [signed_column],
                    _variant_spec(variant),
                )
                valid = neutral.filter(
                    pl.col(signed_column).is_not_null()
                    & pl.col(signed_column).is_finite()
                    & pl.col(return_column).is_not_null()
                    & pl.col(return_column).is_finite()
                )
                ic_rows = calculate_rank_ic(
                    neutral,
                    signed_column,
                    return_column,
                    minimum=30,
                )
                values = (
                    ic_rows["ic"].drop_nulls().to_numpy()
                    if ic_rows.height
                    else []
                )
                nw = (
                    stats_v2.newey_west_t(values, lag=int(horizon))
                    if len(values) >= 3
                    else None
                )
                grid[factor_id][str(int(horizon))][variant] = {
                    "directional_ic": (
                        round(float(values.mean()), 6) if len(values) else None
                    ),
                    "nw_t": round(float(nw[0]), 6) if nw is not None else None,
                    "p_value": (
                        round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
                        if nw is not None
                        else None
                    ),
                    "n_dates": len(values),
                    "valid_rows": int(valid.height),
                }
    return grid


def build_conditional_factor(
    panel: pl.DataFrame,
    factor_id: str,
    regime_mask: pl.DataFrame,
    *,
    state: str = "weak",
    neutral_fill: str = "median",
) -> pl.DataFrame:
    """Keep a factor's cross-sectional information only in the selected regime."""
    if factor_id not in panel.columns:
        raise ValueError(f"conditional factor missing: {factor_id}")
    if neutral_fill != "median":
        raise ValueError("only median neutral fill is supported")
    base = panel.drop("is_weak") if "is_weak" in panel.columns else panel
    mask = regime_mask.select(
        "date",
        pl.col("is_weak").alias("_p9_is_weak"),
        pl.when(pl.col("is_weak")).then(state == "weak").otherwise(state != "weak").alias(
            "_selected_state"
        ),
    )
    joined = base.join(mask, on="date", how="left")
    median = pl.col(factor_id).median().over("date")
    return (
        joined.with_columns(
            pl.when(pl.col("_selected_state"))
            .then(pl.col(factor_id))
            .otherwise(median)
            .alias(f"{factor_id}_conditional")
        )
        .with_columns(pl.col("_p9_is_weak").alias("is_weak"))
        .drop("_selected_state", "_p9_is_weak")
    )


def factor_horizon_gate(variants: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Require two neutralized directions with the same sign and p<=0.10."""
    confirmations = []
    for variant in NEUTRAL_VARIANTS:
        row = variants.get(variant, {})
        ic = row.get("directional_ic")
        p_value = row.get("p_value")
        if (
            isinstance(ic, (int, float))
            and isinstance(p_value, (int, float))
            and float(ic) > 0.0
            and float(p_value) <= 0.10
        ):
            confirmations.append(variant)
    return {
        "pass": len(confirmations) >= 2,
        "confirmations": confirmations,
        "required_confirmations": 2,
        "reasons": [] if len(confirmations) >= 2 else ["fewer_than_two_neutralized_confirmations"],
    }


def build_conditional_composite(
    panel: pl.DataFrame,
    factor_ids: Sequence[str],
    regime_mask: pl.DataFrame,
    *,
    output_column: str,
) -> pl.DataFrame:
    """Average direction-adjusted conditional rank scores into one slot."""
    if not factor_ids:
        raise ValueError("conditional composite needs at least one factor")
    out = panel
    score_columns: list[str] = []
    for factor_id in factor_ids:
        spec = REGIME_HYPOTHESES.get(factor_id)
        if spec is None:
            raise ValueError(f"unsupported P9 financial factor: {factor_id}")
        out = build_conditional_factor(out, factor_id, regime_mask)
        score_column = f"_p9_score_{factor_id}"
        out = rank_score_frame(
            out,
            f"{factor_id}_conditional",
            str(spec["direction"]),
            output_column=score_column,
        )
        score_columns.append(score_column)
    expression = pl.lit(0.0)
    for score_column in score_columns:
        expression = expression + pl.col(score_column).fill_null(0.0)
    return out.with_columns(
        (expression / float(len(score_columns))).alias(output_column)
    )


def weight_neighbourhood(
    center: float,
    steps: Sequence[float] = (0.05, 0.10, 0.15, 0.20, 0.30),
) -> list[float]:
    weights = sorted({round(float(center), 12), *(round(float(item), 12) for item in steps)})
    if weights[0] <= 0.0 or weights[-1] >= 1.0:
        raise ValueError("weight neighbourhood must stay inside (0, 1)")
    return weights

def _complete_regime_history(data_dir: Path, end: Any) -> tuple[pl.DataFrame, dict[str, Any]]:
    history = load_regime_history(Path(data_dir))
    if history.is_empty():
        raise FileNotFoundError("regime history is unavailable")
    latest = max(history["date"].to_list())
    computed = pl.DataFrame()
    if latest < end:
        repo = KlineRepository(DataStore(Path(data_dir)))
        computed = run_regime_batch(repo, latest + timedelta(days=1), end)
        if not computed.is_empty():
            history = (
                pl.concat([history, computed], how="diagonal_relaxed")
                .unique(subset=["date"], keep="last")
                .sort("date")
            )
    if history["date"].max() < end:
        raise ValueError(f"regime history is incomplete through {end}")
    return history, {
        "stored_latest_before": str(latest),
        "computed_start": (
            str(computed["date"].min()) if not computed.is_empty() else None
        ),
        "computed_end": (
            str(computed["date"].max()) if not computed.is_empty() else None
        ),
        "computed_days": int(computed.height),
        "source": "stored_regime_history_plus_in_memory_recompute",
    }


def _factor_eligibility(
    grid: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]]
) -> dict[str, dict[str, Any]]:
    eligibility: dict[str, dict[str, Any]] = {}
    for factor_id in REGIME_HYPOTHESES:
        gate = factor_horizon_gate(grid[factor_id]["60"])
        eligibility[factor_id] = {
            "primary_horizon": 60,
            "gate": gate,
            "eligible": gate["pass"],
            "diagnostics": grid[factor_id],
        }
    return eligibility


def _eligible_variants(eligibility: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    return {
        name: factors
        for name, factors in CANDIDATE_VARIANTS.items()
        if all(eligibility.get(factor_id, {}).get("eligible") is True for factor_id in factors)
    }


def _run_baseline(
    service: Any,
    prepared_full: Any,
    prepared_recent: Any,
    prepared_weak: Any,
    scoring: Mapping[str, float],
    directions: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    return {
        "full_2x": p5._run_prepared(
            service,
            prepared_full,
            dict(scoring),
            dict(directions),
            start=p5.START,
            end=p5.END,
            cost_label="2x",
            label="p9_baseline_full_2x",
        ),
        "full_3x": p5._run_prepared(
            service,
            prepared_full,
            dict(scoring),
            dict(directions),
            start=p5.START,
            end=p5.END,
            cost_label="3x",
            label="p9_baseline_full_3x",
        ),
        "recent_2x": p5._run_prepared(
            service,
            prepared_recent,
            dict(scoring),
            dict(directions),
            start=p5.RECENT_START,
            end=p5.END,
            cost_label="2x",
            label="p9_baseline_recent_2x",
        ),
        "weak_2x": p5._run_prepared(
            service,
            prepared_weak,
            dict(scoring),
            dict(directions),
            start=p5.WEAK_START,
            end=p5.WEAK_END,
            cost_label="2x",
            label="p9_baseline_weak_2x",
        ),
    }


def _weak_gate(
    baseline: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_weak = float(baseline["weak_2x"]["stats"]["total_return"] or 0.0)
    candidate_weak = float(candidate["weak_2x"]["stats"]["total_return"] or 0.0)
    return {
        "pass": candidate_weak >= baseline_weak - 0.01,
        "baseline_weak_total_return": round(baseline_weak, 6),
        "candidate_weak_total_return": round(candidate_weak, 6),
        "tolerance": 0.01,
    }


def _weight_neighborhood(
    service: Any,
    prepared_full: Any,
    prepared_recent: Any,
    prepared_weak: Any,
    baseline: Mapping[str, Mapping[str, Any]],
    base_ids: Sequence[str],
    base_directions: Mapping[str, str],
    composite_id: str,
    *,
    center: float = WEIGHT_CENTER,
) -> dict[str, Any]:
    base_scale = float(len(base_ids))
    rows: dict[str, dict[str, Any]] = {}
    weights = weight_neighbourhood(center)
    for weight in weights:
        scoring = {
            factor_id: (1.0 - weight) / base_scale for factor_id in base_ids
        }
        scoring[composite_id] = weight
        directions = {**base_directions, composite_id: "high"}
        full = p5._run_prepared(
            service,
            prepared_full,
            scoring,
            directions,
            start=p5.START,
            end=p5.END,
            cost_label="2x",
            label=f"p9_weight_{weight:.6f}_full",
        )
        recent = p5._run_prepared(
            service,
            prepared_recent,
            scoring,
            directions,
            start=p5.RECENT_START,
            end=p5.END,
            cost_label="2x",
            label=f"p9_weight_{weight:.6f}_recent",
        )
        weak = p5._run_prepared(
            service,
            prepared_weak,
            scoring,
            directions,
            start=p5.WEAK_START,
            end=p5.WEAK_END,
            cost_label="2x",
            label=f"p9_weight_{weight:.6f}_weak",
        )
        baseline_full = baseline["full_2x"]["stats"]
        baseline_recent = baseline["recent_2x"]["stats"]
        checks = {
            "full_annual_not_worse_1pp": float(full["stats"]["annual_return"] or -1.0)
            >= float(baseline_full["annual_return"] or -1.0) - 0.01,
            "recent_total_not_worse_1pp": float(recent["stats"]["total_return"] or -1.0)
            >= float(baseline_recent["total_return"] or -1.0) - 0.01,
            "full_sharpe_not_worse_0_05": float(full["stats"]["sharpe"] or -1.0)
            >= float(baseline_full["sharpe"] or -1.0) - 0.05,
            "full_mdd_not_worse_2pp": float(full["stats"]["max_drawdown"] or -1.0)
            >= float(baseline_full["max_drawdown"] or -1.0) - 0.02,
            "weak_total_not_worse_1pp": float(weak["stats"]["total_return"] or -1.0)
            >= float(baseline["weak_2x"]["stats"]["total_return"] or -1.0) - 0.01,
        }
        rows[f"{weight:.12g}"] = {
            "weight": weight,
            "checks": checks,
            "pass": all(checks.values()),
            "full_2x": full,
            "recent_2x": recent,
            "weak_2x": weak,
        }
    ordered = sorted(rows.values(), key=lambda item: item["weight"])
    center_index = next(
        (index for index, row in enumerate(ordered) if math.isclose(row["weight"], center, abs_tol=1e-12)),
        None,
    )
    plateau = False
    if center_index is not None and 0 < center_index < len(ordered) - 1:
        plateau = all(
            ordered[index]["pass"]
            for index in (center_index - 1, center_index, center_index + 1)
        )
    return {
        "center": center,
        "weights": ordered,
        "center_pass": bool(center_index is not None and ordered[center_index]["pass"]),
        "three_neighbor_plateau": plateau,
        "gate": {"pass": plateau, "center": center, "neighbors": 3},
    }


def _candidate_reasons(record: Mapping[str, Any]) -> list[str]:
    reasons = list(record.get("statistical_gate", {}).get("reasons") or [])
    portfolio_gate = record.get("portfolio_gate")
    if isinstance(portfolio_gate, Mapping) and portfolio_gate.get("pass") is False:
        reasons.extend(
            f"portfolio_gate_failed:{key}"
            for key, passed in portfolio_gate.get("checks", {}).items()
            if not passed
        )
    weak_gate = record.get("weak_gate")
    if isinstance(weak_gate, Mapping) and weak_gate.get("pass") is False:
        reasons.append("weak_gate_failed")
    status = str(record.get("status") or "")
    if status in {"ablation_rejected", "rolling_rejected", "weight_plateau_rejected"}:
        reasons.append(status)
    return sorted(set(reasons)) or [status or "rejected"]


def _input_manifest(data_dir: Path, audit: Mapping[str, Any], p5_payload: Mapping[str, Any], current_version: Any) -> list[dict[str, Any]]:
    manifest = p8._input_manifest(data_dir, audit, p5_payload, current_version)
    manifest.append(
        p8._manifest_group(
            data_dir,
            "p8_financial_increment",
            [P8_RESULT, P8_RESULT.with_suffix(".md")],
        )
    )
    manifest.append(
        p8._manifest_group(
            data_dir,
            "regime_history",
            [Path(data_dir) / "regime_history" / "part.parquet"],
        )
    )
    return manifest


def run_p9_research(
    data_dir: Path = DATA,
    *,
    output_path: Path = DEFAULT_OUTPUT,
    stats_only: bool = False,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    audit = p8.load_verified_audit(data_dir)
    funding_candidates = [
        {
            "factor_id": factor_id,
            "direction": REGIME_HYPOTHESES[factor_id]["direction"],
        }
        for factor_id in REGIME_HYPOTHESES
    ]
    admission = p8.admit_financial_candidates(data_dir, funding_candidates)
    _sets, _head, current_version = p8._current_set(data_dir)
    _base_weights_raw, current_directions, current_ids = p5._current_inputs(
        current_version
    )
    if BASE_DROP_FACTOR not in current_ids:
        raise ValueError(f"P9 pre-registered drop factor is absent: {BASE_DROP_FACTOR}")
    base_ids = [factor_id for factor_id in current_ids if factor_id != BASE_DROP_FACTOR]
    base_directions = {factor_id: current_directions[factor_id] for factor_id in base_ids}
    base_scale = float(len(base_ids))
    base_weights = {factor_id: 1.0 / base_scale for factor_id in base_ids}
    from app.hs300.current import current_symbols

    symbols = current_symbols()
    panel, coverage = p8.load_financial_panel(
        data_dir, symbols, list(REGIME_HYPOTHESES)
    )
    panel = build_horizon_panel(panel)
    regime_history, regime_meta = _complete_regime_history(data_dir, p5.END)
    regime_mask = regime_mask_t_minus_one(
        panel.select("date").unique(), regime_history
    )
    grid = neutralized_ic_grid(panel)
    eligibility = _factor_eligibility(grid)
    eligible = _eligible_variants(eligibility)
    effective_regime = effective_regime_fingerprint(regime_mask)
    regime = {**regime_meta, "effective_state": effective_regime}

    if stats_only:
        preview = {
            "financial_pit_research_id": audit["research_id"],
            "base_set_content_sha256": current_version.content_sha256,
            "current_ids": current_ids,
            "base_ids_after_pre_registered_drop": base_ids,
            "regime": regime,
            "eligibility": eligibility,
            "eligible_variants": {
                name: list(factors) for name, factors in eligible.items()
            },
            "horizon_grid": grid,
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return preview

    p8_payload = p8._read_json(P8_RESULT)
    if p8_payload.get("research_id") is None:
        raise ValueError("P8 research ID is missing")
    p5_payload = p8._read_json(P5_RESULT)
    enriched = panel
    for variant, factor_ids in eligible.items():
        enriched = build_conditional_composite(
            enriched,
            factor_ids,
            regime_mask,
            output_column=f"p9_composite_{variant}",
        )
    candidates = [
        {
            "candidate_key": f"p9_{variant}",
            "economic_key": variant,
            "label": "+".join(REGIME_HYPOTHESES[factor_id]["label"] for factor_id in factors),
            "factor_ids": list(factors),
            "factor_id": f"p9_composite_{variant}",
            "direction": "high",
            "registered": True,
            "transform": "t1_weak_median_neutral",
            "set_role": "new",
            "replaces_factor_id": BASE_DROP_FACTOR,
            "dedupe_priority": 0,
        }
        for variant, factors in eligible.items()
    ]
    candidate_field_ids = [candidate["factor_id"] for candidate in candidates]
    all_field_ids = list(dict.fromkeys([*base_ids, *candidate_field_ids]))
    service = p8._service()
    prepared_full = p5._prepare_shared_window(
        service, symbols, enriched, all_field_ids, start=p5.START, end=p5.END
    )
    prepared_recent = p5._prepare_shared_window(
        service, symbols, enriched, all_field_ids, start=p5.RECENT_START, end=p5.END
    )
    prepared_weak = p5._prepare_shared_window(
        service, symbols, enriched, all_field_ids, start=p5.WEAK_START, end=p5.WEAK_END
    )
    baseline = _run_baseline(
        service,
        prepared_full,
        prepared_recent,
        prepared_weak,
        base_weights,
        base_directions,
    )
    base_gate = p5._gate_metrics(
        baseline["full_2x"], baseline["recent_2x"], baseline["full_3x"]
    )
    scored = enriched
    marginal: dict[str, dict[str, Any]] = {}
    candidate_records: list[dict[str, Any]] = []
    if candidates:
        scored, marginal = p5.score_candidates(
            enriched,
            candidates,
            base_weights,
            base_directions,
            return_column="forward_return_60d",
            horizon=60,
        )
        for candidate in candidates:
            result = dict(marginal.get(candidate["candidate_key"], {}))
            candidate_gate = p5.evaluate_statistical_gate(candidate, result)
            candidate_records.append(
                {
                    **candidate,
                    "coverage": {
                        factor_id: coverage.get(factor_id) for factor_id in candidate["factor_ids"]
                    },
                    "marginal_ic": result,
                    "statistical_gate": candidate_gate,
                    "status": "statistical_pass" if candidate_gate["pass"] else "statistical_rejected",
                }
            )
    statistical = [record for record in candidate_records if record["status"] == "statistical_pass"]
    screen: dict[str, dict[str, Any]] = {}
    if statistical:
        screen = p5._run_candidate_screen(
            service,
            statistical,
            marginal,
            base_ids,
            base_directions,
            BASE_DROP_FACTOR,
            prepared_full,
            prepared_recent,
            prepared_weak,
            baseline["full_2x"],
            baseline["recent_2x"],
            baseline["full_3x"],
            baseline["weak_2x"],
        )
        for record in candidate_records:
            screen_record = screen.get(record["candidate_key"])
            if screen_record is None:
                continue
            record["portfolio_result"] = screen_record["portfolio"]
            record["portfolio_gate"] = screen_record["portfolio_gate"]
            record["weak_gate"] = _weak_gate(
                baseline, screen_record["portfolio"]
            )
            record["status"] = (
                "portfolio_pass"
                if screen_record["portfolio_gate"]["pass"] and record["weak_gate"]["pass"]
                else "portfolio_rejected"
            )
    passing = [
        record for record in candidate_records if record["status"] == "portfolio_pass"
    ]
    passing.sort(
        key=lambda record: (
            record["marginal_ic"].get("directional_delta_mean") or -999.0,
            record["marginal_ic"].get("delta_p_value") or 1.0,
        ),
        reverse=True,
    )
    final_ids: list[str] | None = None
    final_directions: dict[str, str] = {}
    final_candidate: dict[str, Any] | None = None
    ablation: dict[str, Any] | None = None
    ablation_gate: dict[str, Any] | None = None
    rolling: dict[str, Any] | None = None
    weight_result: dict[str, Any] | None = None
    warning_5x: dict[str, Any] | None = None
    if passing:
        final_candidate = passing[0]
        final_ids = [*base_ids, final_candidate["factor_id"]]
        final_directions = {**base_directions, final_candidate["factor_id"]: "high"}
        final_scoring, selected_directions = p5._equal_inputs(final_ids, final_directions)
        ablation = p5._run_ablation(
            service,
            final_ids,
            selected_directions,
            {"full": prepared_full, "recent": prepared_recent, "weak": prepared_weak},
            {
                "full": (p5.START, p5.END),
                "recent": (p5.RECENT_START, p5.END),
                "weak": (p5.WEAK_START, p5.WEAK_END),
            },
        )
        ablation_gate = p5._ablation_candidate_gate(
            ablation, [final_candidate["factor_id"]]
        )
        if not ablation_gate["pass"]:
            final_candidate["status"] = "ablation_rejected"
            final_ids = None
            final_directions = {}
    if final_ids is not None and final_candidate is not None:
        final_scoring, selected_directions = p5._equal_inputs(final_ids, final_directions)
        rolling = p5._rolling_comparison(
            service,
            symbols,
            scored,
            all_field_ids,
            base_weights,
            base_directions,
            final_scoring,
            selected_directions,
        )
        if not rolling["gate"]["pass"]:
            final_candidate["status"] = "rolling_rejected"
            final_ids = None
            final_directions = {}
    if final_ids is not None and final_candidate is not None:
        weight_result = _weight_neighborhood(
            service,
            prepared_full,
            prepared_recent,
            prepared_weak,
            baseline,
            base_ids,
            base_directions,
            final_candidate["factor_id"],
        )
        if not weight_result["gate"]["pass"]:
            final_candidate["status"] = "weight_plateau_rejected"
            final_ids = None
            final_directions = {}
    if final_ids is not None:
        warning_5x = p8._five_x_warning(
            service, prepared_full, final_ids, final_directions
        )
    negative_knowledge: list[dict[str, Any]] = []
    for record in candidate_records:
        if final_ids is not None and record is final_candidate:
            continue
        negative_knowledge.append(
            {
                "candidate_key": record["candidate_key"],
                "factor_id": record["factor_id"],
                "status": record["status"],
                "reasons": _candidate_reasons(record),
            }
        )
    for factor_id, row in eligibility.items():
        if not row["eligible"]:
            negative_knowledge.append(
                {
                    "candidate_key": f"eligibility_{factor_id}",
                    "factor_id": factor_id,
                    "status": "horizon_rejected",
                    "reasons": row["gate"]["reasons"],
                }
            )
    if not candidates:
        status = "no_horizon_candidate"
    elif not statistical:
        status = "no_statistical_candidate"
    elif not passing:
        status = "no_portfolio_candidate"
    elif final_ids is None:
        status = "no_new_watch_version"
    else:
        status = "watch_candidate_requires_registration"
    input_manifest = _input_manifest(data_dir, audit, p5_payload, current_version)
    input_manifest_sha = sha256_json(input_manifest)
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha,
            "financial_pit_research_id": audit["research_id"],
            "p8_research_id": p8_payload["research_id"],
            "p5_research_id": p5_payload["research_id"],
            "p6_admission_catalog_snapshot_id": admission.get("catalog_snapshot_id"),
            "regime_meta": regime_meta,
            "effective_regime_sha256": effective_regime["sha256"],
            "panel_rows": enriched.height,
            "symbol_count": len(symbols),
            "coverage": coverage,
            "horizon_grid": grid,
        }
    )
    core = {
        "schema_version": 1,
        "method_version": METHOD_VERSION,
        "financial_pit_research_id": audit["research_id"],
        "p8_research_id": p8_payload["research_id"],
        "p5_research_id": p5_payload["research_id"],
        "catalog_snapshot_id": p5_payload.get("catalog_snapshot_id"),
        "p6_admission_catalog_snapshot_id": admission.get("catalog_snapshot_id"),
        "input_manifest_sha256": input_manifest_sha,
        "input_manifest": input_manifest,
        "data_fingerprint": data_fingerprint,
        "period": {
            "start": str(p5.START),
            "recent_start": str(p5.RECENT_START),
            "end": str(p5.END),
            "weak_start": str(p5.WEAK_START),
            "weak_end": str(p5.WEAK_END),
        },
        "universe": {
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
        },
        "base_set": {
            "set_id": current_version.set_id,
            "version": current_version.version,
            "content_sha256": current_version.content_sha256,
            "pre_registered_drop_factor": BASE_DROP_FACTOR,
            "factor_ids": base_ids,
        },
        "regime": regime,
        "factor_eligibility": eligibility,
        "eligible_variants": {name: list(factors) for name, factors in eligible.items()},
        "horizon_grid": grid,
        "candidate_count": len(candidate_records),
        "statistical_pass_count": len(statistical),
        "portfolio_pass_count": len(passing),
        "candidates": candidate_records,
        "portfolio": {
            "baseline": baseline,
            "baseline_gate": base_gate,
            "final_candidate": final_candidate["candidate_key"] if final_candidate is not None else None,
            "final_ids": final_ids,
            "final_directions": final_directions,
        },
        "ablation": ablation,
        "ablation_gate": ablation_gate,
        "rolling_year": rolling,
        "weight_neighborhood": weight_result,
        "cost_warning_5x": warning_5x,
        "decision": {
            "status": status,
            "active_version": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
            "factor_ids": final_ids,
            "new_watch_version": None,
            "requires_registration": status == "watch_candidate_requires_registration",
        },
        "negative_knowledge": sorted(
            negative_knowledge,
            key=lambda item: (item["candidate_key"], item["status"]),
        ),
        "caveats": [
            "Current HS300 membership is applied to history; survivorship bias remains.",
            "P9 uses a pre-registered synthetic conditional composite; it is not a registered factor and cannot enter a factor set until a separate registration/admission round passes.",
            "Regime alignment uses the previous trading session and fails closed on unresolved states.",
            "2x is the normal cost baseline, 3x is a hard stress gate, 5x is warning-only.",
            "No leverage, paper target, order, or production activation is produced.",
        ],
    }
    research_id = sha256_json(core)
    payload = {**core, "research_id": research_id, "generated_at": utc_now()}
    output_path = p8._normalize_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    published = p8._publish_json(output_path, payload)
    markdown_path = output_path.with_suffix(".md")
    p8._publish_markdown(markdown_path, render_markdown(published))
    ledger_record, _ledger_created = bind_research_evidence(
        data_dir,
        operation=OPERATION,
        evidence_paths=[output_path, markdown_path],
        research_id=published["research_id"],
        payload={
            "catalog_snapshot_id": published.get("catalog_snapshot_id"),
            "input_fingerprints": {
                "financial_pit_research_id": published["financial_pit_research_id"],
                "p8_research_id": published["p8_research_id"],
                "p5_research_id": published["p5_research_id"],
                "input_manifest_sha256": published["input_manifest_sha256"],
                "data_fingerprint": published["data_fingerprint"],
            },
            "metrics": {
                "candidate_count": published["candidate_count"],
                "statistical_pass_count": published["statistical_pass_count"],
                "portfolio_pass_count": published["portfolio_pass_count"],
                "final_factor_count": len(published["decision"]["factor_ids"] or []),
            },
            "verdict": published["decision"]["status"],
            "reason": "watch_only_no_activation",
            "negative_knowledge": json.dumps(
                published["negative_knowledge"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )
    print(
        json.dumps(
            {
                "research_id": published["research_id"],
                "output": str(output_path.relative_to(ROOT)),
                "eligible_variants": list(eligible),
                "candidate_count": published["candidate_count"],
                "statistical_pass_count": published["statistical_pass_count"],
                "portfolio_pass_count": published["portfolio_pass_count"],
                "decision": published["decision"]["status"],
                "active_version": None,
                "ledger_record_id": ledger_record.record_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return published


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# P9 财务期限分层与 Regime 条件化报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- P8 财务增量：`{payload['p8_research_id']}`",
        f"- T-1 Regime 指纹：`{payload['regime']['effective_state']['sha256']}`",
        f"- 决策：`{payload['decision']['status']}`",
        "- `active_version` 保持 null；不生成纸面目标或订单。",
        "",
        "## 期限资格",
        "",
        "| 因子 | 60日 Gate | 确认中性化口径 | 原因 |",
        "| --- | --- | --- | --- |",
    ]
    for factor_id, row in payload["factor_eligibility"].items():
        gate = row["gate"]
        lines.append(
            f"| `{factor_id}` | {'pass' if gate['pass'] else 'reject'} | "
            f"{', '.join(gate['confirmations']) or '-'} | {', '.join(gate['reasons']) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## 组合候选",
            "",
            "| 候选 | 组合 | 统计 | 2x | 3x | 近一年 | 弱市一年 | 状态 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for candidate in payload["candidates"]:
        portfolio = candidate.get("portfolio_result") or {}
        full = (portfolio.get("full_2x") or {}).get("stats", {})
        full_3x = (portfolio.get("full_3x") or {}).get("stats", {})
        recent = (portfolio.get("recent_2x") or {}).get("stats", {})
        weak = (portfolio.get("weak_2x") or {}).get("stats", {})
        lines.append(
            f"| `{candidate['candidate_key']}` | {'+'.join(candidate['factor_ids'])} | "
            f"{'pass' if candidate['statistical_gate']['pass'] else 'reject'} | "
            f"{_fmt(full.get('total_return'))} | {_fmt(full_3x.get('total_return'))} | "
            f"{_fmt(recent.get('total_return'))} | {_fmt(weak.get('total_return'))} | "
            f"{candidate['status']} |"
        )
    lines.extend(
        [
            "",
            "## 稳健性",
            "",
            f"- 期限资格通过：{sum(row['eligible'] for row in payload['factor_eligibility'].values())}/{len(payload['factor_eligibility'])}。",
            f"- 统计通过：{payload['statistical_pass_count']}；组合通过：{payload['portfolio_pass_count']}。",
            f"- 权重邻域平台：{(payload.get('weight_neighborhood') or {}).get('gate', {}).get('pass', False)}。",
            f"- 5x 成本只作 warning：{payload.get('cost_warning_5x') is not None}。",
            "",
            "## 负知识",
            "",
        ]
    )
    if payload["negative_knowledge"]:
        for item in payload["negative_knowledge"]:
            lines.append(f"- `{item['candidate_key']}`：{', '.join(item['reasons'])}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- T-1 regime 对齐；未解析的前一交易日状态会 fail-closed。",
            "- 财务因子来自 P7 PIT 证据，组合结果只是研究准入，不是收益保证。",
            "- 合成条件复合腿尚未注册为正式因子，因此即使通过也只会标记 `requires_registration`。",
            "- HS300 当前成分回填历史存在幸存者偏差。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_p9_research(DATA, output_path=args.output, stats_only=args.stats_only)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
