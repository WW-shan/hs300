#!/usr/bin/env python3
"""P2 parameter scan for built-in price, liquidity, and sentiment windows."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import research_alt_factor_parameters as p1_scan

from app.factor_research.builtin_parameter_evaluators import BUILTIN_EVALUATORS
from app.factor_research.catalog import load_latest_catalog
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
    load_parameter_registry,
    validate_parameter_registry,
)
from app.factor_research.signal_scan import calculate_rank_ic
from app.services.industry_map import load_industry_l1
from app.strategy.scoring import neutralize_scoring_columns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p2_builtin_parameter_mc_2026-11-11_v2.json"
)
P1_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p1_parameter_mc_2026-11-11.json"
)
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
METHOD_VERSION = "builtin_parameter_mc_v2_scoped"
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
INITIAL_BOOTSTRAP_RUNS = 1000
FINAL_BOOTSTRAP_RUNS = 5000
PRIMARY_BLOCK_SIZE = 20
FINAL_BLOCK_SIZES = (10, 20, 40)
OPERATION = "factor_parameter_mc_published"
SUPERSEDES_RESEARCH_ID = (
    "sha256:614306d5679c37acfc37ed62a310c7ed5a5472efa998fb263be80ea4a3720de3"
)

INDUSTRY_COLUMN = "industry_l1"
SIZE_COLUMN = "log_float_mv"


def load_p2_families(data_dir: Path) -> list[ParameterFamily]:
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
    by_id = {row.factor_id: row for row in classifications}
    builtin_unclassified = [
        factor.factor_id
        for factor in catalog.factors
        if factor.source.type == "builtin"
        and by_id[factor.factor_id].status == "unclassified"
    ]
    if builtin_unclassified:
        raise ValueError(f"builtin factors remain unclassified: {builtin_unclassified}")
    return [
        family
        for family in registry.families
        if family.status == "tunable" and family.evaluator in BUILTIN_EVALUATORS
    ]


def variant_id(family_id: str, parameter: int) -> str:
    return f"{family_id}__p_{parameter}"


def p2_research_registry_payload(families: list[ParameterFamily]) -> dict[str, Any]:
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


def comparison_basis(family_result: dict[str, Any]) -> tuple[int, str]:
    recommended = family_result.get("recommended_parameter")
    if recommended is not None:
        return int(recommended), "recommended"
    return int(family_result["current_parameter"]), "current_fallback"


def correlation_summary(
    frame: pl.DataFrame,
    left_column: str,
    right_column: str,
    *,
    minimum_symbols: int = MINIMUM_CROSS_SECTION,
) -> dict[str, Any]:
    missing = {left_column, right_column, "symbol", "date"} - set(frame.columns)
    if missing:
        raise ValueError(f"correlation frame missing columns: {sorted(missing)}")
    clean = frame.filter(
        pl.col(left_column).is_not_null()
        & pl.col(left_column).is_finite()
        & pl.col(right_column).is_not_null()
        & pl.col(right_column).is_finite()
    )
    daily = (
        clean.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(
                pl.col(left_column).rank(method="average"),
                pl.col(right_column).rank(method="average"),
            ).alias("corr"),
        )
        .filter(
            (pl.col("n") >= minimum_symbols)
            & pl.col("corr").is_not_null()
            & pl.col("corr").is_finite()
        )
        .sort("date")
    )
    if daily.is_empty():
        raise ValueError("no common dates meet the minimum correlation sample")
    values = daily["corr"].to_numpy()
    absolute = abs(values)
    return {
        "n_dates": int(values.size),
        "mean_symbols": round(float(daily["n"].mean()), 3),
        "mean": round(float(values.mean()), 8),
        "mean_abs": round(float(absolute.mean()), 8),
        "max_abs": round(float(absolute.max()), 8),
    }


def _enriched_panel(start: date, end: date, symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .select([
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_rate",
            "consecutive_limit_ups",
        ])
        .collect()
        .sort(["symbol", "date"])
    )


def _market_panel() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def _neutralized_ic(joined: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    neutral_input = joined.select([
        "date",
        "symbol",
        "forward_return_5d",
        "factor_value",
        INDUSTRY_COLUMN,
        SIZE_COLUMN,
    ])
    neutralized = neutralize_scoring_columns(
        neutral_input,
        ["factor_value"],
        {
            "industry": INDUSTRY_COLUMN if "industry" in names else None,
            "styles": [SIZE_COLUMN] if "size" in names else [],
            "exclude": [],
            "shrink": 1.0,
        },
    )
    return calculate_rank_ic(
        neutralized,
        "factor_value",
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
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
        "median_cross_section": round(float(widths.median()), 3) if widths.len() else 0.0,
    }


def _standard_factor_frame(
    raw: pl.DataFrame,
    output_column: str,
) -> pl.DataFrame:
    return raw.select([
        "symbol",
        "date",
        pl.col(output_column).alias("factor_value"),
    ])


def _p1_comparison_frames(
    families: list[ParameterFamily],
    p1_payload: dict[str, Any],
    prices: pl.DataFrame,
    market: pl.DataFrame,
) -> tuple[dict[str, pl.DataFrame], dict[str, dict[str, Any]]]:
    frames: dict[str, pl.DataFrame] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for family in families:
        result = p1_payload["families"].get(family.family_id)
        if result is None:
            raise ValueError(f"P1 result is missing family {family.family_id}")
        parameter, basis = comparison_basis(result)
        evaluator = p1_scan.EVALUATORS.get(family.evaluator)
        if evaluator is None:
            raise ValueError(f"P1 evaluator is missing: {family.evaluator}")
        raw, output_column = evaluator(prices, market, parameter)
        frames[family.family_id] = _standard_factor_frame(raw, output_column)
        metadata[family.family_id] = {
            "parameter": parameter,
            "basis": basis,
        }
    return frames, metadata


def _correlation_matrix(
    p2_families: list[ParameterFamily],
    family_results: dict[str, Any],
    p1_frames: dict[str, pl.DataFrame],
    p1_metadata: dict[str, dict[str, Any]],
    prices: pl.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in p2_families:
        result = family_results[family.family_id]
        parameter, basis = comparison_basis(result)
        raw, output_column, _formula = BUILTIN_EVALUATORS[family.evaluator](
            prices, parameter
        )
        p2_frame = _standard_factor_frame(raw, output_column)
        for p1_family_id, p1_frame in p1_frames.items():
            joined = p2_frame.select([
                "symbol",
                "date",
                pl.col("factor_value").alias("p2_value"),
            ]).join(
                p1_frame.select([
                    "symbol",
                    "date",
                    pl.col("factor_value").alias("p1_value"),
                ]),
                on=["symbol", "date"],
                how="inner",
            )
            summary = correlation_summary(joined, "p2_value", "p1_value")
            rows.append({
                "p2_family_id": family.family_id,
                "p2_parameter": parameter,
                "p2_variant_id": variant_id(family.family_id, parameter),
                "p2_basis": basis,
                "p1_family_id": p1_family_id,
                "p1_parameter": p1_metadata[p1_family_id]["parameter"],
                "p1_basis": p1_metadata[p1_family_id]["basis"],
                **summary,
            })
    return rows


def _render_markdown(payload: dict[str, Any]) -> str:
    def fmt(value: Any) -> str:
        return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"

    lines = [
        "# P2 内置窗口参数族蒙特卡洛报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 方法版本：`{payload['method_version']}`",
        f"- 样本：`{payload['period']['start']}` 至 `{payload['period']['end']}`",
        f"- 参数族：{payload['family_count']}；参数点：{payload['trial_count']}",
        "- 所有结论仅为 watch 候选研究，不产生 active 版本、订单或纸面目标。",
        "",
        "## 家族结论",
        "",
        "| 参数族 | 当前 | 推荐 | 结论 | 当前全样本 IC | 当前检验 IC | 决赛主 CI |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for family_id, result in payload["families"].items():
        current = result["current_trial"]
        recommended = result.get("recommended_trial")
        final_ci = (
            final_ci_low(recommended) if recommended is not None else None
        )
        lines.append(
            f"| `{family_id}` | {result['current_parameter']} | "
            f"{result['recommended_parameter'] if result['recommended_parameter'] is not None else '-'} | "
            f"{result['recommendation_status']} | "
            f"{fmt(current['full']['directional_ic'])} | "
            f"{fmt(current['test']['directional_ic'])} | {fmt(final_ci)} |"
        )

    lines.extend([
        "",
        "## 与 P1 的跨族平均绝对相关",
        "",
        "| P2 参数族 | " + " | ".join(payload["p1_family_ids"]) + " |",
        "| --- | " + " | ".join("---:" for _ in payload["p1_family_ids"]) + " |",
    ])
    matrix: dict[tuple[str, str], float] = {
        (row["p2_family_id"], row["p1_family_id"]): row["mean_abs"]
        for row in payload["correlation_matrix"]
    }
    for family_id in payload["families"]:
        cells = [
            fmt(matrix[(family_id, p1_family_id)])
            for p1_family_id in payload["p1_family_ids"]
        ]
        lines.append(f"| `{family_id}` | " + " | ".join(cells) + " |")

    lines.extend([
        "",
        "## 判定口径",
        "",
        "- 当前参数与既有 enriched 公式逐值对齐后才允许进入参数族。",
        "- 初筛：稳健平台、全样本/训练/检验方向 IC 为正、行业+规模中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 CI 下限为正。",
        "- 决赛：5000 次 bootstrap，主 block=20；block 10/40 作为敏感性，不直接参与收益优化。",
        "- 推荐排序：`min(train_ic, test_ic)`、决赛主 CI 下限、与当前参数距离。",
        "- 多窗口复合定义显式 fixed；非 scale-free 的原始 ATR 显式 blocked。",
        "",
    ])
    return "\n".join(lines)


def _resolve_output(value: str) -> Path:
    path = Path(value)
    allowed = (DATA / "factor_research" / "research" / "parameters").resolve()
    candidates = (path,) if path.is_absolute() else (Path.cwd() / path, ROOT / path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(allowed):
            return resolved
    raise ValueError("parameter output must stay inside data/factor_research/research/parameters")


def _publish_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"parameter result already exists: {path}")
        return existing
    atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, payload: dict[str, Any]) -> None:
    text = _render_markdown(payload)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"parameter markdown already exists: {path}")
        return
    atomic_write_text(path, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--end", default=str(DEFAULT_END))
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT.relative_to(ROOT)),
        help="Repository-relative JSON output path.",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end <= start or end <= TRAIN_END:
        raise ValueError("end date must be after the training window")
    output = _resolve_output(args.output)
    markdown_path = output.with_suffix(".md")

    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    registry = load_parameter_registry(DATA)
    families = load_p2_families(DATA)
    p1_payload = json.loads(P1_RESULT.read_text(encoding="utf-8"))
    research_id = sha256_json({
        "method_version": METHOD_VERSION,
        "catalog_snapshot_id": catalog.snapshot_id,
        "parameter_registry": p2_research_registry_payload(families),
        "p1_research_id": p1_payload["research_id"],
        "start": str(start),
        "end": str(end),
        "horizon": HORIZON,
        "initial_bootstrap_runs": INITIAL_BOOTSTRAP_RUNS,
        "final_bootstrap_runs": FINAL_BOOTSTRAP_RUNS,
        "final_block_sizes": FINAL_BLOCK_SIZES,
    })

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    prices = _enriched_panel(date(2019, 1, 1), end, symbols)
    market = _market_panel()
    prices = prices.with_columns(
        (
            pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0
        ).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias(SIZE_COLUMN),
    )
    industry = (
        load_industry_l1(DATA)
        .select(["symbol", INDUSTRY_COLUMN])
        .unique(subset=["symbol"], keep="last")
    )
    prices = prices.join(industry, on="symbol", how="left")
    data_fingerprint = sha256_json({
        "symbols": sorted(symbols),
        "start": str(start),
        "end": str(end),
        "price_rows": prices.height,
        "market_rows": market.height,
    })

    family_results: dict[str, Any] = {}
    all_trials: list[dict[str, Any]] = []
    for family in families:
        evaluator = BUILTIN_EVALUATORS[family.evaluator]
        sign = -1 if family.direction == "low" else 1
        current_factor_id = current_family_factor_id(family)
        trials: list[dict[str, Any]] = []
        for parameter in family.parameter_grid:
            raw, output_column, formula_text = evaluator(prices, parameter)
            if family.formula_template is not None:
                expected_formula = family.formula_template.format(parameter=parameter)
                if formula_text != expected_formula:
                    raise ValueError(
                        f"formula template drift for {family.family_id}: "
                        f"{formula_text!r} != {expected_formula!r}"
                    )
            factor_frame = _standard_factor_frame(raw, output_column)
            joined = (
                prices.select([
                    "symbol",
                    "date",
                    "forward_return_5d",
                    INDUSTRY_COLUMN,
                    SIZE_COLUMN,
                ])
                .join(factor_frame, on=["symbol", "date"], how="inner")
                .filter(pl.col("forward_return_5d").is_not_null())
            )
            raw_ic = calculate_rank_ic(
                joined,
                "factor_value",
                "forward_return_5d",
                minimum=MINIMUM_CROSS_SECTION,
            )
            industry_ic = _neutralized_ic(joined, ["industry"])
            size_ic = _neutralized_ic(joined, ["size"])
            combined_ic = _neutralized_ic(joined, ["industry", "size"])
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
                "definition_fingerprint": sha256_json({
                    "method_version": METHOD_VERSION,
                    "catalog_snapshot_id": catalog.snapshot_id,
                    "data_fingerprint": data_fingerprint,
                    "family_id": family.family_id,
                    "parameter": parameter,
                    "formula_text": formula_text,
                }),
                "coverage": _coverage(joined),
                "full": stats_for_period(
                    raw_ic,
                    start,
                    end,
                    sign,
                    horizon=HORIZON,
                    include_distribution=True,
                ),
                "train": stats_for_period(
                    raw_ic, start, TRAIN_END, sign, horizon=HORIZON
                ),
                "test": stats_for_period(
                    raw_ic, TEST_START, end, sign, horizon=HORIZON
                ),
                "recent": stats_for_period(
                    raw_ic, RECENT_START, end, sign, horizon=HORIZON
                ),
                "bootstrap_full": bootstrap_ic(
                    raw_ic,
                    start,
                    end,
                    sign,
                    block_size=PRIMARY_BLOCK_SIZE,
                    n_bootstrap=INITIAL_BOOTSTRAP_RUNS,
                    seed=seed,
                ),
                "industry_neutral": {
                    "full": stats_for_period(
                        industry_ic, start, end, sign, horizon=HORIZON
                    ),
                    "train": stats_for_period(
                        industry_ic, start, TRAIN_END, sign, horizon=HORIZON
                    ),
                    "test": stats_for_period(
                        industry_ic, TEST_START, end, sign, horizon=HORIZON
                    ),
                    "recent": stats_for_period(
                        industry_ic, RECENT_START, end, sign, horizon=HORIZON
                    ),
                },
                "size_neutral": {
                    "full": stats_for_period(
                        size_ic, start, end, sign, horizon=HORIZON
                    ),
                    "train": stats_for_period(
                        size_ic, start, TRAIN_END, sign, horizon=HORIZON
                    ),
                    "test": stats_for_period(
                        size_ic, TEST_START, end, sign, horizon=HORIZON
                    ),
                    "recent": stats_for_period(
                        size_ic, RECENT_START, end, sign, horizon=HORIZON
                    ),
                },
                "industry_size_neutral": {
                    "full": stats_for_period(
                        combined_ic, start, end, sign, horizon=HORIZON
                    ),
                    "train": stats_for_period(
                        combined_ic, start, TRAIN_END, sign, horizon=HORIZON
                    ),
                    "test": stats_for_period(
                        combined_ic, TEST_START, end, sign, horizon=HORIZON
                    ),
                    "recent": stats_for_period(
                        combined_ic, RECENT_START, end, sign, horizon=HORIZON
                    ),
                },
                "q_value": None,
                "final_bootstrap": {},
            }
            trials.append(trial)

        annotate_plateau(trials)
        assign_family_q_values(trials)
        initial_finalists = select_robust_candidates(trials)
        finalists: list[dict[str, Any]] = []
        for trial in initial_finalists:
            final_bootstrap: dict[str, Any] = {}
            raw, output_column, _formula = evaluator(prices, int(trial["parameter"]))
            factor_frame = _standard_factor_frame(raw, output_column)
            joined = (
                prices.select([
                    "symbol",
                    "date",
                    "forward_return_5d",
                    INDUSTRY_COLUMN,
                    SIZE_COLUMN,
                ])
                .join(factor_frame, on=["symbol", "date"], how="inner")
                .filter(pl.col("forward_return_5d").is_not_null())
            )
            raw_ic = calculate_rank_ic(
                joined,
                "factor_value",
                "forward_return_5d",
                minimum=MINIMUM_CROSS_SECTION,
            )
            for block_size in FINAL_BLOCK_SIZES:
                final_bootstrap[str(block_size)] = bootstrap_ic(
                    raw_ic,
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
            finalists.append(trial)

        recommended = choose_recommended_candidate(
            finalists,
            current_parameter=family.current_parameter,
        )
        current = next(trial for trial in trials if trial["is_current"])
        sensitivity_positive = None
        if recommended is not None:
            sensitivity_positive = all(
                recommended["final_bootstrap"][str(block)]["ci_low"] > 0
                for block in FINAL_BLOCK_SIZES
            )
        family_results[family.family_id] = {
            "label": family.label,
            "evaluator": family.evaluator,
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
            "initial_failure_counts": initial_failure_counts(trials),
            "rejection_reasons": (
                []
                if recommended is not None
                else [
                    "final_5000_block20_ci_not_positive"
                    if initial_finalists
                    else "no_parameter_passed_initial_robustness_gate"
                ]
            ),
            "current_trial": current,
            "recommended_trial": recommended,
            "trials": trials,
        }
        all_trials.extend(trials)

    p1_families = [
        family
        for family in registry.families
        if family.status == "tunable" and family.evaluator in p1_scan.EVALUATORS
    ]
    p1_frames, p1_metadata = _p1_comparison_frames(
        p1_families, p1_payload, prices, market
    )
    correlation_rows = _correlation_matrix(
        families, family_results, p1_frames, p1_metadata, prices
    )
    p1_family_ids = sorted(p1_frames)


    payload = {
        "schema_version": 1,
        "research_id": research_id,
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "catalog_snapshot_id": catalog.snapshot_id,
        "parameter_registry_sha256": sha256_file(
            DATA / "factor_research" / "parameters" / "families.json"
        ),
        "data_fingerprint": data_fingerprint,
        "p1_research_id": p1_payload["research_id"],
        "p1_family_ids": p1_family_ids,
        "research_scope_version": 2,
        "supersedes_research_id": SUPERSEDES_RESEARCH_ID,
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
            "formula_parity": "current window matched to canonical enriched formula",
            "train_test": "train through 2024-12-31; test from 2025-01-01",
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
            "neutralization": ["industry", "size", "industry+size"],
            "correlation": "daily cross-sectional Spearman of selected variant values",
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
        "active_version": None,
        "families": family_results,
        "correlation_matrix": correlation_rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _publish_json(output, payload)
    _publish_markdown(markdown_path, payload)

    ledger = ResearchLedger(DATA)
    ledger.verify()
    existing = [
        record
        for record in ledger.records()
        if record.operation == OPERATION
        and record.input_fingerprints.get("research_id") == payload["research_id"]
    ]
    if not existing:
        ledger.append(
            OPERATION,
            payload={
                "catalog_snapshot_id": payload["catalog_snapshot_id"],
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "p1_research_id": payload["p1_research_id"],
                    "parameter_registry_sha256": payload["parameter_registry_sha256"],
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
                "supersedes_record_id": next(
                    (
                        record.record_id
                        for record in ledger.records()
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
                },
                "verdict": "watch_candidates_only_no_active_version",
            },
        )

    print(json.dumps({
        "research_id": payload["research_id"],
        "output": str(output.relative_to(ROOT)),
        "markdown": str(markdown_path.relative_to(ROOT)),
        "family_count": payload["family_count"],
        "trial_count": payload["trial_count"],
        "finalist_count": payload["finalist_count"],
        "recommended_parameter_count": payload["recommended_parameter_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
