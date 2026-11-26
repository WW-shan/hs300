#!/usr/bin/env python3
"""R548: 20/60-day incremental screen for the R547 research candidates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import stats_v2  # noqa: E402
from app.factor_research.incremental import pairwise_cross_sectional_correlation  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.signal_scan import calculate_rank_ic  # noqa: E402

RESEARCH_TYPE = "statistical_screen"
RESEARCH_ID = "R548"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r548_multi_horizon_incremental_screen_preregistration.json"
)
RUNNER = "scripts/research_r548_multi_horizon_incremental_screen.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
R547_PREREG = RESEARCH / "r547_incremental_factor_screen_preregistration.json"
R547_RUNNER = ROOT / "scripts" / "research_r547_incremental_factor_screen.py"
R543_RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"
PREREG = RESEARCH / "r548_multi_horizon_incremental_screen_preregistration.json"
RESULT = RESEARCH / "r548_multi_horizon_incremental_screen_result.json"
MARKDOWN = RESEARCH / "r548_multi_horizon_incremental_screen_result.md"
MEMBERSHIP = RESEARCH / "r526_unified_pit_bundle_window" / "daily_snapshots.parquet"
INDUSTRY = RESEARCH / "r526_unified_pit_bundle_window" / "industry_history.parquet"
ALT_CONFIG = DATA / "ext_data" / "alt_hs300" / "config.json"

START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
HORIZONS = (20, 60)
MINIMUM_CROSS_SECTION = 30
MINIMUM_COVERAGE = 0.75
MINIMUM_VALID_DATES = 500
MINIMUM_TRAIN_IC = 0.005
MAX_INCREMENTAL_P = 0.20
MAX_FDR_Q = 0.20
MAX_STABLE7_CORRELATION = 0.85


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


def policy_hash(value: dict[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def add_horizon_returns(frame: pl.DataFrame, horizons: tuple[int, ...]) -> pl.DataFrame:
    expressions = [
        (
            pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0
        ).alias(f"forward_return_{horizon}d")
        for horizon in horizons
    ]
    return frame.with_columns(expressions)


def _ic_summary(
    ic: pl.DataFrame,
    start: date,
    end: date,
    *,
    horizon: int,
) -> dict[str, Any]:
    from app.factor_research.parameter_mc import stats_for_period

    return stats_for_period(
        ic,
        start,
        end,
        1,
        horizon=horizon,
        include_distribution=True,
    )


def _delta_summary(
    paired: pl.DataFrame,
    start: date,
    end: date,
    *,
    horizon: int,
) -> dict[str, Any]:
    values = (
        paired.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        .select((pl.col("combined_ic") - pl.col("base_ic")).alias("delta"))["delta"]
        .to_numpy()
    )
    if values.size <= horizon + 2:
        return {
            "n_dates": int(values.size),
            "directional_delta": None,
            "nw_t": None,
            "p_value": None,
        }
    nw = stats_v2.newey_west_t(values, lag=horizon)
    return {
        "n_dates": int(values.size),
        "directional_delta": round(float(values.mean()), 6),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": (
            round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
            if nw is not None
            else None
        ),
    }


def _coverage(panel: pl.DataFrame, factor_id: str) -> dict[str, Any]:
    valid = panel.filter(pl.col(factor_id).is_not_null())
    widths = valid.group_by("date").len().sort("date")["len"]
    return {
        "rows": panel.height,
        "valid_factor_rows": valid.height,
        "factor_non_null_ratio": round(valid.height / panel.height, 6)
        if panel.height
        else 0.0,
        "valid_dates": widths.len(),
        "minimum_cross_section": int(widths.min()) if widths.len() else 0,
        "median_cross_section": round(float(widths.median()), 3)
        if widths.len()
        else 0.0,
    }


def _candidate_panel(
    panel: pl.DataFrame,
    factor_id: str,
    horizon: int,
) -> pl.DataFrame:
    denominator = pl.col(factor_id).is_not_null().sum().over("date")
    return panel.select(
        "symbol",
        "date",
        "industry_l1",
        "log_float_mv",
        pl.col(f"forward_return_{horizon}d").alias("forward_return"),
        pl.col(factor_id).alias("_candidate_score"),
        pl.when(denominator > 0)
        .then(pl.col(factor_id).rank("average").over("date") / denominator)
        .otherwise(None)
        .alias("_candidate_pct"),
    ).filter(pl.col("_candidate_score").is_not_null())


def _evaluate_horizon(
    factor_id: str,
    base: pl.DataFrame,
    panel: pl.DataFrame,
    horizon: int,
) -> dict[str, Any]:
    candidate = _candidate_panel(panel, factor_id, horizon)
    work = (
        base.join(candidate, on=["symbol", "date"], how="inner")
        .filter(pl.col("forward_return").is_not_null())
        .sort(["date", "symbol"])
    )
    work = work.with_columns(
        (
            0.8 * pl.col("_base_pct") + 0.2 * pl.col("_candidate_pct")
        ).alias("_combined_pct")
    )
    base_ic = calculate_rank_ic(
        work,
        "_base_pct",
        "forward_return",
        minimum=MINIMUM_CROSS_SECTION,
    )
    candidate_ic = calculate_rank_ic(
        work,
        "_candidate_pct",
        "forward_return",
        minimum=MINIMUM_CROSS_SECTION,
    )
    combined_ic = calculate_rank_ic(
        work,
        "_combined_pct",
        "forward_return",
        minimum=MINIMUM_CROSS_SECTION,
    )
    paired = (
        base_ic.select(["date", pl.col("ic").alias("base_ic")])
        .join(
            combined_ic.select(["date", pl.col("ic").alias("combined_ic")]),
            on="date",
            how="inner",
        )
        .sort("date")
    )
    _, correlations = pairwise_cross_sectional_correlation(
        work,
        {"stable7": "_base_pct", "candidate": "_candidate_pct"},
        minimum_symbols=MINIMUM_CROSS_SECTION,
    )
    corr = (
        correlations.get("stable7|candidate")
        or correlations.get("candidate|stable7")
        or {}
    )
    raw = {
        "train": _ic_summary(candidate_ic, START, TRAIN_END, horizon=horizon),
        "validation": _ic_summary(
            candidate_ic, VALIDATION_START, VALIDATION_END, horizon=horizon
        ),
        "recent": _ic_summary(candidate_ic, RECENT_START, END, horizon=horizon),
        "full": _ic_summary(candidate_ic, START, END, horizon=horizon),
    }
    incremental = {
        "train": _delta_summary(paired, START, TRAIN_END, horizon=horizon),
        "validation": _delta_summary(
            paired, VALIDATION_START, VALIDATION_END, horizon=horizon
        ),
        "recent": _delta_summary(paired, RECENT_START, END, horizon=horizon),
        "full": _delta_summary(paired, START, END, horizon=horizon),
    }
    return {
        "horizon": horizon,
        "coverage": _coverage(panel, factor_id),
        "raw_ic": raw,
        "incremental": incremental,
        "correlation_to_stable7": (
            round(abs(float(corr["mean_abs"])), 6)
            if corr.get("mean_abs") is not None
            else None
        ),
        "train_ic": raw["train"].get("directional_ic"),
        "validation_ic": raw["validation"].get("directional_ic"),
        "recent_ic": raw["recent"].get("directional_ic"),
        "q_value": None,
    }


def _horizon_gate(row: dict[str, Any], horizon: int) -> dict[str, Any]:
    coverage = row.get("coverage") or {}
    train_inc = (row.get("incremental") or {}).get("train") or {}
    p_value = train_inc.get("p_value", train_inc.get("nw_p"))
    checks = {
        "coverage_ge_0_75": float(coverage.get("factor_non_null_ratio") or 0.0)
        >= MINIMUM_COVERAGE,
        "valid_dates_ge_500": int(coverage.get("valid_dates") or 0)
        >= MINIMUM_VALID_DATES,
        "train_ic_ge_0_005": float(row.get("train_ic") or 0.0) >= MINIMUM_TRAIN_IC,
        "validation_ic_positive": float(row.get("validation_ic") or 0.0) > 0.0,
        "incremental_train_positive": float(
            train_inc.get("directional_delta") or 0.0
        )
        > 0.0,
        "incremental_nw_p_le_0_20": (
            p_value is not None and float(p_value) <= MAX_INCREMENTAL_P
        ),
        "fdr_q_le_0_20": (
            row.get("q_value") is not None and float(row["q_value"]) <= MAX_FDR_Q
        ),
        "stable7_corr_le_0_85": (
            row.get("correlation_to_stable7") is None
            or float(row["correlation_to_stable7"]) <= MAX_STABLE7_CORRELATION
        ),
    }
    return {
        "horizon": horizon,
        "checks": checks,
        "pass": all(checks.values()),
    }


def candidate_gate(candidate: dict[str, Any]) -> dict[str, Any]:
    horizons = candidate.get("horizons") or {}
    checks = {
        str(horizon): _horizon_gate(row, horizon)
        for horizon, row in sorted(horizons.items())
    }
    passing = [int(horizon) for horizon, check in checks.items() if check["pass"]]
    primary = max(passing) if passing else None
    recent_ic = candidate.get("recent_ic")
    if recent_ic is None and primary is not None:
        recent_ic = (horizons[str(primary)].get("raw_ic") or {}).get("recent", {}).get(
            "directional_ic"
        )
    return {
        "horizon_checks": checks,
        "selection_pass": bool(passing),
        "primary_horizon": primary,
        "diagnostics": {
            "recent_ic_positive": (
                isinstance(recent_ic, (int, float)) and float(recent_ic) > 0.0
            ),
            "recent_is_diagnostic_only": True,
        },
        "conclusion": "finalist" if passing else "rejected",
    }


def _assign_fdr(candidates: dict[str, dict[str, Any]]) -> None:
    trials = [
        (factor_id, horizon, row)
        for factor_id, candidate in candidates.items()
        for horizon, row in candidate["horizons"].items()
    ]
    pvalues = [
        ((row.get("incremental") or {}).get("train") or {}).get("p_value")
        for _, _, row in trials
    ]
    qvalues = stats_v2.bh_fdr_qvalues(pvalues)
    for (_, _, row), qvalue in zip(trials, qvalues, strict=True):
        row["q_value"] = round(float(qvalue), 8) if qvalue is not None else None


def scientific_design() -> dict[str, Any]:
    return {
        "candidates": sorted(_load_r547().EXPECTED_CANDIDATES),
        "horizons": list(HORIZONS),
        "baseline": "stable7 rank-percentile composite from R547",
        "selection_policy": "a candidate passes if at least one predeclared horizon passes all train+validation checks",
        "recent_is_diagnostic_only": True,
        "maximum_selected_horizon": max(HORIZONS),
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
        },
        "gates": {
            "coverage_min": MINIMUM_COVERAGE,
            "valid_dates_min": MINIMUM_VALID_DATES,
            "train_ic_min": MINIMUM_TRAIN_IC,
            "validation_ic_positive": True,
            "incremental_train_positive": True,
            "incremental_nw_p_max": MAX_INCREMENTAL_P,
            "fdr_q_max": MAX_FDR_Q,
            "stable7_correlation_max": MAX_STABLE7_CORRELATION,
            "recent_policy": "report_only_not_used_for_selection",
        },
        "execution": {
            "matching": "signal_close_to_horizon_close_return",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _load_r547() -> Any:
    return _load_module("research_r547_incremental_factor_screen")


def _input_paths(root: Path) -> dict[str, Path]:
    r547 = _load_r547()
    return {
        **r547._input_paths(root),
        "r547_result": root / R547_RESULT.relative_to(ROOT),
        "r547_preregistration": root / R547_PREREG.relative_to(ROOT),
        "r547_runner": root / R547_RUNNER.relative_to(ROOT),
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R548 preregistration already exists: {PREREG}")
    r547 = _load_r547()
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Do the R547 financial, sentiment-flow and industry-quality composites "
            "show incremental signal at 20- or 60-day horizons consistent with the "
            "60-day portfolio hold?"
        ),
        "hypothesis": (
            "A slow fundamental composite can pass on a longer horizon even when the "
            "5-day screen rejects it; the horizon must still pass training and validation."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": {
            **{name: sha256(path) for name, path in _input_paths(root).items()},
            "alt_timeseries": r547._ext_timeseries_hash(),
            "candidate_definitions": policy_hash(candidate_hashes),
        },
        "parameters": {
            **scientific_design(),
            "candidate_definitions": candidate_hashes,
        },
        "sample": scientific_design()["windows"],
        "execution": scientific_design()["execution"],
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "selection_uses_training_and_validation_only",
            "recent_not_used_for_selection",
            "r547_candidates_reused_without_redefinition",
            "financial_factors_use_announcement_t_plus_one_gate",
            "r526_pit_membership_and_industry_used",
            "no_portfolio_return_claim",
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
        raise RuntimeError("R548 runner hash no longer matches preregistration")
    r547 = _load_r547()
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    if prereg["parameters"]["candidate_definitions"] != candidate_hashes:
        raise RuntimeError("R548 candidate definition hash changed")
    if prereg["input_hashes"]["candidate_definitions"] != policy_hash(candidate_hashes):
        raise RuntimeError("R548 candidate definition aggregate hash changed")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R548 input changed: {name}")
    if r547._ext_timeseries_hash() != prereg["input_hashes"].get("alt_timeseries"):
        raise RuntimeError("R548 extension timeseries changed")


def build_payload(
    *,
    base: pl.DataFrame,
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _assign_fdr(candidates)
    for factor_id in sorted(candidates):
        candidates[factor_id]["gate"] = candidate_gate(candidates[factor_id])
    finalists = [
        {
            "factor_id": factor_id,
            "primary_horizon": row["gate"]["primary_horizon"],
        }
        for factor_id, row in sorted(candidates.items())
        if row["gate"]["selection_pass"]
    ]
    data_gaps = [
        factor_id
        for factor_id, row in sorted(candidates.items())
        if all(
            not check["checks"]["coverage_ge_0_75"]
            or not check["checks"]["valid_dates_ge_500"]
            for check in row["gate"]["horizon_checks"].values()
        )
    ]
    base_valid = base.with_columns(pl.col("_base_pct").alias("_base_valid"))
    baseline = {
        "members": list(_load_r547().STABLE7),
        "coverage": _coverage(base_valid, "_base_valid"),
        "dataset_rows": base.height,
    }
    conclusion = "watch_only" if finalists else "rejected"
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "design": scientific_design(),
        "baseline": baseline,
        "candidates": candidates,
        "finalists": finalists,
        "data_gaps": data_gaps,
        "conclusion": conclusion,
        "known_limits": [
            "This is a statistical incremental screen, not a portfolio backtest or trading recommendation.",
            "The 20/60-day forward returns overlap and use Newey-West lag equal to the horizon.",
            "Financial fields use project PIT announcement gating; extension sentiment/flow fields remain declared PIT.",
            "Recent-year values were observed in R547 and are diagnostic only; selection uses training and validation.",
            "A horizon can pass by multiple testing chance; the next formal add-one test must confirm it.",
            "The candidate definitions remain draft and no active version, orders or leverage are produced.",
        ],
        "negative_knowledge": [],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    baseline_names = ", ".join(
        f"`{item['family_id']}`" for item in payload["baseline"]["members"]
    )
    lines = [
        "# R548 多周期增量因子筛选",
        "",
        "> 5 日筛选只作为 R547 的负知识；本轮检查与 60 日持有期更匹配的 20/60 日统计信号，仍不是组合收益结论。",
        "",
        "## 基线",
        "",
        f"- stable7：{baseline_names}",
        f"- 基线有效行：{payload['baseline']['coverage']['valid_factor_rows']:,}",
        f"- 结论：`{payload['conclusion']}`",
        "",
        "## 结果",
        "",
        "| 候选 | 周期 | 覆盖 | train IC | validation IC | recent IC | 增量 IC | 增量 p | q | stable7 相关 | 周期门槛 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for factor_id, row in sorted(payload["candidates"].items()):
        for horizon, trial in sorted(row["horizons"].items()):
            gate = row["gate"]["horizon_checks"][horizon]
            inc = trial["incremental"]["train"]
            cov = trial["coverage"]
            lines.append(
                f"| `{factor_id}` | {horizon}d | {cov['factor_non_null_ratio']:.2%} "
                f"| {trial['raw_ic']['train'].get('directional_ic')} "
                f"| {trial['raw_ic']['validation'].get('directional_ic')} "
                f"| {trial['raw_ic']['recent'].get('directional_ic')} "
                f"| {inc.get('directional_delta')} | {inc.get('p_value')} "
                f"| {trial.get('q_value')} | {trial.get('correlation_to_stable7')} "
                f"| {'PASS' if gate['pass'] else 'reject'} |"
            )
    lines += [
        "",
        "## 入选与数据缺口",
        "",
        f"- finalists：{payload['finalists'] if payload['finalists'] else '无'}",
        f"- data gaps：{payload['data_gaps'] if payload['data_gaps'] else '无'}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def publish_result(payload: dict[str, Any]) -> dict[str, Any]:
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
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "conclusion": payload["conclusion"],
            "gate_results": {
                "finalists": payload["finalists"],
                "data_gaps": payload["data_gaps"],
                "selection_uses_recent": False,
            },
            "metrics": {
                "design": payload["design"],
                "baseline": payload["baseline"],
                "candidates": payload["candidates"],
                "finalists": payload["finalists"],
                "data_gaps": payload["data_gaps"],
                "known_limits": payload["known_limits"],
                "output_policy": payload["output_policy"],
            },
            "source_result_hashes": [
                sha256(R547_RESULT),
                sha256(R543_RESULT),
                sha256(R530_RESULT),
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
        operation="r548_multi_horizon_incremental_screen_published",
    ).__dict__


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r547 = _load_r547()
    definitions = r547.load_candidate_definitions(root)
    membership = r547._load_membership(root)
    panel = r547._load_price_panel(root, membership)
    panel = add_horizon_returns(panel, HORIZONS)
    r530 = _load_module("research_r530_unified_factor_library_mc")
    context = r530._load_context(root, membership)
    context.prices = panel
    context.extended["prices"] = panel
    core = r547._stable_core(root, context, membership)
    base = core.with_columns(pl.col("factor_value").alias("_base_pct")).select(
        "symbol", "date", "_base_pct"
    )
    panel = r547._add_candidate_sources(panel, definitions)
    candidates: dict[str, dict[str, Any]] = {}
    for factor_id, definition in definitions.items():
        candidates[factor_id] = {
            "factor_id": factor_id,
            "label": definition["label"],
            "members": definition["members"],
            "horizons": {
                str(horizon): _evaluate_horizon(factor_id, base, panel, horizon)
                for horizon in HORIZONS
            },
        }
    return build_payload(base=base, candidates=candidates)


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
        raise FileExistsError("R548 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(
        json.dumps(
            {
                "conclusion": payload["conclusion"],
                "finalists": payload["finalists"],
                "data_gaps": payload["data_gaps"],
                "published_result": str(published.get("result_path")),
                "ledger_record_id": published.get("ledger_record_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
