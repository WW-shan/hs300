#!/usr/bin/env python3
"""R535A: full 85-family parameter census with training-only selection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2

RESEARCH_TYPE = "statistical_screen"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r535a_full_family_census_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PARAMETER_ROOT = DATA / "factor_research" / "parameters"
PIT_BUNDLE = RESEARCH / "r526_unified_pit_bundle_window"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"
R530_PREREG = RESEARCH / "r530_unified_factor_library_mc_preregistration.json"
R535_ATTEMPT1 = RESEARCH / "r535_attempt1_stats_module_failure.json"
PREREG = RESEARCH / "r535a_full_family_census_preregistration.json"
RESULT = RESEARCH / "r535a_full_family_census_result.json"
MARKDOWN = RESEARCH / "r535a_full_family_census_result.md"
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MIN_CROSS_SECTION = 30
TRAIN_BOOTSTRAP_RUNS = 1000
FINAL_BOOTSTRAP_RUNS = 5000
TRAIN_BLOCK = 20
FINAL_BLOCKS = (10, 20, 40)
FDR_THRESHOLD = 0.10
SEED = 20261125
EXPECTED_FAMILIES = 85
EXPECTED_PARAMETERS = 472


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


def _tier_map(root: Path, r530: Any) -> dict[str, dict[str, str]]:
    tier_rows: dict[str, dict[str, str]] = {}
    for tier, path in r530.SOURCE_RESULTS:
        source = Path(root) / path.relative_to(ROOT)
        digest = verify_sidecar(source)
        payload = json.loads(source.read_text(encoding="utf-8"))
        for family_id in payload["families"]:
            if family_id in tier_rows:
                raise RuntimeError(f"family appears in multiple source tiers: {family_id}")
            tier_rows[family_id] = {
                "tier": tier,
                "source_result": path.relative_to(ROOT).as_posix(),
                "source_result_sha256": digest,
            }
    return tier_rows


def all_family_trials(root: Path) -> list[dict[str, Any]]:
    root = Path(root)
    r530 = _load_script_module("research_r530_unified_factor_library_mc")
    window, threshold = r530._registry()
    family_by_id = {**window, **threshold}
    tiers = _tier_map(root, r530)
    missing = sorted(set(family_by_id) - set(tiers))
    if missing:
        raise RuntimeError(f"families missing source tier: {missing}")
    result = []
    for family_id in sorted(family_by_id):
        family = family_by_id[family_id]
        tier = tiers[family_id]
        parameters = []
        for parameter in family["parameter_grid"]:
            candidate = r530.Candidate(
                source_tier=tier["tier"],
                source_result=tier["source_result"],
                source_result_sha256=tier["source_result_sha256"],
                family_id=family_id,
                family=family,
                parameter=parameter,
                source_recommended_parameter=family["current_parameter"],
                source_recommended_trial={},
            )
            parameters.append({"parameter": parameter, "candidate": candidate})
        result.append({
            "family_id": family_id,
            "family": family,
            "tier": tier["tier"],
            "parameters": parameters,
        })
    if len(result) != EXPECTED_FAMILIES:
        raise RuntimeError(f"expected {EXPECTED_FAMILIES} families, got {len(result)}")
    total = sum(len(row["parameters"]) for row in result)
    if total != EXPECTED_PARAMETERS:
        raise RuntimeError(f"expected {EXPECTED_PARAMETERS} parameter points, got {total}")
    return result


def training_neighbours(grid: list[float], parameter: float) -> list[float]:
    ordered = sorted(float(value) for value in grid)
    if len(ordered) < 2:
        return []
    index = next(
        i for i, value in enumerate(ordered)
        if abs(value - float(parameter)) <= 1e-12
    )
    if len(ordered) == 2:
        return [ordered[1 - index]]
    if index == 0:
        return ordered[1:3]
    if index == len(ordered) - 1:
        return ordered[-3:-1]
    return [ordered[index - 1], ordered[index + 1]]


def training_eligible(row: dict[str, Any]) -> bool:
    return (
        isinstance(row.get("coverage"), (int, float))
        and row["coverage"] >= 0.90
        and isinstance(row.get("train_ic"), (int, float))
        and row["train_ic"] >= 0.01
        and isinstance(row.get("train_p05"), (int, float))
        and row["train_p05"] > 0
        and row.get("plateau_ok") is True
    )


def select_family_parameter(
    rows: list[dict[str, Any]], *, current_parameter: float
) -> dict[str, Any] | None:
    eligible = [row for row in rows if training_eligible(row)]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            float(row["train_icir"]),
            float(row["train_ic"]),
            float(row["train_p05"]),
            -abs(float(row["parameter"]) - float(current_parameter)),
            -float(row["parameter"]),
        ),
    )


def final_gate_results(row: dict[str, Any], *, q_value: float | None) -> dict[str, bool]:
    bootstrap = row.get("final_bootstrap") or {}
    block20 = bootstrap.get("20") or {}
    block10 = bootstrap.get("10") or {}
    block40 = bootstrap.get("40") or {}
    return {
        "coverage_ge_0_90": isinstance(row.get("coverage"), (int, float))
        and row["coverage"] >= 0.90,
        "train_ic_ge_0_01": isinstance(row.get("train_ic"), (int, float))
        and row["train_ic"] >= 0.01,
        "train_block20_ci_positive": isinstance(block20.get("ci_low"), (int, float))
        and block20["ci_low"] > 0,
        "full_ic_ge_0_01": isinstance(row.get("full_ic"), (int, float))
        and row["full_ic"] >= 0.01,
        "validation_ic_positive": isinstance(row.get("validation_ic"), (int, float))
        and row["validation_ic"] > 0,
        "recent_ic_positive": isinstance(row.get("recent_ic"), (int, float))
        and row["recent_ic"] > 0,
        "fdr_q_le_0_10": isinstance(q_value, (int, float)) and q_value <= FDR_THRESHOLD,
        "training_plateau": row.get("plateau_ok") is True,
        "block10_40_mean_positive": all(
            isinstance(item.get("mean"), (int, float)) and item["mean"] > 0
            for item in (block10, block40)
        ),
    }


def bootstrap_seed(family_id: str, parameter: float, block: int, runs: int) -> int:
    token = hashlib.sha256(
        f"R535:{family_id}:{parameter}:{block}:{runs}:{SEED}".encode()
    ).hexdigest()
    return int(token[:8], 16)


def _training_trial(
    candidate: Any,
    context: Any,
    membership: pl.DataFrame,
) -> tuple[dict[str, Any], pl.DataFrame]:
    r530 = _load_script_module("research_r530_unified_factor_library_mc")
    joined, coverage = r530._evaluation_frame(
        candidate, candidate.parameter, context, membership
    )
    gate_ic = context.p3._neutralized_ic(
        joined,
        ["industry", "size"],
        minimum_cross_section=MIN_CROSS_SECTION,
    )
    sign = -1 if candidate.family["direction"] == "low" else 1
    block = context.p3._ic_block(gate_ic, START, END, sign)
    train_values = (
        gate_ic.filter((pl.col("date") >= START) & (pl.col("date") <= TRAIN_END))["ic"]
        * sign
    ).to_numpy()
    train_icir = (
        float(train_values.mean() / train_values.std())
        if train_values.size > 1 and float(train_values.std()) > 1e-12
        else 0.0
    )
    train_bootstrap = context.p3._bootstrap_ic_or_empty(
        gate_ic,
        START,
        TRAIN_END,
        sign,
        block_size=TRAIN_BLOCK,
        n_bootstrap=TRAIN_BOOTSTRAP_RUNS,
        seed=bootstrap_seed(
            candidate.family_id,
            candidate.parameter,
            TRAIN_BLOCK,
            TRAIN_BOOTSTRAP_RUNS,
        ),
    )
    row = {
        "parameter": candidate.parameter,
        "source_tier": candidate.source_tier,
        "source_result": candidate.source_result,
        "source_result_sha256": candidate.source_result_sha256,
        "coverage": coverage["factor_non_null_ratio"],
        "coverage_detail": coverage,
        "full": block["full"],
        "train": block["train"],
        "validation": block["test"],
        "recent": block["recent"],
        "train_ic": block["train"]["directional_ic"],
        "train_icir": round(train_icir, 6),
        "train_p05": train_bootstrap.get("ci_low"),
        "train_bootstrap": train_bootstrap,
        "plateau_ok": False,
        "plateau_status": "pending",
        "neighbours": [],
    }
    return row, gate_ic


def assign_training_plateau(rows: list[dict[str, Any]]) -> None:
    by_parameter = {float(row["parameter"]): row for row in rows}
    grid = sorted(by_parameter)
    for row in rows:
        parameter = float(row["parameter"])
        neighbours = training_neighbours(grid, parameter)
        row["neighbours"] = neighbours
        if len(grid) < 3:
            row["plateau_ok"] = True
            row["plateau_status"] = "not_applicable_small_grid"
            continue
        ok = all(
            float(by_parameter[value]["coverage"]) >= 0.90
            and float(by_parameter[value]["train_ic"]) > 0
            for value in neighbours
        )
        row["plateau_ok"] = ok
        row["plateau_status"] = "training_neighbour_positive" if ok else "not_robust"


def _public_trial(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"train_bootstrap"}
    }


def scan_family(
    family_row: dict[str, Any],
    context: Any,
    membership: pl.DataFrame,
) -> dict[str, Any]:
    rows = []
    errors = []
    for parameter_row in family_row["parameters"]:
        candidate = parameter_row["candidate"]
        try:
            row, _ = _training_trial(candidate, context, membership)
        except Exception as exc:  # noqa: BLE001 - missing provider fields are explicit data gaps
            errors.append({
                "parameter": parameter_row["parameter"],
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            })
            continue
        rows.append(row)
    if not rows:
        return {
            "family_id": family_row["family_id"],
            "tier": family_row["tier"],
            "family": family_row["family"],
            "status": "data_gap",
            "errors": errors,
            "trials": [],
            "selected": None,
        }
    assign_training_plateau(rows)
    selected = select_family_parameter(
        rows,
        current_parameter=family_row["family"]["current_parameter"],
    )
    if selected is None:
        return {
            "family_id": family_row["family_id"],
            "tier": family_row["tier"],
            "family": family_row["family"],
            "status": "rejected",
            "reason": "no_training_candidate",
            "errors": errors,
            "trials": [_public_trial(row) for row in rows],
            "selected": None,
        }
    candidate = next(
        item["candidate"]
        for item in family_row["parameters"]
        if abs(float(item["parameter"]) - float(selected["parameter"])) <= 1e-12
    )
    final_trial, gate_ic = _training_trial(candidate, context, membership)
    final_trial["plateau_ok"] = selected["plateau_ok"]
    final_trial["plateau_status"] = selected["plateau_status"]
    final_trial["neighbours"] = selected["neighbours"]
    final_trial["train_icir"] = selected["train_icir"]
    sign = -1 if candidate.family["direction"] == "low" else 1
    final_bootstrap = {
        str(block): context.p3._bootstrap_ic_or_empty(
            gate_ic,
            START,
            TRAIN_END,
            sign,
            block_size=block,
            n_bootstrap=FINAL_BOOTSTRAP_RUNS,
            seed=bootstrap_seed(
                family_row["family_id"],
                candidate.parameter,
                block,
                FINAL_BOOTSTRAP_RUNS,
            ),
        )
        for block in FINAL_BLOCKS
    }
    selected_row = {
        **final_trial,
        "parameter": candidate.parameter,
        "train_ic": final_trial["train"]["directional_ic"],
        "full_ic": final_trial["full"]["directional_ic"],
        "validation_ic": final_trial["validation"]["directional_ic"],
        "recent_ic": final_trial["recent"]["directional_ic"],
        "final_bootstrap": final_bootstrap,
    }
    return {
        "family_id": family_row["family_id"],
        "tier": family_row["tier"],
        "family": family_row["family"],
        "status": "finalist_pending_fdr",
        "errors": errors,
        "trials": [_public_trial(row) for row in rows],
        "selected": selected_row,
    }


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    r530 = _load_script_module("research_r530_unified_factor_library_mc")
    family_trials = all_family_trials(root)
    membership = r530.load_pit_membership(root)
    context = r530._load_context(root, membership)
    families = []
    for index, family_row in enumerate(family_trials, start=1):
        print(f"[{index}/{len(family_trials)}] {family_row['family_id']}", flush=True)
        families.append(scan_family(family_row, context, membership))
    finalists = [
        row for row in families
        if row.get("selected") is not None
    ]
    q_values = stats_v2.bh_fdr_qvalues(
        [
            row["selected"]["validation"]["p_value"]
            for row in finalists
        ]
    )
    q_by_family = {
        row["family_id"]: q
        for row, q in zip(finalists, q_values, strict=True)
    }
    for row in families:
        if row.get("selected") is None:
            continue
        q_value = q_by_family[row["family_id"]]
        gates = final_gate_results(row["selected"], q_value=q_value)
        row["selected"]["q_value"] = (
            round(float(q_value), 8) if q_value is not None else None
        )
        row["selected"]["gate_results"] = gates
        pos = row["selected"]
        if all(gates.values()):
            row["status"] = "admitted_for_research"
            pos["conclusion"] = "admitted_for_research"
        elif pos.get("train_p05") is None or pos.get("full_ic") is None:
            row["status"] = "inconclusive"
            pos["conclusion"] = "inconclusive"
        else:
            row["status"] = "rejected"
            pos["conclusion"] = "rejected"
        pos["rejection_reasons"] = [
            key for key, value in gates.items() if not value
        ]
    counts = Counter(row["status"] for row in families)
    payload = {
        "schema_version": 1,
        "research_id": "R535A",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "library_census": {
            "family_count": len(family_trials),
            "parameter_point_count": sum(
                len(row["parameters"]) for row in family_trials
            ),
            "source_tiers": dict(sorted(Counter(row["tier"] for row in family_trials).items())),
        },
        "families": families,
        "conclusion": (
            "admitted_for_research"
            if counts.get("admitted_for_research", 0)
            else "rejected"
            if counts.get("rejected", 0)
            else "data_gap"
            if counts.get("data_gap", 0)
            else "inconclusive"
        ),
        "conclusion_counts": dict(sorted(counts.items())),
        "admitted_families": sorted(
            row["family_id"] for row in families if row["status"] == "admitted_for_research"
        ),
        "known_limits": [
            "All parameter choices use training-period evidence only; validation/recent are confirmation windows.",
            "Block 10/20/40 are sensitivity checks; only block 20 CI is a hard significance gate.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "A statistical admission is not a portfolio return or production authorization.",
            "R535 attempt 1 failed before publication because the runner referenced a non-exported stats module; R535A is the explicit amendment.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload, context


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["conclusion_counts"]
    lines = [
        "# R535A 全参数族普查与训练期参数筛选",
        "",
        "## 规模",
        "",
        f"- 参数族：{payload['library_census']['family_count']}",
        f"- 参数点：{payload['library_census']['parameter_point_count']}",
        f"- 结论计数：{counts}",
        "",
        "## 研究准入",
        "",
        "| 家族 | 数据类 | 参数 | 训练 IC | validation IC | recent IC | q | 训练 block20 CI low |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    admitted = [
        row for row in payload["families"]
        if row["status"] == "admitted_for_research"
    ]
    if admitted:
        for row in admitted:
            selected = row["selected"]
            lines.append(
                f"| {row['family_id']} | {row['family']['data_class']} | "
                f"{selected['parameter']} | {selected['train_ic']} | "
                f"{selected['validation_ic']} | {selected['recent_ic']} | "
                f"{selected['q_value']} | {selected['final_bootstrap']['20']['ci_low']} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — | — |")
    lines += [
        "",
        "## 数据缺口",
        "",
    ]
    gaps = [row for row in payload["families"] if row["status"] == "data_gap"]
    lines += [f"- {row['family_id']}" for row in gaps] or ["- 无"]
    lines += [
        "",
        "## 结论",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 准入家族：{', '.join(payload['admitted_families']) if payload['admitted_families'] else '无'}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R535 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R535 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R535 preregistration already exists: {PREREG}")
    root = Path(root)
    r530 = _load_script_module("research_r530_unified_factor_library_mc")
    families = all_family_trials(root)
    input_hashes = {
        "families": sha256(root / PARAMETER_ROOT.relative_to(ROOT) / "families.json"),
        "thresholds": sha256(root / PARAMETER_ROOT.relative_to(ROOT) / "thresholds.json"),
        "r530_result": sha256(root / R530_RESULT.relative_to(ROOT)),
        "r530_preregistration": sha256(root / R530_PREREG.relative_to(ROOT)),
        "r535_attempt1_failure": sha256(root / R535_ATTEMPT1.relative_to(ROOT)),
        "r526_membership": sha256(root / PIT_BUNDLE.relative_to(ROOT) / "daily_snapshots.parquet"),
        "r526_industry": sha256(root / PIT_BUNDLE.relative_to(ROOT) / "industry_history.parquet"),
    }
    for tier, path in r530.SOURCE_RESULTS:
        input_hashes[f"{tier.lower()}_historical_source"] = sha256(
            root / path.relative_to(ROOT)
        )
    payload = {
        "schema_version": 1,
        "research_id": "R535A",
        "research_type": RESEARCH_TYPE,
        "question": "Which of the 85 registered parameter families survives training-only parameter selection and unified-PIT out-of-sample confirmation?",
        "hypothesis": "A full family census will recover more independent research exposures than the 15 historical recommended parameters alone.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": input_hashes,
        "parameters": {
            "family_count": len(families),
            "parameter_point_count": sum(len(row["parameters"]) for row in families),
            "training_bootstrap_runs": TRAIN_BOOTSTRAP_RUNS,
            "final_bootstrap_runs": FINAL_BOOTSTRAP_RUNS,
            "training_block": TRAIN_BLOCK,
            "final_blocks": list(FINAL_BLOCKS),
            "seed": SEED,
            "selection_rule": [
                "training_icir_desc",
                "training_ic_desc",
                "training_block20_ci_desc",
                "distance_to_current_parameter_asc",
                "parameter_asc",
            ],
        },
        "sample": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "horizon_days": 5,
            "minimum_cross_section": MIN_CROSS_SECTION,
            "neutralization": "industry+size",
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "coverage_min": 0.90,
            "train_ic_min": 0.01,
            "train_block20_ci_positive": True,
            "full_ic_min": 0.01,
            "validation_ic_positive": True,
            "recent_ic_positive": True,
            "fdr_q_max": FDR_THRESHOLD,
            "training_plateau": True,
            "block10_40_mean_positive": True,
        },
        "falsification_checks": [
            "training_only_parameter_selection",
            "training_neighbour_plateau",
            "fixed_seed_bootstrap",
            "validation_fdr",
            "recent_not_used_in_parameter_order",
            "r526_pit_membership_filter",
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
        raise FileExistsError(f"R535 result already exists: {RESULT}")
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
        "gate_results": {
            "family_count": payload["library_census"]["family_count"],
            "parameter_point_count": payload["library_census"]["parameter_point_count"],
            "conclusion_counts": payload["conclusion_counts"],
        },
        "metrics": payload,
        "source_result_hashes": sorted(
            prereg["input_hashes"][f"{tier.lower()}_historical_source"]
            for tier in ("P1", "P2", "P3", "P4")
        ),
        "known_limits": payload["known_limits"],
        "negative_knowledge": [
            f"{row['family_id']}:{row['status']}"
            for row in payload["families"]
            if row["status"] in {"rejected", "data_gap", "inconclusive"}
        ],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r535a_full_family_census_published",
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
        families = all_family_trials(ROOT)
        print(json.dumps({
            "family_count": len(families),
            "parameter_point_count": sum(len(row["parameters"]) for row in families),
            "tiers": dict(sorted(Counter(row["tier"] for row in families).items())),
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R535 results already exist; refusing overwrite")
    payload, _context = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "conclusion_counts": payload["conclusion_counts"],
        "admitted_families": payload["admitted_families"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
