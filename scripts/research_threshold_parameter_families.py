#!/usr/bin/env python3
"""P4 threshold and event-holding parameter research."""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import research_extended_parameter_families as p3_scan

from app.factor_research.catalog import load_latest_catalog
from app.factor_research.extended_parameter_data import load_existing_ext_factors
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.parameter_mc import (
    FDR_THRESHOLD,
    annotate_plateau,
    assign_family_q_values,
    final_ci_low,
    initial_eligibility,
    initial_failure_counts,
    select_robust_candidates,
)
from app.factor_research.parameters import (
    classify_factor_parameters,
    load_parameter_registry,
    validate_parameter_registry,
)
from app.factor_research.threshold_evaluators import (
    EVENT_HOLDING_EVALUATORS,
    threshold_indicator,
)
from app.factor_research.threshold_parameters import (
    ThresholdFamily,
    load_threshold_registry,
    validate_threshold_registry,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p4_threshold_parameter_mc_2026-11-11.json"
)
P3_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p3_extended_parameter_mc_2026-11-11_v3.json"
)
METHOD_VERSION = "threshold_parameter_mc_v1"
OPERATION = "factor_parameter_mc_published"


def _format_parameter(parameter: float) -> str:
    return str(int(parameter)) if float(parameter).is_integer() else f"{parameter:g}"


def _variant_id(family_id: str, parameter: float) -> str:
    token = _format_parameter(parameter).replace("-", "m").replace(".", "_")
    return f"{family_id}__p_{token}"


def _bootstrap_seed(
    research_id: str,
    family_id: str,
    parameter: float,
    block_size: int,
    n_bootstrap: int,
) -> int:
    token = sha256_json(
        {
            "research_id": research_id,
            "family_id": family_id,
            "parameter": parameter,
            "block_size": block_size,
            "n_bootstrap": n_bootstrap,
        }
    ).removeprefix("sha256:")
    return int(token[:8], 16)


def _choose_threshold_candidate(
    trials: list[dict[str, Any]],
    *,
    current_parameter: float,
) -> dict[str, Any] | None:
    eligible = [
        trial
        for trial in trials
        if initial_eligibility(trial)
        and isinstance(trial.get("train", {}).get("directional_ic"), (int, float))
        and trial["train"]["directional_ic"] > 0.0
        and isinstance(trial.get("test", {}).get("directional_ic"), (int, float))
        and trial["test"]["directional_ic"] > 0.0
        and isinstance(final_ci_low(trial), (int, float))
        and final_ci_low(trial) > 0.0
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda trial: (
            min(
                float(trial["train"]["directional_ic"]),
                float(trial["test"]["directional_ic"]),
            ),
            float(final_ci_low(trial)),
            -abs(float(trial["parameter"]) - current_parameter),
        ),
    )


def load_p4_families(data_dir: Path) -> list[ThresholdFamily]:
    data_dir = Path(data_dir)
    catalog = load_latest_catalog(data_dir)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    if catalog.source_completeness != "complete":
        raise RuntimeError(
            f"factor catalog source completeness is {catalog.source_completeness}"
        )
    window_registry = load_parameter_registry(data_dir)
    window_errors = validate_parameter_registry(
        window_registry,
        {factor.factor_id for factor in catalog.factors},
    )
    if window_errors:
        raise ValueError("; ".join(window_errors))
    registry = load_threshold_registry(data_dir)
    errors = validate_threshold_registry(registry, catalog, window_registry)
    if errors:
        raise ValueError("; ".join(errors))
    classifications = classify_factor_parameters(catalog.factors, window_registry)
    unclassified = [
        row.factor_id for row in classifications if row.status == "unclassified"
    ]
    if unclassified:
        raise ValueError(f"factors remain unclassified: {unclassified}")
    if len(registry.families) != 23:
        raise ValueError(f"expected 23 P4 families, got {len(registry.families)}")
    return registry.families


def p4_registry_payload(
    families: Sequence[ThresholdFamily],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "families": [
            {
                "family_id": family.family_id,
                "label": family.label,
                "kind": family.kind,
                "source_factor_id": family.source_factor_id,
                "event_key": family.event_key,
                "mode": family.mode,
                "direction": family.direction,
                "parameter_name": family.parameter_name,
                "parameter_grid": family.parameter_grid,
                "current_parameter": family.current_parameter,
                "neutralization": family.neutralization,
                "data_class": family.data_class,
                "minimum_history": family.minimum_history,
                "formula_template": family.formula_template,
            }
            for family in families
        ],
    }


def _pass_coverage(factor_frame: pl.DataFrame) -> dict[str, Any]:
    valid = factor_frame.filter(
        pl.col("factor_value").is_not_null() & pl.col("factor_value").is_finite()
    )
    selected = valid.filter(pl.col("factor_value") > 0.0)
    return {
        "valid_rows": valid.height,
        "selected_rows": selected.height,
        "pass_ratio": round(selected.height / valid.height, 6) if valid.height else 0.0,
    }


def _scan_signal_family(
    family: ThresholdFamily,
    context: dict[str, Any],
    research_id: str,
    start: date,
    end: date,
    signal_builder: Callable[[float], tuple[pl.DataFrame, str]],
    *,
    role: str,
) -> dict[str, Any]:
    prices = context["prices"]
    minimum_cross_section = int(
        context.get("minimum_cross_section", p3_scan.MINIMUM_CROSS_SECTION)
    )
    sign = -1 if family.direction == "low" else 1
    gate_ic_by_trial: dict[tuple[str, float], pl.DataFrame] = {}
    trials: list[dict[str, Any]] = []

    joined_base = prices.select(
        [
            "symbol",
            "date",
            "forward_return_5d",
            p3_scan.INDUSTRY_COLUMN,
            p3_scan.SIZE_COLUMN,
        ]
    )
    for parameter in family.parameter_grid:
        raw, formula_text = signal_builder(parameter)
        factor_frame = raw.select(
            ["symbol", "date", pl.col("factor_value").cast(pl.Float64)]
        )
        joined = (
            joined_base.join(factor_frame, on=["symbol", "date"], how="inner")
            .filter(pl.col("forward_return_5d").is_not_null())
            .sort(["date", "symbol"])
        )
        raw_ic = p3_scan.calculate_rank_ic(
            joined,
            "factor_value",
            "forward_return_5d",
            minimum=minimum_cross_section,
        )
        industry_ic = p3_scan._neutralized_ic(
            joined,
            ["industry"],
            minimum_cross_section=minimum_cross_section,
        )
        size_ic = p3_scan._neutralized_ic(
            joined,
            ["size"],
            minimum_cross_section=minimum_cross_section,
        )
        combined_ic = p3_scan._neutralized_ic(
            joined,
            ["industry", "size"],
            minimum_cross_section=minimum_cross_section,
        )
        gate_ic = p3_scan._gate_ic(
            raw_ic,
            industry_ic,
            size_ic,
            combined_ic,
            family.neutralization,
        )
        gate_ic_by_trial[(family.family_id, parameter)] = gate_ic
        gate_block = p3_scan._ic_block(gate_ic, start, end, sign)
        seed = _bootstrap_seed(
            research_id,
            family.family_id,
            parameter,
            p3_scan.PRIMARY_BLOCK_SIZE,
            p3_scan.INITIAL_BOOTSTRAP_RUNS,
        )
        trial = {
            "variant_id": _variant_id(family.family_id, parameter),
            "family_id": family.family_id,
            "kind": family.kind,
            "source_factor_id": family.source_factor_id,
            "event_key": family.event_key,
            "threshold_mode": family.mode,
            "parameter": parameter,
            "is_current": math.isclose(
                parameter,
                family.current_parameter,
                rel_tol=0.0,
                abs_tol=1e-12,
            ),
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
            "coverage": p3_scan._coverage(joined),
            "pass_coverage": _pass_coverage(factor_frame),
            **gate_block,
            "gate_neutralization": list(family.neutralization),
            "gate_neutral": gate_block,
            "industry_neutral": p3_scan._ic_block(
                industry_ic, start, end, sign
            ),
            "size_neutral": p3_scan._ic_block(size_ic, start, end, sign),
            "industry_size_neutral": p3_scan._ic_block(
                combined_ic, start, end, sign
            ),
            "raw_bootstrap_full": p3_scan._bootstrap_ic_or_empty(
                raw_ic,
                start,
                end,
                sign,
                block_size=p3_scan.PRIMARY_BLOCK_SIZE,
                n_bootstrap=p3_scan.INITIAL_BOOTSTRAP_RUNS,
                seed=seed,
            ),
            "bootstrap_full": p3_scan._bootstrap_ic_or_empty(
                gate_ic,
                start,
                end,
                sign,
                block_size=p3_scan.PRIMARY_BLOCK_SIZE,
                n_bootstrap=p3_scan.INITIAL_BOOTSTRAP_RUNS,
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
        gate_ic = gate_ic_by_trial[(family.family_id, float(trial["parameter"]))]
        for block_size in p3_scan.FINAL_BLOCK_SIZES:
            final_bootstrap[str(block_size)] = p3_scan._bootstrap_ic_or_empty(
                gate_ic,
                start,
                end,
                sign,
                block_size=block_size,
                n_bootstrap=p3_scan.FINAL_BOOTSTRAP_RUNS,
                seed=_bootstrap_seed(
                    research_id,
                    family.family_id,
                    float(trial["parameter"]),
                    block_size,
                    p3_scan.FINAL_BOOTSTRAP_RUNS,
                ),
            )
        trial["final_bootstrap"] = final_bootstrap

    recommended = _choose_threshold_candidate(
        initial_finalists,
        current_parameter=family.current_parameter,
    )
    current = next(trial for trial in trials if trial["is_current"])
    sensitivity_positive = None
    if recommended is not None:
        sensitivity_positive = all(
            recommended["final_bootstrap"][str(block)]["ci_low"] is not None
            and recommended["final_bootstrap"][str(block)]["ci_low"] > 0.0
            for block in p3_scan.FINAL_BLOCK_SIZES
        )
    return {
        "label": family.label,
        "kind": family.kind,
        "role": role,
        "stock_alpha_eligible": recommended is not None,
        "use": "stock alpha research candidate only",
        "source_factor_id": family.source_factor_id,
        "event_key": family.event_key,
        "threshold_mode": family.mode,
        "direction": family.direction,
        "parameter_name": family.parameter_name,
        "parameter_grid": family.parameter_grid,
        "current_parameter": family.current_parameter,
        "gate_neutralization": family.neutralization,
        "recommended_parameter": (
            float(recommended["parameter"]) if recommended is not None else None
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
        "rejection_reasons": p3_scan._recommendation_reasons(
            recommended,
            len(initial_finalists),
        ),
        "current_trial": current,
        "recommended_trial": recommended,
        "trials": trials,
    }


def scan_threshold_family(
    family: ThresholdFamily,
    context: dict[str, Any],
    factor_panel: pl.DataFrame,
    research_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    if family.kind != "threshold" or family.source_factor_id is None:
        raise ValueError(f"not a threshold family: {family.family_id}")
    if family.mode is None:
        raise ValueError(f"threshold family has no mode: {family.family_id}")
    if family.source_factor_id not in factor_panel.columns:
        raise ValueError(f"factor panel missing {family.source_factor_id}")
    source = (
        factor_panel.select(
            [
                "symbol",
                "date",
                pl.col(family.source_factor_id)
                .cast(pl.Float64)
                .alias("factor_value"),
            ]
        )
        .filter(pl.col("factor_value").is_not_null())
        .sort(["symbol", "date"])
    )

    def build(parameter: float) -> tuple[pl.DataFrame, str]:
        indicator = threshold_indicator(
            source,
            parameter,
            family.mode,
            family.direction,
        )
        formula = (
            family.formula_template.format(parameter=_format_parameter(parameter))
            if family.formula_template is not None
            else f"{family.source_factor_id} threshold {parameter}"
        )
        return indicator, formula

    return _scan_signal_family(
        family,
        context,
        research_id,
        start,
        end,
        build,
        role="threshold_research",
    )


def scan_event_holding_family(
    family: ThresholdFamily,
    context: dict[str, Any],
    research_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    if family.kind != "event_holding" or family.event_key is None:
        raise ValueError(f"not an event holding family: {family.family_id}")
    evaluators = context.get("event_evaluators", EVENT_HOLDING_EVALUATORS)
    evaluator = evaluators.get(family.event_key)
    if evaluator is None:
        raise ValueError(f"event holding evaluator is missing: {family.event_key}")

    def build(parameter: float) -> tuple[pl.DataFrame, str]:
        raw, output_column, formula = evaluator(context, parameter)
        expected = (
            family.formula_template.format(parameter=int(parameter))
            if family.formula_template is not None
            else formula
        )
        if formula != expected:
            raise ValueError(
                f"formula template drift for {family.family_id}: "
                f"{formula!r} != {expected!r}"
            )
        return (
            raw.select(
                [
                    "symbol",
                    "date",
                    pl.col(output_column).cast(pl.Float64).alias("factor_value"),
                ]
            ),
            formula,
        )

    return _scan_signal_family(
        family,
        context,
        research_id,
        start,
        end,
        build,
        role="event_holding_research",
    )


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P4 阈值与事件后持有期参数报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 方法版本：`{payload['method_version']}`",
        f"- 样本：`{payload['period']['start']}` 至 `{payload['period']['end']}`",
        f"- 参数族：{payload['family_count']}；参数点：{payload['trial_count']}",
        "- 阈值和持有期只生成股票研究候选，不修改注册因子，不自动进入 watch 或 active。",
        "",
        "## 家族结论",
        "",
        "| 参数族 | 类型 | 口径 | 源因子 | 当前 | 推荐 | 结论 | 当前全样本 | 当前检验 | q | 决赛主 CI |",
        "| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for family_id, result in payload["families"].items():
        current = result["current_trial"]
        recommended = result.get("recommended_trial")
        lines.append(
            f"| `{family_id}` | {result['kind']} | {result.get('threshold_mode') or 'step'} | "
            f"`{result['source_factor_id']}` | {result['current_parameter']} | "
            f"{result['recommended_parameter'] if result['recommended_parameter'] is not None else '-'} | "
            f"{result['recommendation_status']} | "
            f"{_fmt(current['full']['directional_ic'])} | "
            f"{_fmt(current['test']['directional_ic'])} | "
            f"{_fmt(current.get('q_value'))} | "
            f"{_fmt(final_ci_low(recommended) if recommended else None)} |"
        )

    lines.extend(
        [
            "",
            "## 参数点统计",
            "",
            "| 参数族 | 参数 | 通过比例 | 全样本 IC | 训练 IC | 检验 IC | 近一年 IC | 初筛 CI 下限 | q | 平台 | 决赛主 CI |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for family_id, result in payload["families"].items():
        for trial in result["trials"]:
            lines.append(
                f"| `{family_id}` | {_format_parameter(trial['parameter'])} | "
                f"{_fmt(trial['pass_coverage']['pass_ratio'])} | "
                f"{_fmt(trial['full']['directional_ic'])} | "
                f"{_fmt(trial['train']['directional_ic'])} | "
                f"{_fmt(trial['test']['directional_ic'])} | "
                f"{_fmt(trial['recent']['directional_ic'])} | "
                f"{_fmt(trial['bootstrap_full']['ci_low'])} | "
                f"{_fmt(trial.get('q_value'))} | {trial['plateau_status']} | "
                f"{_fmt(final_ci_low(trial))} |"
            )
    lines.extend(
        [
            "",
            "## 判定口径",
            "",
            "- 绝对阈值和分位阈值分别成族；分位阈值按每个交易日横截面计算。",
            "- 初筛要求稳健平台、全样本/训练/检验方向 IC 为正、行业+规模中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 bootstrap `ci_low > 0`。",
            "- 决赛使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。",
            "- 推荐排序先最大化 `min(train_ic, test_ic)`，再最大化决赛 CI，最后选择更接近参考参数者。",
            "- 单点或极窄平台必须拒绝。",
            "",
        ]
    )
    return "\n".join(lines)


def _input_manifest(data_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = p3_scan._input_manifest(data_dir)
    registry_path = data_dir / "factor_research" / "parameters" / "thresholds.json"
    registry_entry = {
        "label": "threshold_registry",
        "path": str(registry_path.relative_to(data_dir)),
        "bytes": registry_path.stat().st_size,
        "sha256": sha256_file(registry_path),
    }
    return [*manifest, registry_entry], registry_entry


def _p3_research_id() -> str:
    if not P3_RESULT.exists():
        raise FileNotFoundError(P3_RESULT)
    return json.loads(P3_RESULT.read_text(encoding="utf-8"))["research_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=str(p3_scan.DEFAULT_START))
    parser.add_argument("--end", default=str(p3_scan.DEFAULT_END))
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT.relative_to(ROOT)),
        help="Repository-relative JSON output path.",
    )
    parser.add_argument("--registry-only", action="store_true")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end <= start or end <= p3_scan.TRAIN_END:
        raise ValueError("end date must be after the training window")

    families = load_p4_families(DATA)
    if args.registry_only:
        print(
            json.dumps(
                {
                    "family_count": len(families),
                    "threshold_family_count": sum(
                        family.kind == "threshold" for family in families
                    ),
                    "event_holding_family_count": sum(
                        family.kind == "event_holding" for family in families
                    ),
                    "trial_count": sum(
                        len(family.parameter_grid) for family in families
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    output = p3_scan._resolve_output(args.output)
    markdown_path = output.with_suffix(".md")
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    p1_research_id, p2_research_id = p3_scan._research_ids()
    p3_research_id = _p3_research_id()
    manifest, registry_entry = _input_manifest(DATA)
    input_manifest_sha256 = sha256_json(manifest)

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    context = p3_scan.build_research_context(DATA, symbols, end)
    threshold_sources = sorted(
        {
            family.source_factor_id
            for family in families
            if family.kind == "threshold" and family.source_factor_id is not None
        }
    )
    factor_panel = load_existing_ext_factors(
        DATA,
        {
            factor_id: factor_id.removeprefix("ext_alt_hs300_")
            for factor_id in threshold_sources
        },
    )
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha256,
            "symbols": sorted(symbols),
            "start": str(start),
            "end": str(end),
            "price_rows": context["prices"].height,
            "threshold_factor_rows": factor_panel.height,
        }
    )
    context["data_fingerprint"] = data_fingerprint
    research_id = sha256_json(
        {
            "method_version": METHOD_VERSION,
            "catalog_snapshot_id": catalog.snapshot_id,
            "threshold_registry": p4_registry_payload(families),
            "threshold_registry_sha256": registry_entry["sha256"],
            "p1_research_id": p1_research_id,
            "p2_research_id": p2_research_id,
            "p3_research_id": p3_research_id,
            "input_manifest_sha256": input_manifest_sha256,
            "start": str(start),
            "end": str(end),
            "horizon": p3_scan.HORIZON,
            "minimum_cross_section": p3_scan.MINIMUM_CROSS_SECTION,
            "initial_bootstrap_runs": p3_scan.INITIAL_BOOTSTRAP_RUNS,
            "final_bootstrap_runs": p3_scan.FINAL_BOOTSTRAP_RUNS,
            "final_block_sizes": p3_scan.FINAL_BLOCK_SIZES,
            "fdr_threshold": FDR_THRESHOLD,
        }
    )

    family_results: dict[str, Any] = {}
    all_trials: list[dict[str, Any]] = []
    for family in families:
        if family.kind == "threshold":
            result = scan_threshold_family(
                family,
                context,
                factor_panel,
                research_id,
                start,
                end,
            )
        else:
            result = scan_event_holding_family(
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
        "threshold_registry_sha256": registry_entry["sha256"],
        "p1_research_id": p1_research_id,
        "p2_research_id": p2_research_id,
        "p3_research_id": p3_research_id,
        "data_fingerprint": data_fingerprint,
        "input_manifest_sha256": input_manifest_sha256,
        "input_manifest": manifest,
        "period": {
            "start": str(start),
            "end": str(end),
            "train_end": str(p3_scan.TRAIN_END),
            "test_start": str(p3_scan.TEST_START),
            "recent_start": str(p3_scan.RECENT_START),
        },
        "universe": {
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
        },
        "return_horizon_days": p3_scan.HORIZON,
        "minimum_cross_section": p3_scan.MINIMUM_CROSS_SECTION,
        "method": {
            "parameter_grid": "pre-registered threshold and holding-period grids only",
            "absolute_threshold": "factor >= absolute threshold",
            "quantile_threshold": "factor >= daily cross-sectional quantile",
            "event_holding": "announcement-date plus one trading day for a constant holding window",
            "initial_monte_carlo": {
                "runs": p3_scan.INITIAL_BOOTSTRAP_RUNS,
                "block_size": p3_scan.PRIMARY_BLOCK_SIZE,
            },
            "final_monte_carlo": {
                "runs": p3_scan.FINAL_BOOTSTRAP_RUNS,
                "block_sizes": list(p3_scan.FINAL_BLOCK_SIZES),
                "primary_block_size": p3_scan.PRIMARY_BLOCK_SIZE,
            },
            "multiple_testing": "BH-FDR within each absolute, quantile or holding family",
            "fdr_threshold": FDR_THRESHOLD,
        },
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
        "threshold_family_count": sum(
            result["kind"] == "threshold" for result in family_results.values()
        ),
        "event_holding_family_count": sum(
            result["kind"] == "event_holding"
            for result in family_results.values()
        ),
        "active_version": None,
        "families": family_results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = p3_scan._publish_json(output, payload)
    p3_scan._publish_markdown(markdown_path, render_markdown(payload))

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
                    "p3_research_id": payload["p3_research_id"],
                    "threshold_registry_sha256": payload[
                        "threshold_registry_sha256"
                    ],
                    "input_manifest_sha256": payload["input_manifest_sha256"],
                    "data_fingerprint": payload["data_fingerprint"],
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
                "metrics": {
                    "family_count": payload["family_count"],
                    "trial_count": payload["trial_count"],
                    "finalist_count": payload["finalist_count"],
                    "recommended_parameter_count": payload[
                        "recommended_parameter_count"
                    ],
                    "rejected_family_count": payload["rejected_family_count"],
                    "threshold_family_count": payload["threshold_family_count"],
                    "event_holding_family_count": payload[
                        "event_holding_family_count"
                    ],
                },
                "verdict": "threshold_candidates_only_no_active_version",
            },
        )

    print(
        json.dumps(
            {
                "research_id": payload["research_id"],
                "output": str(output.relative_to(ROOT)),
                "markdown": str(markdown_path.relative_to(ROOT)),
                "family_count": payload["family_count"],
                "trial_count": payload["trial_count"],
                "finalist_count": payload["finalist_count"],
                "recommended_parameter_count": payload[
                    "recommended_parameter_count"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
