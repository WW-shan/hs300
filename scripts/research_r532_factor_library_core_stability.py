#!/usr/bin/env python3
"""R532: test time stability of the frozen R531 factor-library core."""

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

RESEARCH_TYPE = "statistical_screen"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/"
    "r532_factor_library_core_stability_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"
R530_PREREG = RESEARCH / "r530_unified_factor_library_mc_preregistration.json"
R531_RESULT = RESEARCH / "r531_factor_library_core_result.json"
R531_PREREG = RESEARCH / "r531_factor_library_core_preregistration.json"
PREREG = RESEARCH / "r532_factor_library_core_stability_preregistration.json"
RESULT = RESEARCH / "r532_factor_library_core_stability_result.json"
MARKDOWN = RESEARCH / "r532_factor_library_core_stability_result.md"
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 10, 14)
ROLLING_WINDOW = 252
ROLLING_STEP = 21
MIN_ROLLING_WINDOWS = 24
FINAL_MC = 5000
BLOCK_SIZES = (10, 20, 40)
ROLLING_BLOCK_SIZE = 20
SEED = 20261125
EXPECTED_REPRESENTATIVES = frozenset({
    "amount_level",
    "atr_risk",
    "dividend_yield_absolute",
    "max_return",
    "turnover_level",
    "vol_of_vol",
})
PERIODS = {
    "full": (START, END),
    "test": (TEST_START, END),
    "recent": (RECENT_START, END),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    side = path.with_name(path.name + ".sha256")
    if not side.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = side.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def load_frozen_core(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(root)
    for relative in (R530_PREREG, R530_RESULT, R531_PREREG, R531_RESULT):
        verify_sidecar(root / relative.relative_to(ROOT))
    payload = json.loads((root / R531_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    if payload.get("research_type") != RESEARCH_TYPE:
        raise RuntimeError("R531 source is not a statistical screen")
    if payload.get("conclusion") != "admitted_for_research":
        raise RuntimeError("R531 core was not admitted for research")
    representatives = payload.get("metrics", {}).get("representatives", [])
    clusters = payload.get("metrics", {}).get("clusters", [])
    family_ids = {str(row.get("family_id")) for row in representatives}
    if len(representatives) != 6 or family_ids != EXPECTED_REPRESENTATIVES:
        raise RuntimeError(
            f"expected six frozen R531 representatives, got {sorted(family_ids)}"
        )
    if len(clusters) != 6:
        raise RuntimeError(f"expected six R531 clusters, got {len(clusters)}")
    for row in representatives:
        if row["family_id"] not in row["cluster"]:
            raise RuntimeError(f"representative outside cluster: {row['family_id']}")
    return representatives, payload


def _load_script_module(name: str) -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_study_inputs(root: Path) -> dict[str, Any]:
    representatives, r531_payload = load_frozen_core(root)
    r531 = _load_script_module("research_r531_factor_library_core")
    r530 = r531._load_r530()
    admitted = r531.load_admitted_candidates(root)
    by_family = {row.family_id: row for row in admitted}
    missing = sorted(EXPECTED_REPRESENTATIVES - set(by_family))
    if missing:
        raise RuntimeError(f"R531 representatives are not in R530 admitted set: {missing}")
    frozen_candidates = [by_family[row["family_id"]] for row in representatives]
    membership = r530.load_pit_membership(root)
    context = r530._load_context(root, membership)
    frames = r531._load_factor_frames(frozen_candidates, context, membership)
    return {
        "representatives": representatives,
        "r531_payload": r531_payload,
        "r531": r531,
        "r530": r530,
        "candidates": by_family,
        "membership": membership,
        "context": context,
        "frames": frames,
    }


def build_core(inputs: dict[str, Any], representatives: list[dict[str, Any]]) -> tuple[pl.DataFrame, dict[str, Any], pl.DataFrame]:
    r531 = inputs["r531"]
    candidates = {
        row["family_id"]: inputs["candidates"][row["family_id"]]
        for row in representatives
    }
    core = r531._core_frame(
        representatives,
        candidates,
        inputs["frames"],
        inputs["membership"],
    )
    stats, gate_ic = r531._core_ic(core, inputs["context"], inputs["membership"])
    return core, stats, gate_ic


def _assert_r531_core_reproduced(inputs: dict[str, Any], stats: dict[str, Any]) -> None:
    committed = inputs["r531_payload"]["metrics"]["core"]["stats"]["industry_size_neutral"]
    for period in ("full", "test", "recent"):
        expected = committed[period]["directional_ic"]
        observed = stats["industry_size_neutral"][period]["directional_ic"]
        if expected is None or observed is None or abs(float(expected) - float(observed)) > 1e-5:
            raise RuntimeError(
                f"R531 core drift for {period}: expected {expected}, observed {observed}"
            )


def rolling_ic_windows(
    ic: pl.DataFrame, *, window: int, step: int
) -> list[pl.DataFrame]:
    if window <= 1 or step <= 0:
        raise ValueError("window must exceed one and step must be positive")
    clean = (
        ic.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        .select("date", "n", "ic")
        .sort("date")
    )
    windows = []
    for start in range(0, clean.height - window + 1, step):
        windows.append(clean.slice(start, window))
    return windows


def bootstrap_seed(label: str, block_size: int, n_bootstrap: int) -> int:
    token = hashlib.sha256(
        f"R532:{label}:{block_size}:{n_bootstrap}:{SEED}".encode()
    ).hexdigest()
    return int(token[:8], 16)


def core_conclusion(gates: dict[str, bool]) -> tuple[str, list[str]]:
    reasons = [name for name, passed in gates.items() if not passed]
    directional = (
        gates.get("full_ic_ge_0_01", False)
        and gates.get("validation_ic_positive", False)
        and gates.get("recent_ic_positive", False)
    )
    if not directional:
        return "rejected", reasons
    if not gates.get("rolling_window_count_ge_24", False):
        return "inconclusive", reasons
    if all(gates.values()):
        return "admitted_for_research", []
    return "rejected", reasons


def summarise_rolling_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "window_count": 0,
            "median_ic": None,
            "positive_ratio": 0.0,
            "worst_ic": None,
            "bootstrap_positive_ratio": 0.0,
            "worst_bootstrap_ci_low": None,
        }
    values = [float(row["directional_ic"]) for row in records]
    ci_lows = [
        float(row["bootstrap"]["ci_low"])
        for row in records
        if isinstance(row.get("bootstrap", {}).get("ci_low"), (int, float))
    ]
    values_sorted = sorted(values)
    middle = len(values_sorted) // 2
    median = (
        values_sorted[middle]
        if len(values_sorted) % 2
        else (values_sorted[middle - 1] + values_sorted[middle]) / 2.0
    )
    return {
        "window_count": len(records),
        "median_ic": round(median, 6),
        "positive_ratio": round(sum(value > 0 for value in values) / len(values), 6),
        "worst_ic": round(min(values), 6),
        "bootstrap_positive_ratio": (
            round(sum(value > 0 for value in ci_lows) / len(ci_lows), 6)
            if ci_lows
            else 0.0
        ),
        "worst_bootstrap_ci_low": round(min(ci_lows), 6) if ci_lows else None,
    }


def bootstrap_periods(gate_ic: pl.DataFrame, p3: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for period, (start, end) in PERIODS.items():
        result[period] = {}
        for block_size in BLOCK_SIZES:
            result[period][str(block_size)] = p3._bootstrap_ic_or_empty(
                gate_ic,
                start,
                end,
                1,
                block_size=block_size,
                n_bootstrap=FINAL_MC,
                seed=bootstrap_seed(f"period:{period}", block_size, FINAL_MC),
            )
    return result


def rolling_window_records(
    windows: list[pl.DataFrame], p3: Any
) -> list[dict[str, Any]]:
    records = []
    for index, window in enumerate(windows):
        start = window["date"][0]
        end = window["date"][-1]
        stats = p3.stats_for_period(window, start, end, 1, horizon=5)
        bootstrap = p3._bootstrap_ic_or_empty(
            window,
            start,
            end,
            1,
            block_size=ROLLING_BLOCK_SIZE,
            n_bootstrap=FINAL_MC,
            seed=bootstrap_seed(
                f"rolling:{index}:{start.isoformat()}:{end.isoformat()}",
                ROLLING_BLOCK_SIZE,
                FINAL_MC,
            ),
        )
        records.append({
            "index": index,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "directional_ic": stats["directional_ic"],
            "n_dates": stats["n_dates"],
            "ic_win_rate": round(float((window["ic"] > 0).mean()), 6),
            "bootstrap": bootstrap,
        })
    return records


def paired_ic_difference(
    base_ic: pl.DataFrame,
    variant_ic: pl.DataFrame,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    from app.backtest import stats_v2

    base = base_ic.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).select("date", pl.col("ic").alias("base"))
    variant = variant_ic.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).select("date", pl.col("ic").alias("variant"))
    paired = base.join(variant, on="date", how="inner").drop_nulls()
    if paired.is_empty():
        return {
            "mean_delta": None,
            "nw_t": None,
            "p_value": None,
            "n_dates": 0,
        }
    values = (paired["base"] - paired["variant"]).to_numpy()
    nw = stats_v2.newey_west_t(values, lag=5)
    return {
        "mean_delta": round(float(values.mean()), 6),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": (
            round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
            if nw is not None
            else None
        ),
        "n_dates": int(values.size),
    }


def leave_one_out_diagnostics(
    inputs: dict[str, Any],
    core_gate_ic: pl.DataFrame,
) -> list[dict[str, Any]]:
    representatives = inputs["representatives"]
    rows = []
    for removed in representatives:
        remaining = [
            row for row in representatives if row["family_id"] != removed["family_id"]
        ]
        _, stats, variant_ic = build_core(inputs, remaining)
        rows.append({
            "removed": removed["family_id"],
            "remaining_count": len(remaining),
            "stats": stats["industry_size_neutral"],
            "paired_delta_vs_core": {
                period: paired_ic_difference(
                    core_gate_ic,
                    variant_ic,
                    start=start,
                    end=end,
                )
                for period, (start, end) in PERIODS.items()
            },
        })
    return rows


def _representative_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for representative in inputs["representatives"]:
        candidate = inputs["candidates"][representative["family_id"]]
        source = candidate.r530_row
        rows.append({
            "family_id": representative["family_id"],
            "parameter": candidate.parameter,
            "cluster": representative["cluster"],
            "cluster_size": representative["cluster_size"],
            "r531_training_ic": representative["training_directional_ic"],
            "data_class": source["data_class"],
            "r530_plateau_status": source.get("plateau_after_pit"),
            "r530_prior_plateau": source.get("prior_plateau"),
            "r530_q_value": source.get("q_value"),
            "r530_bootstrap_ci_low": {
                key: value.get("ci_low")
                for key, value in source.get("final_bootstrap", {}).items()
            },
        })
    return rows


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = load_study_inputs(root)
    _, core_stats, gate_ic = build_core(inputs, inputs["representatives"])
    _assert_r531_core_reproduced(inputs, core_stats)
    period_bootstrap = bootstrap_periods(gate_ic, inputs["context"].p3)
    windows = rolling_ic_windows(
        gate_ic,
        window=ROLLING_WINDOW,
        step=ROLLING_STEP,
    )
    rolling_records = rolling_window_records(windows, inputs["context"].p3)
    rolling_summary = summarise_rolling_records(rolling_records)
    leave_one_out = leave_one_out_diagnostics(inputs, gate_ic)
    yearly = core_stats["full"].get("yearly", [])
    yearly_positive = sum(float(row["directional_ic"]) > 0 for row in yearly)
    period_ci_positive = all(
        period_bootstrap[period][str(block_size)].get("ci_low") is not None
        and float(period_bootstrap[period][str(block_size)]["ci_low"]) > 0
        for period in PERIODS
        for block_size in BLOCK_SIZES
    )
    gates = {
        "frozen_core_chain_valid": True,
        "coverage_ge_0_90": core_stats["coverage"]["factor_non_null_ratio"] >= 0.90,
        "full_ic_ge_0_01": core_stats["full"]["directional_ic"] >= 0.01,
        "validation_ic_positive": core_stats["test"]["directional_ic"] > 0,
        "recent_ic_positive": core_stats["recent"]["directional_ic"] > 0,
        "yearly_positive_min_4_of_5": len(yearly) >= 5 and yearly_positive >= 4,
        "period_bootstrap_ci_positive": period_ci_positive,
        "rolling_window_count_ge_24": rolling_summary["window_count"] >= MIN_ROLLING_WINDOWS,
        "rolling_median_positive": (
            rolling_summary["median_ic"] is not None
            and rolling_summary["median_ic"] > 0
        ),
        "rolling_positive_ratio_ge_0_70": rolling_summary["positive_ratio"] >= 0.70,
        "rolling_worst_ic_ge_minus_0_01": (
            rolling_summary["worst_ic"] is not None
            and rolling_summary["worst_ic"] >= -0.01
        ),
        "rolling_bootstrap_positive_ratio_ge_0_70": (
            rolling_summary["bootstrap_positive_ratio"] >= 0.70
        ),
        "rolling_worst_bootstrap_ci_low_ge_minus_0_01": (
            rolling_summary["worst_bootstrap_ci_low"] is not None
            and rolling_summary["worst_bootstrap_ci_low"] >= -0.01
        ),
    }
    conclusion, reasons = core_conclusion(gates)
    payload = {
        "schema_version": 1,
        "research_id": "R532",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r530": R530_RESULT.relative_to(ROOT).as_posix(),
        "source_r531": R531_RESULT.relative_to(ROOT).as_posix(),
        "representatives": _representative_rows(inputs),
        "sample": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(TEST_START),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "horizon_days": 5,
            "neutralization": "industry+size",
            "minimum_cross_section": 30,
        },
        "core": {
            "stats": core_stats,
            "period_bootstrap": period_bootstrap,
        },
        "yearly_stability": {
            "rows": yearly,
            "positive_years": yearly_positive,
            "year_count": len(yearly),
        },
        "rolling_stability": {
            "window": ROLLING_WINDOW,
            "step": ROLLING_STEP,
            "bootstrap_block_size": ROLLING_BLOCK_SIZE,
            "summary": rolling_summary,
            "windows": rolling_records,
        },
        "leave_one_out": leave_one_out,
        "gates": gates,
        "conclusion": conclusion,
        "negative_knowledge": (
            [f"stability_gate_failed:{reason}" for reason in reasons]
            if conclusion != "admitted_for_research"
            else []
        ),
        "known_limits": [
            "The six representatives and all factor parameters are frozen from R530/R531.",
            "Rolling 252-day windows overlap by 231 days; their positive ratio is not an independent-sample rate.",
            "Block bootstrap is applied to daily IC and does not create a portfolio return stream.",
            "Leave-one-out results are diagnostic only and are not used to delete factors in this round.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload, inputs


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R532 因子库核心时间稳定性与综合能力",
        "",
        "## 冻结代表因子",
        "",
        "| 因子 | 参数 | 聚类大小 | R531 训练 IC | R530 plateau |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in payload["representatives"]:
        lines.append(
            f"| {row['family_id']} | {row['parameter']} | {row['cluster_size']} | "
            f"{row['r531_training_ic']} | {row['r530_plateau_status']} |"
        )
    lines += [
        "",
        "## 核心表现",
        "",
        "| 区间 | 中性 IC | NW t | 日期数 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key in (("full", "full"), ("validation", "test"), ("recent", "recent")):
        stats = payload["core"]["stats"][key]
        lines.append(
            f"| {label} | {stats['directional_ic']} | {stats['nw_t']} | {stats['n_dates']} |"
        )
    lines += [
        "",
        "## 时间稳定性",
        "",
        "| 年份 | 中性 IC | 日期数 |",
        "| --- | ---: | ---: |",
    ]
    for row in payload["yearly_stability"]["rows"]:
        lines.append(
            f"| {row['year']} | {row['directional_ic']} | {row['n_dates']} |"
        )
    summary = payload["rolling_stability"]["summary"]
    lines += [
        "",
        f"- 完整滚动窗口：{summary['window_count']}",
        f"- 滚动窗口中位 IC：{_fmt(summary['median_ic'])}",
        f"- 正 IC 窗口比例：{summary['positive_ratio']}",
        f"- 最差滚动窗口 IC：{_fmt(summary['worst_ic'])}",
        f"- bootstrap CI 为正的窗口比例：{summary['bootstrap_positive_ratio']}",
        f"- 最差窗口 bootstrap CI 下限：{_fmt(summary['worst_bootstrap_ci_low'])}",
        "",
        "## 留一因子诊断",
        "",
        "| 移除因子 | full 配对差 | validation 配对差 | recent 配对差 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in payload["leave_one_out"]:
        delta = row["paired_delta_vs_core"]
        lines.append(
            f"| {row['removed']} | {_fmt(delta['full']['mean_delta'])} | "
            f"{_fmt(delta['test']['mean_delta'])} | {_fmt(delta['recent']['mean_delta'])} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 门槛：{payload['gates']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    if payload["negative_knowledge"]:
        lines += ["", "## 负知识", "", *[f"- {item}" for item in payload["negative_knowledge"]]]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R532 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    runner = Path(__file__).resolve()
    if payload.get("runner_sha256") != sha256(runner):
        raise RuntimeError("R532 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R532 preregistration already exists: {PREREG}")
    inputs = load_study_inputs(root)
    r530 = inputs["r530"]
    bundle = root / r530.PIT_BUNDLE.relative_to(ROOT)
    payload = {
        "schema_version": 1,
        "research_id": "R532",
        "research_type": RESEARCH_TYPE,
        "question": "Does the frozen R531 core retain positive, economically meaningful IC across years, rolling windows and the recent year?",
        "hypothesis": "The six-representative equal-weight core remains positive in most regimes without retuning factors.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": {
            "r530_result": sha256(root / R530_RESULT.relative_to(ROOT)),
            "r530_preregistration": sha256(root / R530_PREREG.relative_to(ROOT)),
            "r531_result": sha256(root / R531_RESULT.relative_to(ROOT)),
            "r531_preregistration": sha256(root / R531_PREREG.relative_to(ROOT)),
            "r526_membership": sha256(bundle / "daily_snapshots.parquet"),
            "r526_industry": sha256(bundle / "industry_history.parquet"),
        },
        "parameters": {
            "representatives": _representative_rows(inputs),
            "aggregation": "equal-weight percentile rank",
            "recluster": False,
            "retune_parameters": False,
            "rolling_window": ROLLING_WINDOW,
            "rolling_step": ROLLING_STEP,
            "rolling_block_size": ROLLING_BLOCK_SIZE,
            "final_mc": FINAL_MC,
            "block_sizes": list(BLOCK_SIZES),
            "seed": SEED,
        },
        "sample": {
            "start": str(START),
            "end": str(END),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
            "horizon_days": 5,
            "minimum_cross_section": 30,
            "min_rolling_windows": MIN_ROLLING_WINDOWS,
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "coverage_min": 0.90,
            "full_ic_min": 0.01,
            "validation_ic_positive": True,
            "recent_ic_positive": True,
            "yearly_positive_min": "4_of_5",
            "period_bootstrap_ci_low_positive": True,
            "rolling_windows_min": MIN_ROLLING_WINDOWS,
            "rolling_median_positive": True,
            "rolling_positive_ratio_min": 0.70,
            "rolling_worst_ic_min": -0.01,
            "rolling_bootstrap_positive_ratio_min": 0.70,
            "rolling_worst_bootstrap_ci_low_min": -0.01,
        },
        "falsification_checks": [
            "r531_frozen_core_hash_chain",
            "rolling_252d_step_21",
            "fixed_seed_block_bootstrap_10_20_40_5000",
            "fixed_seed_rolling_block20_5000",
            "leave_one_out_not_used_for_selection",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PREREG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    PREREG.with_name(PREREG.name + ".sha256").write_text(
        f"{sha256(PREREG)}  {PREREG.name}\n", encoding="ascii"
    )
    return PREREG


def publish_result(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    root = Path(root)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError(f"R532 result already exists: {RESULT}")
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": prereg["research_id"],
        "research_type": RESEARCH_TYPE,
        "runner": prereg["runner"],
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["gates"],
        "metrics": {
            "source_r530": payload["source_r530"],
            "source_r531": payload["source_r531"],
            "representatives": payload["representatives"],
            "sample": payload["sample"],
            "core": payload["core"],
            "yearly_stability": payload["yearly_stability"],
            "rolling_stability": payload["rolling_stability"],
            "leave_one_out": payload["leave_one_out"],
        },
        "source_result_hashes": [
            sha256(root / R530_RESULT.relative_to(ROOT)),
            sha256(root / R531_RESULT.relative_to(ROOT)),
        ],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r532_factor_library_core_stability_published",
    ).__dict__


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
        inputs = load_study_inputs(ROOT)
        print(json.dumps({
            "representatives": _representative_rows(inputs),
            "rolling_window": ROLLING_WINDOW,
            "rolling_step": ROLLING_STEP,
            "final_mc": FINAL_MC,
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R532 results already exist; refusing overwrite")
    payload, _inputs = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "gates": payload["gates"],
        "rolling_summary": payload["rolling_stability"]["summary"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
