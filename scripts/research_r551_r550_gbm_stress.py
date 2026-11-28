#!/usr/bin/env python3
"""R551A: standardized GBM path stress for the frozen R550 cash_safety score."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import synthetic_gbm as gbm  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    summarize_gbm_paths,
)

RESEARCH_TYPE = "synthetic_market_stress"
RESEARCH_ID = "R551A"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r551a_r550_gbm_stress_preregistration.json"
)
RUNNER = "scripts/research_r551_r550_gbm_stress.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R550_RESULT = RESEARCH / "r550_cash_safety_confirmation_result.json"
R549_RESULT = RESEARCH / "r549b_quality_addon_portfolio_result.json"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
R550_PREREG = RESEARCH / "r550_cash_safety_confirmation_preregistration.json"
R551_ATTEMPT1 = RESEARCH / "r551_attempt1_fixed_universe_coverage_failure.json"
PREREG = RESEARCH / "r551a_r550_gbm_stress_preregistration.json"
RESULT = RESEARCH / "r551a_r550_gbm_stress_result.json"
MARKDOWN = RESEARCH / "r551a_r550_gbm_stress_result.md"
START = date(2024, 1, 2)
EVALUATION_DAYS = 504
PATH_COUNT = 100
BETA_VALUES = (0.0, 0.7)
COSTS = (2, 3, 5)
STOP = -0.12
TOP_RANK = 20
FIXED_ADDONS = (
    "cf_r547_cash_quality",
    "cf_r547_value_quality_safety",
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
    path = ROOT / "scripts" / f"{name}.py"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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


def build_frozen_score_matrix(
    frame: pl.DataFrame,
    *,
    target_dates: list[date] | tuple[date, ...],
    target_symbols: list[str] | tuple[str, ...],
) -> tuple[np.ndarray, tuple[str, ...], tuple[date, ...]]:
    dates = tuple(target_dates)
    symbols = tuple(target_symbols)
    if not dates or not symbols:
        raise ValueError("R551A score matrix requires non-empty dates and symbols")
    selected = frame.filter(
        pl.col("date").is_in(dates) & pl.col("symbol").is_in(symbols)
    ).select("date", "symbol", "score")
    values = {
        (row["date"], row["symbol"]): float(row["score"])
        for row in selected.iter_rows(named=True)
        if row["score"] is not None
    }
    matrix = np.zeros((len(dates), len(symbols)), dtype=np.float32)
    for date_index, day in enumerate(dates):
        for symbol_index, symbol in enumerate(symbols):
            value = values.get((day, symbol))
            if value is not None and np.isfinite(value):
                matrix[date_index, symbol_index] = float(value)
    return matrix, symbols, dates


def _score_panel(root: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    r542 = _load_module("research_r542_recent_failure_attribution")
    r547 = _load_module("research_r547_incremental_factor_screen")
    r549 = _load_module("research_r549_quality_addon_portfolio")
    runtime = r542.load_runtime(root)
    membership = r547._load_membership(root)
    candidate_panel = r547._load_price_panel(root, membership)
    candidate_panel = r547._add_candidate_sources(
        candidate_panel,
        r547.load_candidate_definitions(root),
    )
    panel = r549.build_variant_panel(runtime, candidate_panel, FIXED_ADDONS)
    return panel, runtime["industries"]


def _target_dates(panel: pl.DataFrame) -> tuple[date, ...]:
    dates = (
        panel.filter(pl.col("date") >= START)
        .select("date")
        .unique()
        .sort("date")["date"]
        .to_list()
    )
    if len(dates) < EVALUATION_DAYS:
        raise RuntimeError(f"R551A insufficient dates: {len(dates)}")
    return tuple(dates[:EVALUATION_DAYS])


def _target_symbols(panel: pl.DataFrame, dates: tuple[date, ...]) -> tuple[str, ...]:
    coverage = (
        panel.filter(pl.col("date").is_in(dates))
        .group_by("symbol")
        .agg(
            pl.col("score").is_not_null().sum().alias("valid_days"),
            pl.len().alias("rows"),
        )
        .sort(["valid_days", "symbol"], descending=[True, False])
    )
    symbols = coverage["symbol"].to_list()
    if len(symbols) < 300:
        raise RuntimeError(f"R551A insufficient fixed symbols: {len(symbols)}")
    return tuple(symbols[:300])


def scientific_design() -> dict[str, Any]:
    return {
        "model": "geometric_brownian_motion",
        "path_count": PATH_COUNT,
        "start": str(START),
        "evaluation_days": EVALUATION_DAYS,
        "scenarios": [
            {"scenario_id": key, "annual_drift": value.annual_drift, "annual_volatility": value.annual_volatility}
            for key, value in gbm.SCENARIOS.items()
        ],
        "betas": list(BETA_VALUES),
        "cost_multipliers": list(COSTS),
        "stop_loss": STOP,
        "top_rank": TOP_RANK,
        "fixed_addons": list(FIXED_ADDONS),
        "score_source": "R550 cash_safety score panel; 300 highest-coverage fixed symbols",
        "benchmark": "same_path_equal_weight_buy_and_hold",
        "execution": {
            "matching": "open_t+1",
            "max_positions": 10,
            "max_hold_days": 60,
            "industry_cap": 3,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_policy": "warning_only",
        },
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": PATH_COUNT},
            "parameter_mc": {
                "required": False,
                "status": "fixed_frozen",
                "reason": "R550 parameters are frozen; R552 owns parameter-neighborhood MC",
            },
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _input_paths(root: Path) -> dict[str, Path]:
    return {
        "r550_result": root / R550_RESULT.relative_to(ROOT),
        "r549_result": root / R549_RESULT.relative_to(ROOT),
        "r547_result": root / R547_RESULT.relative_to(ROOT),
        "r550_preregistration": root / R550_PREREG.relative_to(ROOT),
        "r551_attempt1": root / R551_ATTEMPT1.relative_to(ROOT),
        "candidate_definitions": root / "data" / "factor_research" / "candidates" / "r547",
        "membership": root / "data" / "factor_research" / "research" / "r526_unified_pit_bundle_window" / "daily_snapshots.parquet",
        "industry": root / "data" / "factor_research" / "research" / "r526_unified_pit_bundle_window" / "industry_history.parquet",
        "alt_timeseries": root / "data" / "ext_data" / "alt_hs300" / "timeseries",
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R551A preregistration already exists: {PREREG}")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root / "data" / "factor_research" / "candidates" / "r547" / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    inputs = _input_paths(root)
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "How does the frozen R550 cash_safety score perform across standardized "
            "GBM market scenarios, 100 Monte Carlo paths and 2x/3x/5x costs versus "
            "the same-path equal-weight benchmark?"
        ),
        "hypothesis": (
            "The R550 score can be executed without leverage or exposure violations, "
            "but GBM is a diagnostic null for real financial-factor alpha and cannot "
            "grant production status."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "synthetic_gbm",
        "synthetic_model": {
            "model": "geometric_brownian_motion",
            "parameters": {
                "scenarios": scientific_design()["scenarios"],
                "betas": list(BETA_VALUES),
                "cost_multipliers": list(COSTS),
            },
            "seed": gbm.DEFAULT_MASTER_SEED,
            "path_count": PATH_COUNT,
            "benchmark": "same_path_equal_weight_buy_and_hold",
        },
        "input_hashes": {
            **{
                name: sha256(path)
                for name, path in inputs.items()
                if name not in {"candidate_definitions", "alt_timeseries"}
            },
            "alt_timeseries": r547._ext_timeseries_hash(),
            "candidate_definitions": hashlib.sha256(
                json.dumps(candidate_hashes, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "parameters": {
            **scientific_design(),
            "candidate_definitions": candidate_hashes,
        },
        "sample": {
            "start": str(START),
            "evaluation_days": EVALUATION_DAYS,
            "path_count": PATH_COUNT,
            "asset_count": 300,
            "scenario_count": len(gbm.SCENARIOS),
            "beta_count": len(BETA_VALUES),
            "cost_count": len(COSTS),
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "path_count": PATH_COUNT,
            "scenarios": len(gbm.SCENARIOS),
            "cost_multipliers": list(COSTS),
            "deterministic_replay": True,
            "no_leverage": True,
            "same_path_benchmark": True,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "frozen_r550_score_panel",
            "same_seed_byte_identical_replay",
            "signal_t_fills_at_t_plus_one_open_only",
            "no_leverage_and_max_exposure_at_or_below_100pct",
            "same_path_equal_weight_benchmark",
            "all_100_paths_completed",
            "plots_are_sidecar_bound",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R551A runner hash no longer matches preregistration")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root / "data" / "factor_research" / "candidates" / "r547" / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    if prereg["parameters"]["candidate_definitions"] != candidate_hashes:
        raise RuntimeError("R551A candidate definition hash changed")
    inputs = _input_paths(root)
    for name, path in inputs.items():
        if name in {"candidate_definitions", "alt_timeseries"}:
            continue
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R551A input changed: {name}")
    expected = hashlib.sha256(
        json.dumps(candidate_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if prereg["input_hashes"]["candidate_definitions"] != expected:
        raise RuntimeError("R551A candidate aggregate hash changed")
    if prereg["input_hashes"]["alt_timeseries"] != r547._ext_timeseries_hash():
        raise RuntimeError("R551A extension timeseries changed")


def _run_paths(
    market_scores: np.ndarray,
    symbols: tuple[str, ...],
    dates: tuple[date, ...],
    industries: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {path_id: [] for path_id in range(PATH_COUNT)}
    for path_id in range(PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in BETA_VALUES:
                result = gbm.run_fixed_score_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                    scores=market_scores,
                    score_name="r550_cash_safety",
                    asset_count=300,
                    warmup_days=gbm.WARMUP_DAYS,
                    evaluation_days=EVALUATION_DAYS,
                    max_positions=10,
                    max_hold_days=60,
                    top_rank=TOP_RANK,
                    industry_cap=3,
                    stop_loss_pct=STOP,
                    cost_multipliers=COSTS,
                    symbols=symbols,
                    industries=industries,
                    dates=dates,
                )
                grouped[path_id].extend(result["observations"])
    paths = [{"path_id": path_id, "observations": rows} for path_id, rows in grouped.items()]
    replay_a = gbm.run_fixed_score_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=market_scores,
        score_name="r550_cash_safety",
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=EVALUATION_DAYS,
        max_positions=10,
        max_hold_days=60,
        top_rank=TOP_RANK,
        industry_cap=3,
        stop_loss_pct=STOP,
        cost_multipliers=COSTS,
        symbols=symbols,
        industries=industries,
        dates=dates,
    )
    replay_b = gbm.run_fixed_score_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=market_scores,
        score_name="r550_cash_safety",
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=EVALUATION_DAYS,
        max_positions=10,
        max_hold_days=60,
        top_rank=TOP_RANK,
        industry_cap=3,
        stop_loss_pct=STOP,
        cost_multipliers=COSTS,
        symbols=symbols,
        industries=industries,
        dates=dates,
    )
    deterministic = replay_a["realized"]["close_sha256"] == replay_b["realized"]["close_sha256"] and replay_a["observations"] == replay_b["observations"]
    return paths, {"deterministic_replay": deterministic}


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    panel, industries = _score_panel(root)
    dates = _target_dates(panel)
    symbols = _target_symbols(panel, dates)
    score_matrix, frozen_symbols, frozen_dates = build_frozen_score_matrix(
        panel,
        target_dates=dates,
        target_symbols=symbols,
    )
    score_industries = {
        symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}")
        for index, symbol in enumerate(frozen_symbols)
    }
    paths, replay = _run_paths(score_matrix, frozen_symbols, frozen_dates, score_industries)
    gbm_summary = summarize_gbm_paths(
        paths,
        path_count=PATH_COUNT,
        scenarios=set(gbm.SCENARIOS),
        costs=COSTS,
        deterministic_replay=replay["deterministic_replay"],
    )
    if gbm_summary["constraints"]["errors"]:
        raise RuntimeError(f"R551A GBM constraints failed: {gbm_summary['constraints']['errors']}")
    with tempfile.TemporaryDirectory(prefix="r551_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(gbm_summary["summary"], plot_dir / "r551_r550_gbm_distribution.png")
        constraints_plot = plot_gbm_constraints(gbm_summary["cost_summary"], plot_dir / "r551_r550_gbm_constraints.png")
        artifacts = [
            {
                "kind": "gbm_distribution",
                "path": distribution.name,
                "sha256": hashlib.sha256(distribution.read_bytes()).hexdigest(),
            },
            {
                "kind": "gbm_constraints",
                "path": constraints_plot.name,
                "sha256": hashlib.sha256(constraints_plot.read_bytes()).hexdigest(),
            },
        ]
        attachment_bytes = [(distribution.name, distribution.read_bytes()), (constraints_plot.name, constraints_plot.read_bytes())]
        receipt = build_standard_validation_receipt(
            gbm_summary,
            {
                "status": "fixed_frozen",
                "reason": "R550 parameters are frozen; R552 owns parameter-neighborhood MC",
                "recent_used_for_selection": False,
            },
            artifacts=artifacts,
        )
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R551A standard validation failed: {checked['errors']}")
        payload = _build_payload(
            gbm_summary=gbm_summary,
            receipt=receipt,
            score_matrix=score_matrix,
            symbols=frozen_symbols,
            dates=frozen_dates,
            paths=paths,
        )
        published = _publish(payload, attachment_bytes)
    return {**payload, "published": published}


def _build_payload(
    *,
    gbm_summary: dict[str, Any],
    receipt: dict[str, Any],
    score_matrix: np.ndarray,
    symbols: tuple[str, ...],
    dates: tuple[date, ...],
    paths: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "score_freeze": {
            "matrix_sha256": hashlib.sha256(score_matrix.tobytes()).hexdigest(),
            "symbols": list(symbols),
            "dates": [str(value) for value in dates],
            "rows": int(score_matrix.shape[0]),
            "columns": int(score_matrix.shape[1]),
        },
        "gbm": gbm_summary,
        "standard_validation": receipt,
        "paths": paths,
        "conclusion": "diagnostic_only",
        "known_limits": [
            "GBM has no real financial statements, dividend, fund-holding, sentiment or macro fields.",
            "The R550 score is frozen from the real-data portfolio; this tests its ranking and execution under synthetic prices, not its real-data alpha.",
            "The GBM adapter uses project matcher equal position sizing; the R550 vol_12 position policy is not applied in this synthetic diagnostic.",
            "5x cost is warning-only and synthetic results cannot admit a strategy or generate orders.",
        ],
        "negative_knowledge": [],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    gbm_summary = payload["gbm"]
    lines = [
        "# R551A R550 策略级 GBM 压力测试",
        "",
        "> `diagnostic_only`：这是合成行情诊断，不是真实收益结论，也不能启用策略。",
        "",
        "## 标准轴",
        "",
        f"- 路径：{gbm_summary['path_count']} 条",
        f"- 情景：{gbm_summary['scenarios']} 个",
        f"- 成本：{gbm_summary['cost_multipliers']}",
        f"- 固定股票数：{payload['score_freeze']['columns']}",
        f"- 评价交易日：{payload['score_freeze']['rows']}",
        "",
        "## 情景结果",
        "",
        "| 情景 | 超额 p05 | 超额 p50 | 超额 p95 | 正超额比例 | MDD p50 | Sharpe p50 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in gbm_summary["summary"].items():
        lines.append(
            f"| {name} | {row['excess_p05']:.2%} | {row['excess_p50']:.2%} | "
            f"{row['excess_p95']:.2%} | {row['positive_excess_ratio']:.2%} | "
            f"{row['mdd_p50']:.2%} | {row['sharpe_p50']:.2f} |"
        )
    lines += [
        "",
        "## 成本结果",
        "",
        "| 成本 | 年化 p50 | MDD p50 | 超额 p50 |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for cost, row in gbm_summary["cost_summary"].items():
        lines.append(
            f"| {cost}x | {row['annual_return_p50']:.2%} | {row['mdd_p50']:.2%} | {row['excess_p50']:.2%} |"
        )
    lines += [
        "",
        "## 标准回执",
        "",
        f"- status：`{payload['standard_validation']['gbm']['status']}`",
        f"- 约束错误：{gbm_summary['constraints']['errors']}",
        f"- 图表：{', '.join(item['path'] for item in payload['standard_validation']['artifacts'])}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def _publish(payload: dict[str, Any], attachments: list[tuple[str, bytes]]) -> dict[str, Any]:
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate(
        {
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "runner": RUNNER,
            "runner_sha256": prereg["runner_sha256"],
            "preregistration_sha256": prereg_hash,
            "generated_at": payload["generated_at"],
            "conclusion": payload["conclusion"],
            "gate_results": payload["gbm"]["constraints"],
            "metrics": {
                "design": payload["design"],
                "score_freeze": payload["score_freeze"],
                "gbm": payload["gbm"],
                "standard_validation": payload["standard_validation"],
                "paths": payload["paths"],
            },
            "source_result_hashes": [
                sha256(R550_RESULT),
                sha256(R549_RESULT),
                sha256(R547_RESULT),
            ],
            "formal_evidence": None,
            "known_limits": payload["known_limits"],
            "negative_knowledge": payload["negative_knowledge"],
            "output_policy": payload["output_policy"],
        }
    )
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r551a_r550_gbm_stress_published",
        attachments=attachments,
    ).__dict__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-preregistration", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R551A result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(
        json.dumps(
            {
                "conclusion": payload["conclusion"],
                "gbm": payload["gbm"]["summary"],
                "costs": payload["gbm"]["cost_summary"],
                "published_result": str(payload["published"].get("result_path")),
                "ledger_record_id": payload["published"].get("ledger_record_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
