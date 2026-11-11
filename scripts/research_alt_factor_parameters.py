#!/usr/bin/env python3
"""Registry-driven parameter scan with holdout and two-stage block bootstrap."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import build_alt_factor_data as builder
import polars as pl

from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.parameter_mc import (
    annotate_plateau,
    choose_recommended_candidate,
    select_robust_candidates,
)
from app.factor_research.parameter_mc import (
    assign_family_q_values as _yearly_q_values,
)
from app.factor_research.parameter_mc import (
    bootstrap_ic as _bootstrap,
)
from app.factor_research.parameter_mc import (
    bootstrap_seed as _bootstrap_seed,
)
from app.factor_research.parameter_mc import (
    final_ci_low as _final_ci_low,
)
from app.factor_research.parameter_mc import (
    initial_failure_counts as _initial_failure_counts,
)
from app.factor_research.parameter_mc import (
    stats_for_period as _stats_for_period,
)
from app.factor_research.parameters import (
    ParameterFamily,
    current_family_factor_id,
    load_parameter_registry,
    validate_parameter_registry,
)
from app.factor_research.signal_scan import (
    calculate_rank_ic,
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
    / "p1_parameter_mc_2026-11-11.json"
)
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
METHOD_VERSION = "parameter_mc_v2_two_stage"
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
INITIAL_BOOTSTRAP_RUNS = 1000
FINAL_BOOTSTRAP_RUNS = 5000
PRIMARY_BLOCK_SIZE = 20
FINAL_BLOCK_SIZES = (10, 20, 40)
FDR_THRESHOLD = 0.10
OPERATION = "factor_parameter_mc_published"

INDUSTRY_COLUMN = "industry_l1"
SIZE_COLUMN = "log_float_mv"


def _compute_idio(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    return (
        builder._residual_volatility_frame(prices, market, window_size=value),
        "idio_vol_60d_low",
    )


def _compute_cs_spread(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    del market
    return builder._corwin_schultz_spread_frame(prices, window_size=value), "cs_spread_60d"


def _compute_vol_of_vol(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    del market
    return (
        builder._vol_of_vol_frame(prices, stability_window=value),
        "vol_of_vol_60d_low",
    )


def _compute_down_beta(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    return (
        builder._market_down_beta_frame(prices, market, window_size=value),
        "market_down_beta_60d_low",
    )


def _compute_price_delay(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    return (
        builder._price_delay_frame(prices, market, window_size=value),
        "price_delay_60d",
    )


def _compute_trend_efficiency(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    del market
    return (
        builder._trend_efficiency_frame(prices, window_size=value),
        "trend_efficiency_60d",
    )


def _compute_risk_adjusted(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    del market
    return (
        builder._risk_adjusted_momentum_frame(prices, window_size=value),
        "risk_adjusted_momentum_60d",
    )


def _compute_beta_asymmetry(
    prices: pl.DataFrame,
    market: pl.DataFrame,
    value: int,
) -> tuple[pl.DataFrame, str]:
    return (
        builder._beta_asymmetry_frame(prices, market, window_size=value),
        "beta_asymmetry_120d",
    )


Evaluator = Callable[[pl.DataFrame, pl.DataFrame, int], tuple[pl.DataFrame, str]]

EVALUATORS: dict[str, Evaluator] = {
    "residual_volatility": _compute_idio,
    "corwin_schultz_spread": _compute_cs_spread,
    "vol_of_vol": _compute_vol_of_vol,
    "market_down_beta": _compute_down_beta,
    "price_delay": _compute_price_delay,
    "trend_efficiency": _compute_trend_efficiency,
    "risk_adjusted_momentum": _compute_risk_adjusted,
    "beta_asymmetry": _compute_beta_asymmetry,
}


def load_p1_families(data_dir: Path) -> list[ParameterFamily]:
    catalog = load_latest_catalog(Path(data_dir))
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    registry = load_parameter_registry(Path(data_dir))
    errors = validate_parameter_registry(
        registry,
        {factor.factor_id for factor in catalog.factors},
    )
    if errors:
        raise ValueError("; ".join(errors))
    return [
        family
        for family in registry.families
        if family.status == "tunable" and family.evaluator in EVALUATORS
    ]


def p1_research_registry_payload(families: list[ParameterFamily]) -> dict[str, Any]:
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
                "evidence_refs": family.evidence_refs,
            }
            for family in families
        ],
        "unclassified_factor_ids": [],
        "blocked_factor_ids": [],
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


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) > 0.0


def _neutralization_spec(names: list[str]) -> dict[str, Any]:
    return {
        "industry": INDUSTRY_COLUMN if "industry" in names else None,
        "styles": [SIZE_COLUMN] if "size" in names else [],
        "exclude": [],
        "shrink": 1.0,
    }


def _neutralized_ic(
    joined: pl.DataFrame,
    names: list[str],
) -> pl.DataFrame:
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
        _neutralization_spec(names),
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


def _render_markdown(payload: dict[str, Any]) -> str:
    def fmt(value: Any) -> str:
        return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"

    lines = [
        "# P1 价格风险参数族蒙特卡洛报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 方法版本：`{payload['method_version']}`",
        f"- 样本：`{payload['period']['start']}` 至 `{payload['period']['end']}`",
        f"- 训练/检验：`{payload['period']['train_end']}` / `{payload['period']['test_start']}`",
        f"- 参数点：{payload['trial_count']}；决赛参数点：{payload['finalist_count']}",
        "- 所有结论仅为 watch 候选研究，不产生 active 版本、订单或纸面目标。",
        "",
        "## 家族结论",
        "",
        "| 参数族 | 当前 | 推荐 | 结论 | 当前全样本 IC | 当前检验 IC | 决赛主 CI 下限 | block 10/20/40 |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for family_id, result in payload["families"].items():
        current = result["current_trial"]
        recommended = result.get("recommended_trial")
        final = recommended.get("final_bootstrap", {}) if recommended else {}
        primary = final.get(str(PRIMARY_BLOCK_SIZE), {})
        sensitivity = "/".join(
            fmt(final.get(str(block), {}).get("ci_low"))
            for block in FINAL_BLOCK_SIZES
        )
        lines.append(
            f"| `{family_id}` | {result['current_parameter']} | "
            f"{result['recommended_parameter'] if result['recommended_parameter'] is not None else '-'} | "
            f"{result['recommendation_status']} | "
            f"{fmt(current['full']['directional_ic'])} | "
            f"{fmt(current['test']['directional_ic'])} | "
            f"{fmt(primary.get('ci_low'))} | {sensitivity} |"
        )

    lines.extend([
        "",
        "## 参数点统计",
        "",
        "| 参数族 | 参数 | 全样本 IC | 训练 IC | 检验 IC | 近一年 IC | 行业+规模 IC | q | 初筛 CI 下限 | 平台 | 决赛 CI 下限 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ])
    for family_id, result in payload["families"].items():
        for trial in result["trials"]:
            lines.append(
                f"| `{family_id}` | {trial['parameter']} | "
                f"{fmt(trial['full']['directional_ic'])} | "
                f"{fmt(trial['train']['directional_ic'])} | "
                f"{fmt(trial['test']['directional_ic'])} | "
                f"{fmt(trial['recent']['directional_ic'])} | "
                f"{fmt(trial['industry_size_neutral']['full']['directional_ic'])} | "
                f"{fmt(trial['q_value'])} | "
                f"{fmt(trial['bootstrap_full']['ci_low'])} | "
                f"{trial['plateau_status']} | "
                f"{fmt(_final_ci_low(trial))} |"
            )
    lines.extend([
        "",
        "## 判定口径",
        "",
        "- 初筛：`robust_plateau`、全样本/训练/检验方向 IC 为正、行业+规模中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次主 block bootstrap `ci_low > 0`。",
        "- 决赛：入围点使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。",
        "- 推荐排序：先最大化 `min(train_ic, test_ic)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。",
        "- 单一参数点不能构成稳健平台；未通过者保留为负知识。",
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
    if catalog.source_completeness != "complete":
        raise RuntimeError(
            f"factor catalog source completeness is {catalog.source_completeness}"
        )
    families = load_p1_families(DATA)
    research_id = sha256_json({
        "method_version": METHOD_VERSION,
        "catalog_snapshot_id": catalog.snapshot_id,
        "parameter_registry": p1_research_registry_payload(families),
        "start": str(start),
        "end": str(end),
        "horizon": HORIZON,
        "initial_bootstrap_runs": INITIAL_BOOTSTRAP_RUNS,
        "final_bootstrap_runs": FINAL_BOOTSTRAP_RUNS,
        "final_block_sizes": FINAL_BLOCK_SIZES,
    })

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    load_start = date(2019, 1, 1)
    prices = _enriched_panel(load_start, end, symbols)
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
    raw_ic_by_trial: dict[tuple[str, int], pl.DataFrame] = {}
    for family in families:
        evaluator = EVALUATORS[family.evaluator]
        sign = -1 if family.direction == "low" else 1
        current_factor_id = current_family_factor_id(family)
        trials: list[dict[str, Any]] = []
        for parameter in family.parameter_grid:
            raw, output_column = evaluator(prices, market, parameter)
            factor_frame = raw.select([
                "symbol",
                "date",
                pl.col(output_column).alias("factor_value"),
            ])
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
            raw_ic_by_trial[(family.family_id, parameter)] = raw_ic
            industry_ic = _neutralized_ic(joined, ["industry"])
            size_ic = _neutralized_ic(joined, ["size"])
            combined_ic = _neutralized_ic(joined, ["industry", "size"])
            seed = _bootstrap_seed(
                research_id,
                family.family_id,
                parameter,
                PRIMARY_BLOCK_SIZE,
                INITIAL_BOOTSTRAP_RUNS,
            )
            variant_id = f"{family.family_id}__p_{parameter}"
            trial = {
                "variant_id": variant_id,
                "family_id": family.family_id,
                "source_factor_id": current_factor_id,
                "parameter": parameter,
                "is_current": parameter == family.current_parameter,
                "definition_fingerprint": sha256_json({
                    "method_version": METHOD_VERSION,
                    "catalog_snapshot_id": catalog.snapshot_id,
                    "data_fingerprint": data_fingerprint,
                    "family_id": family.family_id,
                    "parameter": parameter,
                }),
                "coverage": _coverage(joined),
                "full": _stats_for_period(
                    raw_ic,
                    start,
                    end,
                    sign,
                    include_distribution=True,
                ),
                "train": _stats_for_period(raw_ic, start, TRAIN_END, sign),
                "test": _stats_for_period(raw_ic, TEST_START, end, sign),
                "recent": _stats_for_period(raw_ic, RECENT_START, end, sign),
                "bootstrap_full": _bootstrap(
                    raw_ic,
                    start,
                    end,
                    sign,
                    block_size=PRIMARY_BLOCK_SIZE,
                    n_bootstrap=INITIAL_BOOTSTRAP_RUNS,
                    seed=seed,
                ),
                "industry_neutral": {
                    "full": _stats_for_period(industry_ic, start, end, sign),
                    "train": _stats_for_period(industry_ic, start, TRAIN_END, sign),
                    "test": _stats_for_period(industry_ic, TEST_START, end, sign),
                    "recent": _stats_for_period(industry_ic, RECENT_START, end, sign),
                },
                "size_neutral": {
                    "full": _stats_for_period(size_ic, start, end, sign),
                    "train": _stats_for_period(size_ic, start, TRAIN_END, sign),
                    "test": _stats_for_period(size_ic, TEST_START, end, sign),
                    "recent": _stats_for_period(size_ic, RECENT_START, end, sign),
                },
                "industry_size_neutral": {
                    "full": _stats_for_period(combined_ic, start, end, sign),
                    "train": _stats_for_period(combined_ic, start, TRAIN_END, sign),
                    "test": _stats_for_period(combined_ic, TEST_START, end, sign),
                    "recent": _stats_for_period(combined_ic, RECENT_START, end, sign),
                },
                "q_value": None,
                "final_bootstrap": {},
            }
            trials.append(trial)

        annotate_plateau(trials)
        _yearly_q_values(trials)
        initial_finalists = select_robust_candidates(trials)
        finalists: list[dict[str, Any]] = []
        for trial in initial_finalists:
            final_bootstrap: dict[str, Any] = {}
            for block_size in FINAL_BLOCK_SIZES:
                final_bootstrap[str(block_size)] = _bootstrap(
                    raw_ic_by_trial[(family.family_id, int(trial["parameter"]))],
                    start,
                    end,
                    sign,
                    block_size=block_size,
                    n_bootstrap=FINAL_BOOTSTRAP_RUNS,
                    seed=_bootstrap_seed(
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
                _positive(recommended["final_bootstrap"][str(block)]["ci_low"])
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
            "initial_failure_counts": _initial_failure_counts(trials),
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
            "recommendation_order": [
                "maximize min(train_ic, test_ic)",
                "maximize final block-20 bootstrap ci_low",
                "minimize distance to current parameter",
            ],
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
        "families": {
            family_id: {
                "current": result["current_parameter"],
                "recommended": result["recommended_parameter"],
                "status": result["recommendation_status"],
            }
            for family_id, result in payload["families"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
