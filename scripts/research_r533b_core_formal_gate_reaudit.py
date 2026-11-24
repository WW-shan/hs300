#!/usr/bin/env python3
"""R533B: correct v2 gate metric names against the frozen R533A formal result."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.research.gate_policy_v2 import evaluate_gate_policy_v2

RESEARCH_TYPE = "derived_analysis_only"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/"
    "r533b_core_formal_gate_reaudit_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
SOURCE = RESEARCH / "r533a_core_formal_portfolio_result.json"
SOURCE_PREREG = RESEARCH / "r533a_core_formal_portfolio_preregistration.json"
ATTEMPT1 = RESEARCH / "r533_attempt1_signature_mismatch_failure.json"
GATE_POLICY = (
    DATA
    / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/gate_policy_v2.json"
)
PREREG = RESEARCH / "r533b_core_formal_gate_reaudit_preregistration.json"
RESULT = RESEARCH / "r533b_core_formal_gate_reaudit_result.json"
MARKDOWN = RESEARCH / "r533b_core_formal_gate_reaudit_result.md"


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


def load_source(root: Path) -> dict[str, Any]:
    root = Path(root)
    for relative in (SOURCE, SOURCE_PREREG, ATTEMPT1, GATE_POLICY):
        verify_sidecar(root / relative.relative_to(ROOT))
    source = json.loads((root / SOURCE.relative_to(ROOT)).read_text(encoding="utf-8"))
    if source.get("research_type") != "project_formal":
        raise RuntimeError("R533A source is not project_formal")
    if source.get("conclusion") != "rejected":
        raise RuntimeError("R533A source conclusion changed")
    evidence = source.get("formal_evidence") or {}
    if (
        evidence.get("persistence") != "saved"
        or evidence.get("lookahead") != "passed"
        or evidence.get("execution_timing") != "passed"
    ):
        raise RuntimeError("R533A source formal evidence is incomplete")
    return source


def _normalise_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(value)
    out["annual"] = value.get("annual", value.get("annual_return"))
    out["mdd"] = value.get("mdd", value.get("max_drawdown"))
    return out


def normalize_gate_candidate(source: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(source)
    for key in ("main_2x", "stress_3x", "extreme_5x"):
        if isinstance(candidate.get(key), dict):
            candidate[key] = _normalise_snapshot(candidate[key])
    return candidate


def failed_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.extend(failed_leaf_paths(item, path))
    elif isinstance(value, bool) and not value:
        paths.append(prefix)
    return paths


def build_reaudit(root: Path) -> dict[str, Any]:
    source = load_source(root)
    source_candidate = source["gate_results"]["candidate"]
    corrected = normalize_gate_candidate(source_candidate)
    evaluation = evaluate_gate_policy_v2(corrected, policy_path=GATE_POLICY)
    validation_annual = (
        source["metrics"]["confirmation"]["validation"]["stats"]["annual_return"]
    )
    validation_positive = isinstance(validation_annual, (int, float)) and validation_annual > 0
    conclusion = (
        "watch_only"
        if evaluation["status"] == "qualified_for_paper_review" and validation_positive
        else "rejected"
    )
    failed = failed_leaf_paths(evaluation["gates"])
    if not validation_positive:
        failed.append("validation_annual_positive")
    return {
        "schema_version": 1,
        "research_id": "R533B",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r533a": SOURCE.relative_to(ROOT).as_posix(),
        "source_r533a_sha256": sha256(SOURCE),
        "source_preregistration_sha256": sha256(SOURCE_PREREG),
        "gate_policy": GATE_POLICY.relative_to(ROOT).as_posix(),
        "gate_policy_sha256": sha256(GATE_POLICY),
        "mapping_correction": {
            "annual_return_to": "annual",
            "max_drawdown_to": "mdd",
            "applied_to": ["main_2x", "stress_3x", "extreme_5x"],
        },
        "source_conclusion": source["conclusion"],
        "source_selected": source["metrics"]["selected"],
        "validation_annual_return": validation_annual,
        "validation_positive": validation_positive,
        "corrected_candidate": corrected,
        "evaluation": evaluation,
        "failed_gate_paths": failed,
        "conclusion": conclusion,
        "negative_knowledge": failed,
        "known_limits": [
            "This is a frozen-result gate-key correction, not a new backtest.",
            "No factor, execution parameter, window, cost or gate threshold changed.",
            "R533A original result and rejection remain immutable.",
            "The corrected decision still depends on the recent-year and stress gates.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "formal_evidence": source["formal_evidence"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    evaluation = payload["evaluation"]
    lines = [
        "# R533B 正式门禁字段重审",
        "",
        "本轮只修正 `annual_return -> annual`、`max_drawdown -> mdd` 的映射，不重跑、不改参数。",
        "",
        f"- R533A 原结论：{payload['source_conclusion']}",
        f"- 重审结论：{payload['conclusion']}",
        f"- v2 状态：{evaluation['status']}",
        f"- 失败路径：{payload['failed_gate_paths']}",
        f"- 验证期年化：{payload['validation_annual_return']}",
        "",
        "## 关键指标",
        "",
        "| 项目 | 年化 | Sharpe | MDD | 近一年 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (
        ("main_2x", "主组合 2x"),
        ("control_2x", "两因子对照 2x"),
        ("stress_3x", "3x 压力"),
        ("extreme_5x", "5x 诊断"),
    ):
        row = payload["corrected_candidate"][key]
        lines.append(
            f"| {label} | {row.get('annual')} | {row.get('sharpe')} | "
            f"{row.get('mdd')} | {row.get('recent')} |"
        )
    lines += [
        "",
        "## 诊断",
        "",
        f"- 5× warnings：{evaluation['diagnostics']['5x_warnings']}",
        f"- 5× 硬否决：{evaluation['diagnostics']['5x_hard_rejection']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    return "\n".join(lines) + "\n"


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R533B preregistration already exists: {PREREG}")
    source = load_source(root)
    payload = {
        "schema_version": 1,
        "research_id": "R533B",
        "research_type": RESEARCH_TYPE,
        "question": "Does the frozen R533A formal candidate pass gate-policy-v2 after correcting only its metric-key mapping?",
        "hypothesis": "R533A's rejection was partially caused by annual_return/max_drawdown keys not matching the v2 annual/mdd schema.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_frozen_r533a_result",
        "input_hashes": {
            "source_result": sha256(root / SOURCE.relative_to(ROOT)),
            "source_preregistration": sha256(root / SOURCE_PREREG.relative_to(ROOT)),
            "attempt1_failure": sha256(root / ATTEMPT1.relative_to(ROOT)),
            "gate_policy_v2": sha256(root / GATE_POLICY.relative_to(ROOT)),
        },
        "parameters": {
            "metric_mapping": {
                "annual_return": "annual",
                "max_drawdown": "mdd",
            },
            "source_selected": source["metrics"]["selected"],
            "rerun_backtest": False,
            "retune_parameters": False,
        },
        "sample": {
            "source_start": source["metrics"]["selected"]["start"],
            "source_end": source["metrics"]["selected"]["end"],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "gate_policy_v2": GATE_POLICY.relative_to(ROOT).as_posix(),
            "validation_annual_positive": True,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "source_result_sidecar",
            "source_formal_evidence_saved_passed_passed",
            "metric_key_mapping_only",
            "no_backtest_rerun",
            "five_x_warning_only",
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
        raise FileExistsError(f"R533B result already exists: {RESULT}")
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
        "gate_results": payload["evaluation"]["gates"],
        "metrics": {
            "source_conclusion": payload["source_conclusion"],
            "source_selected": payload["source_selected"],
            "mapping_correction": payload["mapping_correction"],
            "corrected_candidate": payload["corrected_candidate"],
            "evaluation": payload["evaluation"],
            "failed_gate_paths": payload["failed_gate_paths"],
            "validation_annual_return": payload["validation_annual_return"],
            "validation_positive": payload["validation_positive"],
        },
        "source_result_hashes": [
            payload["source_r533a_sha256"],
            payload["source_preregistration_sha256"],
        ],
        "formal_evidence": payload["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r533b_core_formal_gate_reaudit_published",
    ).__dict__


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R533B preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R533B runner hash does not match frozen preregistration")


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
        source = load_source(ROOT)
        print(json.dumps({
            "source_conclusion": source["conclusion"],
            "source_selected": source["metrics"]["selected"],
            "validation_annual_return": source["metrics"]["confirmation"]["validation"]["stats"]["annual_return"],
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R533B results already exist; refusing overwrite")
    payload = build_reaudit(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "status": payload["evaluation"]["status"],
        "failed_gate_paths": payload["failed_gate_paths"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
