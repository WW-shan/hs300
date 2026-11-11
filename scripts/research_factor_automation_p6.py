#!/usr/bin/env python3
"""P6 automatic discovery, readiness, queueing, and ledger binding."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.factor_research.audit import load_audit
from app.factor_research.automation import (
    audit_research_bindings,
    bind_research_evidence,
    build_automation_payload,
)
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import hash_token, sha256_file, utc_now
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.parameters import load_parameter_registry
from app.factor_research.threshold_parameters import load_threshold_registry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
AUTOMATION_DIR = DATA / "factor_research" / "automation"
CONFIG_FINGERPRINTS = AUTOMATION_DIR / "config_fingerprints.json"
LATEST_PATH = AUTOMATION_DIR / "latest.json"
OPERATION = "factor_research_automation_published"
RESEARCH_PATHS = {
    "p1": (
        "factor_parameter_mc_published",
        DATA / "factor_research" / "research" / "parameters" / "p1_parameter_mc_2026-11-11.json",
    ),
    "p2": (
        "factor_parameter_mc_published",
        DATA / "factor_research" / "research" / "parameters" / "p2_builtin_parameter_mc_2026-11-11_v2.json",
    ),
    "p3": (
        "factor_parameter_mc_published",
        DATA / "factor_research" / "research" / "parameters" / "p3_extended_parameter_mc_2026-11-11_v3.json",
    ),
    "p4": (
        "factor_parameter_mc_published",
        DATA / "factor_research" / "research" / "parameters" / "p4_threshold_parameter_mc_2026-11-11.json",
    ),
    "p5": (
        "factor_increment_scan_published",
        DATA / "factor_research" / "research" / "p5_incremental_2026-11-12.json",
    ),
    "p8": (
        "financial_increment_scan_published",
        DATA / "factor_research" / "research" / "p8_financial_increment_2026-11-12.json",
    ),
    "p9": (
        "financial_regime_scan_published",
        DATA
        / "factor_research"
        / "research"
        / "p9_financial_regime_2026-11-12_v3.json",
    ),
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON: {path}")
    return payload


def _load_research_payloads() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    payloads: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    for name, (operation, path) in RESEARCH_PATHS.items():
        payload = _load_json(path)
        payloads[name] = payload
        bindings.append(
            {
                "name": name,
                "operation": operation,
                "research_id": payload["research_id"],
                "evidence_paths": [path, path.with_suffix(".md")],
            }
        )
    return payloads, bindings


def _latest_audit(data_dir: Path) -> dict[str, Any] | None:
    manifests = sorted((data_dir / "factor_research" / "audits").glob("*/manifest.json"))
    if not manifests:
        return None
    ranked: list[tuple[str, str, Path]] = []
    for path in manifests:
        try:
            manifest = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        ranked.append(
            (
                str(manifest.get("generated_at") or ""),
                str(manifest.get("audit_id") or path.parent.name),
                path,
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    audit_id = str(_load_json(ranked[-1][2]).get("audit_id") or "")
    return load_audit(data_dir, audit_id) if audit_id else None


def _extension_config_sha256(data_dir: Path) -> str:
    fingerprints = _load_json(CONFIG_FINGERPRINTS)
    extensions = fingerprints.get("extensions")
    if not isinstance(extensions, dict):
        raise ValueError("extension fingerprint manifest is malformed")
    expected = extensions.get("alt_hs300")
    if not isinstance(expected, str):
        raise ValueError("alt_hs300 extension fingerprint is missing")
    actual_path = data_dir / "ext_data" / "alt_hs300" / "config.json"
    if actual_path.exists():
        actual = sha256_file(actual_path)
        if actual != expected:
            raise RuntimeError(
                "alt_hs300 config fingerprint drift: "
                f"manifest={expected} actual={actual}"
            )
    return expected


def _previous_payload(data_dir: Path) -> dict[str, Any] | None:
    if not LATEST_PATH.exists():
        return None
    latest = _load_json(LATEST_PATH)
    run_json = latest.get("run_json")
    if not isinstance(run_json, str):
        raise ValueError("automation latest manifest is malformed")
    run_path = data_dir / run_json
    if not run_path.exists():
        raise FileNotFoundError(run_path)
    return _load_json(run_path)


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    classification = payload["classification"]
    audit = payload["audit"]
    lines = [
        "# P6 因子研究自动闭环报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- Catalog：`{payload['catalog']['snapshot_id']}`",
        f"- 因子数：{summary['factor_count']}",
        f"- 未分类：{classification['unclassified_factor_ids']}",
        f"- 审计：`{audit['audit_id']}`；配置同步：{audit['config_current']}",
        f"- scan-ready：{summary['scan_ready_factor_count']}",
        f"- portfolio-ready：{summary['portfolio_ready_factor_count']}",
        f"- 待跑任务：{summary['pending_task_count']}",
        f"- 阻断任务：{summary['blocked_task_count']}",
        "- `active_version`：None；不生成订单或纸面目标。",
        "",
        "## 研究覆盖",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for status, count in payload["coverage"]["status_counts"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## 待跑与阻断任务",
            "",
            "| 任务 | 类型 | 状态 | 原因 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for task in payload["pending_tasks"]:
        reasons = task.get("reasons")
        if not reasons:
            reasons = sorted(
                {
                    reason
                    for values in task.get("reasons_by_factor", {}).values()
                    for reason in values
                }
            )
        lines.append(
            f"| `{task['task_id']}` | {task['kind']} | {task['status']} | "
            f"{', '.join(reasons) if reasons else '-'} |"
        )
    lines.extend(
        [
            "",
            "## 负知识",
            "",
            f"本轮记录 {len(payload['negative_knowledge'])} 条阻断原因；审计指纹过期时，"
            "相关扩展因子不会进入组合准入。",
            "",
            "## 边界",
            "",
            "- 当前 HS300 名单回填历史存在幸存者偏差。",
            "- 本报告只生成研究与待跑任务，不自动修改因子定义或创建 active 版本。",
            "",
        ]
    )
    return "\n".join(lines)


def _publish_run(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path]:
    token = hash_token(payload["research_id"])
    run_json = AUTOMATION_DIR / "runs" / f"{token}.json"
    run_markdown = run_json.with_suffix(".md")
    if run_json.exists():
        published = _load_json(run_json)
        if published.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"automation run identity mismatch: {run_json}")
        if not run_markdown.exists():
            raise FileNotFoundError(run_markdown)
        return published, run_json, run_markdown
    atomic_write_json(run_json, payload)
    atomic_write_text(run_markdown, _render_markdown(payload))
    return payload, run_json, run_markdown


def _bind_p6(
    payload: dict[str, Any],
    run_json: Path,
    run_markdown: Path,
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    record, _created = bind_research_evidence(
        DATA,
        operation=OPERATION,
        evidence_paths=[run_json, run_markdown],
        research_id=payload["research_id"],
        payload={
            "catalog_snapshot_id": payload["catalog"]["snapshot_id"],
            "input_fingerprints": {
                "catalog_snapshot_id": payload["catalog"]["snapshot_id"],
                "catalog_registry_fingerprint": payload["catalog"]["registry_fingerprint"],
                "audit_id": payload["audit"]["audit_id"],
                "p1_research_id": payloads["p1"]["research_id"],
                "p2_research_id": payloads["p2"]["research_id"],
                "p3_research_id": payloads["p3"]["research_id"],
                "p4_research_id": payloads["p4"]["research_id"],
                "p5_research_id": payloads["p5"]["research_id"],
                "p8_research_id": payloads["p8"]["research_id"],
                "p9_research_id": payloads["p9"]["research_id"],
            },
            "metrics": payload["summary"],
            "verdict": "watch_only_no_active_version",
            "reason": None,
            "negative_knowledge": json.dumps(
                payload["negative_knowledge"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )
    return {
        "record_id": record.record_id,
        "record_hash": record.record_hash,
    }


def _publish_latest(
    payload: dict[str, Any],
    run_json: Path,
    run_markdown: Path,
    ledger: dict[str, Any],
) -> None:
    pointer = {
        "schema_version": 1,
        "research_id": payload["research_id"],
        "catalog_snapshot_id": payload["catalog"]["snapshot_id"],
        "generated_at": payload["generated_at"],
        "run_json": run_json.relative_to(DATA).as_posix(),
        "run_markdown": run_markdown.relative_to(DATA).as_posix(),
        "ledger_record_id": ledger["record_id"],
        "ledger_record_hash": ledger["record_hash"],
    }
    if LATEST_PATH.exists() and _load_json(LATEST_PATH) == pointer:
        return
    atomic_write_json(LATEST_PATH, pointer)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    window_registry = load_parameter_registry(DATA)
    threshold_registry = load_threshold_registry(DATA)
    payloads, declarations = _load_research_payloads()
    ledger_bindings = audit_research_bindings(DATA, declarations)
    current_config_sha256 = _extension_config_sha256(DATA)
    audit = _latest_audit(DATA)
    payload = build_automation_payload(
        catalog=catalog,
        window_registry=window_registry,
        threshold_registry=threshold_registry,
        window_payloads={key: payloads[key] for key in ("p1", "p2", "p3")},
        threshold_payload=payloads["p4"],
        incremental_payload=payloads["p5"],
        ledger_bindings=ledger_bindings,
        previous_payload=_previous_payload(DATA),
        current_audit_config_sha256=current_config_sha256,
        audit=audit,
        generated_at=utc_now(),
    )
    published, run_json, run_markdown = _publish_run(payload)
    ledger = _bind_p6(published, run_json, run_markdown, payloads)
    _publish_latest(published, run_json, run_markdown, ledger)
    print(
        json.dumps(
            {
                "research_id": published["research_id"],
                "catalog_snapshot_id": published["catalog"]["snapshot_id"],
                "run_json": run_json.relative_to(DATA).as_posix(),
                "run_markdown": run_markdown.relative_to(DATA).as_posix(),
                "ledger_record_id": ledger["record_id"],
                "summary": published["summary"],
                "task_kinds": dict(
                    sorted(
                        Counter(task["kind"] for task in published["pending_tasks"]).items()
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
