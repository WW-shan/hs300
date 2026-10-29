#!/usr/bin/env python3
"""Post-hoc paired statistical audit of the frozen R275 ablation family."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
R275_RESULT = EVIDENCE / "r275_single_gate_ablation_v3d.json"
R275_PREREG = EVIDENCE / "r275_preregistration_v3d.json"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
R275_CACHE = DATA / "research/r275_cache_v3d"
R266_CACHE = DATA / "research/r266_cache"
OUT = EVIDENCE / "r277_r275_active_return_statistics_v2.json"
RUNLOG = EVIDENCE / "r277_r275_active_return_statistics_runlog.json"
START, END = "2021-06-01", "2026-10-16"
CANDIDATES = {
    "erp": "ai_hs300_macro_gate_quality_r275_remove_erp",
    "credit": "ai_hs300_macro_gate_quality_r275_remove_credit",
    "term": "ai_hs300_macro_gate_quality_r275_remove_term",
}
BOOTSTRAPS = 20000
BLOCK_LENGTHS = (20, 40, 60)
HAC_LAGS = (5, 20, 35, 60)
SEED = 277_2026


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).select(
        pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64)
    ).sort("date")


def returns(frame: pl.DataFrame) -> np.ndarray:
    values = frame["value"].to_numpy()
    return values[1:] / values[:-1] - 1.0


def hac_mean(values: np.ndarray, lag: int) -> dict[str, float]:
    n = len(values)
    mean = float(values.mean())
    centered = values - mean
    long_run_variance = float(centered @ centered) / n
    for offset in range(1, min(lag, n - 1) + 1):
        covariance = float(centered[offset:] @ centered[:-offset]) / n
        weight = 1.0 - offset / (lag + 1.0)
        long_run_variance += 2.0 * weight * covariance
    standard_error = math.sqrt(max(long_run_variance, 0.0) / n)
    statistic = mean / standard_error if standard_error else math.inf
    p_one_sided = 0.5 * math.erfc(statistic / math.sqrt(2.0))
    p_two_sided = math.erfc(abs(statistic) / math.sqrt(2.0))
    return {
        "mean_daily_active_return": mean,
        "hac_lag_days": lag,
        "standard_error": standard_error,
        "z_statistic_normal_approx": statistic,
        "p_one_sided_normal_approx": p_one_sided,
        "p_two_sided_normal_approx": p_two_sided,
    }


def circular_block_bootstrap(
    values: np.ndarray, block_length: int, rng: np.random.Generator
) -> dict[str, float]:
    n = len(values)
    full_blocks, remainder = divmod(n, block_length)
    doubled = np.concatenate([values, values[:block_length]])
    prefix = np.concatenate([[0.0], np.cumsum(doubled)])
    full_sums = prefix[block_length : block_length + n] - prefix[:n]
    block_starts = rng.integers(0, n, size=(BOOTSTRAPS, full_blocks))
    samples = full_sums[block_starts].sum(axis=1)
    if remainder:
        partial_sums = prefix[remainder : remainder + n] - prefix[:n]
        partial_starts = rng.integers(0, n, size=BOOTSTRAPS)
        samples += partial_sums[partial_starts]
    means = samples / n
    centered = values - values.mean()
    null_prefix = np.concatenate([[0.0], np.cumsum(np.concatenate([centered, centered[:block_length]]))])
    null_full_sums = null_prefix[block_length : block_length + n] - null_prefix[:n]
    null_samples = null_full_sums[block_starts].sum(axis=1)
    if remainder:
        null_partial_sums = null_prefix[remainder : remainder + n] - null_prefix[:n]
        null_samples += null_partial_sums[partial_starts]
    null_means = null_samples / n
    observed = float(values.mean())
    return {
        "block_length_days": block_length,
        "replications": BOOTSTRAPS,
        "annualized_active_mean_ci_95": [
            float(np.quantile(means, 0.025) * 252.0),
            float(np.quantile(means, 0.975) * 252.0),
        ],
        "annualized_active_mean_point": observed * 252.0,
        "p_one_sided_centered_block_bootstrap": float(
            (np.count_nonzero(null_means >= observed) + 1) / (BOOTSTRAPS + 1)
        ),
    }


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (name, p_value) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * p_value))
        adjusted[name] = running
    return adjusted


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R277 evidence exists; refusing overwrite")
    prereg = json.loads(R275_PREREG.read_text(encoding="utf-8"))
    result = json.loads(R275_RESULT.read_text(encoding="utf-8"))
    result_sha = sha(R275_RESULT)
    result_proof = result.get("sha256") or result.get("result_sha256")
    sidecar = R275_RESULT.with_suffix(".json.sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != result_sha:
        raise RuntimeError("R275 result checksum sidecar mismatch")
    if result_proof is not None and result_proof != result_sha:
        raise RuntimeError("R275 result self-checksum mismatch")
    for path, expected in (
        (R275_PREREG, result["preregistration_sha256"]),
        (R275_RESULT, None),
        (R266_RESULT, prereg["execution"]["r266_evidence_sha256"]),
    ):
        if expected is not None and sha(path) != expected:
            raise RuntimeError(f"evidence hash mismatch: {path.name}")
    if result.get("classification") != "three_candidate_single_gate_family_exploratory_not_independent_confirmation":
        raise RuntimeError("unexpected R275 family classification")

    control_path = R266_CACHE / f"ai_hs300_macro_gate_quality_3x_{START}_{END}.parquet"
    control = curve(control_path)
    control_days = control["date"].to_list()
    control_returns = returns(control)
    rng = np.random.default_rng(SEED)
    candidate_results: dict[str, dict] = {}
    for name, strategy_id in CANDIDATES.items():
        candidate_path = R275_CACHE / f"{strategy_id}_3x_{START}_{END}.parquet"
        candidate = curve(candidate_path)
        if candidate["date"].to_list() != control_days:
            raise RuntimeError(f"date parity failed for candidate {name}")
        candidate_returns = returns(candidate)
        active = candidate_returns - control_returns
        hac = {str(lag): hac_mean(active, lag) for lag in HAC_LAGS}
        bootstrap = {
            str(length): circular_block_bootstrap(active, length, rng)
            for length in BLOCK_LENGTHS
        }
        candidate_results[name] = {
            "strategy_id": strategy_id,
            "curve_sha256": sha(candidate_path),
            "curve_days": len(control_days),
            "daily_observations": len(active),
            "candidate_total_return": float(candidate["value"][-1] / candidate["value"][0] - 1.0),
            "control_total_return": float(control["value"][-1] / control["value"][0] - 1.0),
            "relative_terminal_wealth_vs_control": float(candidate["value"][-1] / control["value"][-1] - 1.0),
            "mean_daily_active_return": float(active.mean()),
            "annualized_active_arithmetic_mean_diagnostic": float(active.mean() * 252.0),
            "hac_tests": hac,
            "circular_block_bootstrap": bootstrap,
        }
    holm_by_lag = {}
    for lag in HAC_LAGS:
        raw = {name: item["hac_tests"][str(lag)]["p_one_sided_normal_approx"] for name, item in candidate_results.items()}
        holm_by_lag[str(lag)] = holm_adjust(raw)
    for name, item in candidate_results.items():
        item["holm_adjusted_p_one_sided_by_hac_lag"] = {
            str(lag): holm_by_lag[str(lag)][name] for lag in HAC_LAGS
        }
    payload = {
        "round": "R277",
        "classification": "post_hoc_paired_active_return_statistics_not_preregistered_alpha_confirmation",
        "question": "Does the R275 candidate-family return lift over R266 remain statistically distinguishable from zero after serial-dependence and three-candidate selection adjustments?",
        "method": {
            "comparison": "paired daily simple returns: candidate minus R266 control, both with 3x frozen execution costs",
            "window": [START, END],
            "hac_lags_trading_days": list(HAC_LAGS),
            "bootstrap": "circular moving-block bootstrap on paired active returns; centered null for one-sided p; 20,000 replications per candidate and block length",
            "block_lengths_trading_days": list(BLOCK_LENGTHS),
            "selection_adjustment": "Holm family-wise adjustment over the three preregistered R275 candidates, separately for each HAC lag",
            "seed": SEED,
        },
        "inputs": {
            "r275_result_sha256": sha(R275_RESULT),
            "r275_preregistration_sha256": sha(R275_PREREG),
            "r266_result_sha256": sha(R266_RESULT),
            "r266_curve_sha256": sha(control_path),
            "r275_candidate_curve_sha256": {name: item["curve_sha256"] for name, item in candidate_results.items()},
        },
        "candidate_family": candidate_results,
        "holm_adjusted_p_values": holm_by_lag,
        "interpretation": {
            "term_hac_35d_unadjusted_one_sided_p": candidate_results["term"]["hac_tests"]["35"]["p_one_sided_normal_approx"],
            "term_hac_35d_holm_adjusted_one_sided_p": candidate_results["term"]["holm_adjusted_p_one_sided_by_hac_lag"]["35"],
            "term_60d_block_bootstrap_annualized_active_mean_ci_95": candidate_results["term"]["circular_block_bootstrap"]["60"]["annualized_active_mean_ci_95"],
            "term_60d_block_bootstrap_one_sided_p": candidate_results["term"]["circular_block_bootstrap"]["60"]["p_one_sided_centered_block_bootstrap"],
            "conclusion": "The point estimate of paired arithmetic active return is positive, but confidence intervals include zero and tests do not reject no positive mean; this is weak/non-significant evidence, not statistical confirmation.",
        },
        "limitations": [
            "This is a post-hoc statistical audit of already observed outcomes, not an independent holdout or preregistered test.",
            "HAC normal-approximation p-values and block-bootstrap intervals depend on return-stationarity assumptions; multiple lag/block choices are sensitivity checks, not independent replications.",
            "R266 is a related strategy control rather than a broad investable benchmark; active return does not by itself identify causal macro alpha.",
            "Macro point-in-time availability is unproven, and published-data/revision timing may bias the historical comparison.",
        ],
        "decision": "diagnostic_only; statistical significance cannot cure the exploratory selection, macro PIT uncertainty, or reused historical validation window",
        "production_status": "pause_production",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = sha(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({
        "evidence": OUT.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "term_relative_terminal_wealth_vs_control": candidate_results["term"]["relative_terminal_wealth_vs_control"],
        "term_candidate_vs_control_total_return_gap_pp": (candidate_results["term"]["candidate_total_return"] - candidate_results["term"]["control_total_return"]) * 100.0,
        "term_mean_daily_active_return": candidate_results["term"]["mean_daily_active_return"],
        "term_annualized_arithmetic_active_mean_diagnostic": candidate_results["term"]["annualized_active_arithmetic_mean_diagnostic"],
        "term_hac": candidate_results["term"]["hac_tests"],
        "term_holm_adjusted": candidate_results["term"]["holm_adjusted_p_one_sided_by_hac_lag"],
        "term_block_bootstrap": candidate_results["term"]["circular_block_bootstrap"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
