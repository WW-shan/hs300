#!/usr/bin/env python3
"""R536: cluster R535 finalists and reapply BH-FDR at independent-cluster level."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backtest import stats_v2

RESEARCH_TYPE = "statistical_screen"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r536a_finalist_cluster_fdr_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R535_RESULT = RESEARCH / "r535a_full_family_census_result.json"
R535_PREREG = RESEARCH / "r535a_full_family_census_preregistration.json"
R536_ATTEMPT1 = RESEARCH / "r536_attempt1_nan_serialization_failure.json"
PREREG = RESEARCH / "r536a_finalist_cluster_fdr_preregistration.json"
RESULT = RESEARCH / "r536a_finalist_cluster_fdr_result.json"
MARKDOWN = RESEARCH / "r536a_finalist_cluster_fdr_result.md"
TRAIN_END = __import__("datetime").date(2024, 12, 31)
START = __import__("datetime").date(2022, 5, 5)
CORRELATION_THRESHOLD = 0.70
FDR_THRESHOLD = 0.10
MIN_MONTHS = 12


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


def load_finalists(root: Path) -> list[dict[str, Any]]:
    root = Path(root)
    verify_sidecar(root / R535_RESULT.relative_to(ROOT))
    verify_sidecar(root / R535_PREREG.relative_to(ROOT))
    r535 = _load_script_module("research_r535_full_family_census")
    source = json.loads((root / R535_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    family_trials = {
        row["family_id"]: row for row in r535.all_family_trials(root)
    }
    finalists = []
    for row in source["metrics"]["families"]:
        selected = row.get("selected")
        if selected is None:
            continue
        family_trial = family_trials[row["family_id"]]
        candidate = next(
            item["candidate"]
            for item in family_trial["parameters"]
            if abs(float(item["parameter"]) - float(selected["parameter"])) <= 1e-12
        )
        finalists.append({
            "family_id": row["family_id"],
            "family": row["family"],
            "tier": row["tier"],
            "selected": selected,
            "candidate": candidate,
        })
    finalists.sort(key=lambda row: row["family_id"])
    if len(finalists) != 22:
        raise RuntimeError(f"expected 22 R535 finalists, got {len(finalists)}")
    return finalists


def choose_cluster_representatives(
    clusters: list[list[str]], rows: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    representatives = []
    for cluster in clusters:
        candidates = [
            (family_id, rows[family_id])
            for family_id in cluster
            if family_id in rows
        ]
        if not candidates:
            continue
        family_id, _row = max(
            candidates,
            key=lambda item: (
                float(item[1].get("train_icir") or 0),
                float(item[1].get("train_ic") or 0),
                float(item[1].get("train_p05") or 0),
                -float(item[1].get("parameter") or 0),
            ),
        )
        representatives.append({
            "family_id": family_id,
            "cluster": sorted(cluster),
            "cluster_size": len(cluster),
            "selection_basis": "highest training ICIR; training IC/p05/parameter tie-break",
        })
    return sorted(representatives, key=lambda row: row["family_id"])


def run_cluster_study(root: Path) -> dict[str, Any]:
    root = Path(root)
    finalists = load_finalists(root)
    r535 = _load_script_module("research_r535_full_family_census")
    r530 = _load_script_module("research_r530_unified_factor_library_mc")
    r531 = _load_script_module("research_r531_factor_library_core")
    membership = r530.load_pit_membership(root)
    context = r530._load_context(root, membership)
    frames = {
        row["family_id"]: r530._factor_frame(
            row["candidate"], row["candidate"].parameter, context
        )
        for row in finalists
    }
    training_dates = r531.month_end_dates(membership, start=START, end=TRAIN_END)
    if len(training_dates) < MIN_MONTHS:
        raise RuntimeError(
            f"need at least {MIN_MONTHS} training month ends, got {len(training_dates)}"
        )
    pairs = r531.pairwise_factor_correlations(frames, membership, training_dates)
    usable_pairs = [row for row in pairs if row.get("rho") is not None]
    clusters = r531._union_find_clusters(
        sorted(frames),
        usable_pairs,
        CORRELATION_THRESHOLD,
    )
    rows = {row["family_id"]: row["selected"] for row in finalists}
    representatives = choose_cluster_representatives(clusters, rows)
    q_values = stats_v2.bh_fdr_qvalues([
        rows[row["family_id"]]["validation"]["p_value"]
        for row in representatives
    ])
    q_by_family = {
        row["family_id"]: q
        for row, q in zip(representatives, q_values, strict=True)
    }
    for representative in representatives:
        family_id = representative["family_id"]
        selected = rows[family_id]
        q_value = q_by_family[family_id]
        gates = r535.final_gate_results(selected, q_value=q_value)
        representative["parameter"] = selected["parameter"]
        representative["train_ic"] = selected["train_ic"]
        representative["train_icir"] = selected["train_icir"]
        representative["validation_ic"] = selected["validation_ic"]
        representative["recent_ic"] = selected["recent_ic"]
        representative["q_value"] = (
            round(float(q_value), 8) if q_value is not None else None
        )
        representative["gate_results"] = gates
        if all(gates.values()):
            representative["status"] = "admitted_for_research"
        elif selected.get("train_p05") is None or selected.get("full_ic") is None:
            representative["status"] = "inconclusive"
        else:
            representative["status"] = "rejected"
        representative["rejection_reasons"] = [
            key for key, value in gates.items() if not value
        ]
    counts = Counter(row["status"] for row in representatives)
    admitted = sorted(
        row["family_id"]
        for row in representatives
        if row["status"] == "admitted_for_research"
    )
    payload = {
        "schema_version": 1,
        "research_id": "R536A",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r535": R535_RESULT.relative_to(ROOT).as_posix(),
        "source_finalist_count": len(finalists),
        "training_month_end_count": len(training_dates),
        "correlation_threshold": CORRELATION_THRESHOLD,
        "correlation_pairs": pairs,
        "clusters": clusters,
        "representatives": representatives,
        "conclusion_counts": dict(sorted(counts.items())),
        "admitted_families": admitted,
        "conclusion": (
            "admitted_for_research" if admitted else "rejected"
        ),
        "known_limits": [
            "Clusters use training-period month-end factor ranks only.",
            "The 0.70 absolute-correlation threshold was fixed before validation outcomes.",
            "Representatives reuse R535A parameters and 5000-run training bootstrap evidence.",
            "This is a research factor selection, not a portfolio return or active strategy.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "R536 attempt 1 failed before publication because raw correlation NaN values reached the strict JSON ledger serializer; R536A maps non-finite values to null.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R536 全库 Finalist 独立簇与 FDR 复核",
        "",
        f"- R535 finalist：{payload['source_finalist_count']}",
        f"- 训练月末截面：{payload['training_month_end_count']}",
        f"- 相关性阈值：{payload['correlation_threshold']}",
        f"- 独立簇：{len(payload['clusters'])}",
        "",
        "## 代表因子",
        "",
        "| 簇 | 代表 | 参数 | 训练 IC | validation IC | recent IC | q | 结论 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["representatives"]:
        lines.append(
            f"| {', '.join(row['cluster'])} | {row['family_id']} | {row['parameter']} | "
            f"{row['train_ic']} | {row['validation_ic']} | {row['recent_ic']} | "
            f"{row['q_value']} | {row['status']} |"
        )
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
        raise FileNotFoundError(f"R536 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R536 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R536 preregistration already exists: {PREREG}")
    root = Path(root)
    finalists = load_finalists(root)
    payload = {
        "schema_version": 1,
        "research_id": "R536A",
        "research_type": RESEARCH_TYPE,
        "question": "After clustering the 22 R535 finalists by training-period correlation, which independent representatives survive cluster-level FDR and recent/validation gates?",
        "hypothesis": "Correlated price-risk and liquidity finalists collapse into a smaller independent set, avoiding the overly broad 22-way BH-FDR correction.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": {
            "r535_result": sha256(root / R535_RESULT.relative_to(ROOT)),
            "r535_preregistration": sha256(root / R535_PREREG.relative_to(ROOT)),
        "r536_attempt1_failure": sha256(root / R536_ATTEMPT1.relative_to(ROOT)),
            "r526_membership": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/daily_snapshots.parquet"),
            "r526_industry": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/industry_history.parquet"),
        },
        "parameters": {
            "source_finalists": [row["family_id"] for row in finalists],
            "correlation_threshold": CORRELATION_THRESHOLD,
            "training_end": str(TRAIN_END),
            "minimum_training_months": MIN_MONTHS,
            "fdr_threshold": FDR_THRESHOLD,
            "representative_rule": "highest train_icir, then train_ic, train_p05, parameter order",
        },
        "sample": {
            "start": str(START),
            "train_end": str(TRAIN_END),
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
            "training_only_correlation_clustering",
            "representative_selection_before_validation",
            "cluster_level_bh_fdr",
            "r535_source_hashes",
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


def sanitize_finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def publish_result(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    root = Path(root)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError(f"R536 result already exists: {RESULT}")
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    payload = sanitize_finite(payload)
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
            "finalist_count": payload["source_finalist_count"],
            "cluster_count": len(payload["clusters"]),
            "conclusion_counts": payload["conclusion_counts"],
        },
        "metrics": payload,
        "source_result_hashes": [
            sha256(root / R535_RESULT.relative_to(ROOT)),
            sha256(root / R535_PREREG.relative_to(ROOT)),
        ],
        "known_limits": payload["known_limits"],
        "negative_knowledge": [
            f"cluster_finalist_rejected:{row['family_id']}:{row['status']}"
            for row in payload["representatives"]
            if row["status"] != "admitted_for_research"
        ],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r536a_finalist_cluster_fdr_published",
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
        finalists = load_finalists(ROOT)
        print(json.dumps({
            "finalist_count": len(finalists),
            "families": [row["family_id"] for row in finalists],
            "correlation_threshold": CORRELATION_THRESHOLD,
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R536 results already exist; refusing overwrite")
    payload = run_cluster_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "cluster_count": len(payload["clusters"]),
        "conclusion_counts": payload["conclusion_counts"],
        "admitted_families": payload["admitted_families"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
