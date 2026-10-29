#!/usr/bin/env python3
"""Capture hashes for all R296 evidence and write the round index."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
INDEX_JSON = EVIDENCE / "r296_round_index.json"
INDEX_MD = EVIDENCE / "r296_round_index.md"
SELF = Path(__file__).resolve()
SCRIPT_NAMES = {
    "freeze_r296.py",
    "register_r296_panel_once.py",
    "capture_r296_formal_factor_baseline.py",
    "audit_r296_isolated_build_attachment.py",
    "correct_r296_membership_interpretation.py",
    "correct_r296_membership_eligibility.py",
    "capture_r296_provenance.py",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    if any(path.exists() for path in (INDEX_JSON, INDEX_MD)):
        raise RuntimeError("R296 round index exists; refusing overwrite")
    paths = []
    for path in EVIDENCE.iterdir():
        if not path.is_file() or path.name in {INDEX_JSON.name, INDEX_MD.name}:
            continue
        if path.name.startswith("r296_") or path.name in SCRIPT_NAMES or path.name in {name + ".sha256" for name in SCRIPT_NAMES}:
            paths.append(path)
    paths.sort(key=lambda item: item.name)
    artifacts = [
        {"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha(path)}
        for path in paths
    ]
    prereg = json.loads((EVIDENCE / "r296_preregistration.json").read_text(encoding="utf-8"))
    machine = json.loads((EVIDENCE / "r296_isolated_build_attachment_audit.json").read_text(encoding="utf-8"))
    v3 = json.loads((EVIDENCE / "r296_isolated_build_attachment_audit_v3.json").read_text(encoding="utf-8"))
    panel = json.loads((EVIDENCE / "r296_panel_registration.json").read_text(encoding="utf-8"))
    record = {
        "round": "R296",
        "classification": "complete_research_evidence_and_post_result_corrections_index",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "authoritative_status": v3["status"],
        "authoritative_result": "r296_result_v3.md",
        "authoritative_machine_result": "r296_isolated_build_attachment_audit_v3.json",
        "preregistration_sha256": sha(EVIDENCE / "r296_preregistration.json"),
        "input_manifest_sha256": prereg["frozen_context"]["input_manifest"]["sha256"],
        "first_result_sha256": sha(EVIDENCE / "r296_isolated_build_attachment_audit.json"),
        "first_erratum_sha256": sha(EVIDENCE / "r296_membership_interpretation_erratum.json"),
        "second_erratum_sha256": sha(EVIDENCE / "r296_membership_eligibility_erratum.json"),
        "panel_candidate_id": panel["candidate"]["id"],
        "panel_registration_pass": panel["registration_pass"],
        "panel_candidate_count": [panel["candidate_count_before"], panel["candidate_count_after"]],
        "formal_factor_partitions_verified_unchanged": machine["execution_limits"]["formal_factor_partition_count_verified_after"],
        "input_files_verified_unchanged": machine["execution_limits"]["all_frozen_input_files_verified_after"],
        "script_freeze_limitation": "The original preregistration did not fingerprint audit and panel scripts before execution. Their hashes are recorded here after execution, not claimed as pre-run freezes.",
        "artifacts": artifacts,
        "index_writer_sha256": sha(SELF),
    }
    json_hash = write_once(INDEX_JSON, (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    lines = [
        "# R296 证据索引",
        "",
        "权威结论见 r296_result_v3.md 和 r296_isolated_build_attachment_audit_v3.json。首份报告、机器结果和两次事后更正均保留，没有覆盖。",
        "",
        f"面板候选 ID {record['panel_candidate_id']} 单次 POST/GET 均成功；数量 {record['panel_candidate_count'][0]}→{record['panel_candidate_count'][1]}，状态 pending。",
        f"计算前后核对 {record['input_files_verified_unchanged']} 个冻结输入文件和正式三层因子 {record['formal_factor_partitions_verified_unchanged']} 个分区，哈希一致。未调用 provider、builder main、回测、筛选或撮合，未读取收益/成交。",
        "",
        "审计/登记脚本没有纳入原始事前脚本哈希冻结；本索引事后保存其哈希，并如实披露此限制。",
        "",
        "| 文件 | SHA-256 |",
        "| --- | --- |",
    ]
    for item in artifacts:
        lines.append(f"| {Path(item['path']).name} | {item['sha256']} |")
    lines.extend([
        "",
        f"机器索引 JSON SHA-256：{json_hash}。JSON 和 Markdown 索引都有旁置 SHA-256。",
        "",
        "结果边界：历史快照 eligible universe 覆盖 249/249；下一日官方名单 eligible universe 覆盖 249/250，缺少新加入的 002294.SZ。仅证明单日隔离构建和系统装配；不证明收益、alpha、历史 PIT 或生产就绪。R257 仍 research-only，生产 pause_production。",
    ])
    md_hash = write_once(INDEX_MD, ("\n".join(lines) + "\n").encode("utf-8"))
    print(json.dumps({"round": "R296", "artifact_count": len(artifacts), "index_json_sha256": json_hash, "index_markdown_sha256": md_hash, "index_writer_sha256": sha(SELF)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
