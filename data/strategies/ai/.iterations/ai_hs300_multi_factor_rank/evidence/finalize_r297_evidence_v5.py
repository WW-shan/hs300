#!/usr/bin/env python3
"""Finalize R297 evidence integrity and side-effect audit without rebuilding factors."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
RUNNER = EVIDENCE / "run_r297_scoped_audit_v4.py"
RESULT_V4 = EVIDENCE / "r297_counterfactual_membership_audit_v4.json"
REPORT_V4 = EVIDENCE / "r297_result_v4.md"
REGISTRATION = EVIDENCE / "r297_panel_registration.json"
PANEL_RECHECK_V4 = EVIDENCE / "r297_panel_status_recheck_v4.json"
PREREG = EVIDENCE / "r297_preregistration.json"
MANIFEST = EVIDENCE / "r297_input_manifest.jsonl"
BASELINE = EVIDENCE / "r297_formal_factor_baseline.json"
SCOPE = EVIDENCE / "r297_scope_correction.json"
RESULT = EVIDENCE / "r297_final_audit_v5.json"
REPORT = EVIDENCE / "r297_result_v5.md"
INDEX = EVIDENCE / "r297_round_index_v2.md"
PANEL_API = "http://127.0.0.1:3018/api/backtest/candidates"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def check_sidecar(path: Path) -> str:
    actual = sha_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    if actual != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path.name}")
    return actual


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_bytes(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def panel_get() -> tuple[int, bytes]:
    request = urllib.request.Request(PANEL_API, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main() -> int:
    outputs = (RESULT, RESULT.with_name(RESULT.name + ".sha256"), REPORT, REPORT.with_name(REPORT.name + ".sha256"), INDEX, INDEX.with_name(INDEX.name + ".sha256"))
    if any(path.exists() for path in outputs):
        raise RuntimeError("R297 v5 evidence exists; refusing overwrite")

    spec = importlib.util.spec_from_file_location("r297_runner_for_final_audit", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load R297 v4 verifier")
    verifier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = verifier
    spec.loader.exec_module(verifier)

    script_hash = check_sidecar(Path(__file__))
    runner_hash = check_sidecar(RUNNER)
    audit_hash = check_sidecar(verifier.SOURCE)
    erratum_hash = check_sidecar(verifier.ADDENDUM)
    source_before = verifier.verify_sources()
    result_v4_hash = check_sidecar(RESULT_V4)
    report_v4_hash = check_sidecar(REPORT_V4)
    registration_hash = check_sidecar(REGISTRATION)
    panel_recheck_hash = check_sidecar(PANEL_RECHECK_V4)
    scope_hash = check_sidecar(SCOPE)
    prereg_hash = check_sidecar(PREREG)
    manifest_hash = check_sidecar(MANIFEST)
    baseline_hash = check_sidecar(BASELINE)

    old_result = json.loads(RESULT_V4.read_text(encoding="utf-8"))
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    old_panel = json.loads(PANEL_RECHECK_V4.read_text(encoding="utf-8"))
    coverage = old_result["coverage"]
    if old_result.get("status") != "counterfactual_membership_restores_full_eligible_coverage_not_PIT" or not all(old_result["criteria"].values()):
        raise RuntimeError("R297 v4 result failed status or criteria integrity check")
    if coverage["r253_counterfactual_eligible_coverage"] != 250 or coverage["r257_counterfactual_eligible_coverage"] != 250:
        raise RuntimeError("R297 v4 coverage does not reconcile to 250 members")

    status, raw = panel_get()
    if status != 200:
        raise RuntimeError(f"Panel GET verification failed: HTTP {status}")
    payload = json.loads(raw)
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    candidate_id = registration["candidate"]["id"]
    matches = [item for item in items if item.get("id") == candidate_id]
    if len(matches) != 1 or matches[0].get("status") != "pending" or len(items) != registration["candidate_count_after"]:
        raise RuntimeError("R297 panel candidate/count no longer matches its registration proof")
    if old_panel.get("candidate_id") != candidate_id:
        raise RuntimeError("R297 original registration and GET proof identify different candidates")
    source_after = verifier.verify_sources()
    if source_before != source_after:
        raise RuntimeError("R297 frozen sources changed during final evidence audit")

    machine = {
        "round": "R297",
        "classification": "post_execution_evidence_integrity_and_scope_audit_not_alpha",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "status": old_result["status"],
        "counterfactual_not_pit": True,
        "source_factor_audit": {"file": RESULT_V4.name, "sha256": result_v4_hash, "coverage": coverage, "criteria": old_result["criteria"]},
        "panel": {"candidate_id": candidate_id, "status": matches[0]["status"], "candidate_count": len(items), "http_status": status, "response_sha256": sha_bytes(raw), "post_sent_during_this_audit": False},
        "source_hashes": {"preregistration": prereg_hash, "input_manifest": manifest_hash, "formal_factor_baseline": baseline_hash, "scope_correction": scope_hash, "execution_erratum_v4": erratum_hash, "v4_audit_script": audit_hash, "v4_runner_script": runner_hash, "v5_finalizer_script": script_hash, "v4_report": report_v4_hash, "original_panel_registration": registration_hash, "v4_panel_get_proof": panel_recheck_hash},
        "verified_source_state_before_and_after": source_before,
        "membership_interpretation_correction": {"latest_production_r78_as_of": coverage["historical_member_snapshot_diagnostic"]["latest_r78_snapshot_date"], "latest_production_r78_rows": coverage["historical_member_snapshot_diagnostic"]["latest_r78_snapshot_rows"], "symbol_absent_from_latest_production_r78": True, "earlier_r157_rows_for_symbol": coverage["historical_member_snapshot_diagnostic"]["new_symbol_old_r157_snapshot_rows"], "earlier_r157_does_not_change_latest_r78_asof": True},
        "side_effect_audit": {"production_builder_functions_reused": ["R253 factor/write/config", "R257 factor/write/config", "attach_ext_columns(include_snapshot=False)"], "all_data_dir_arguments_point_to_unique_staging_scratch": True, "config_upsert_cache_invalidation_target": "scratch/user_data/strategy_cache.json", "production_strategy_cache_touched": False, "live_api_process_memory_touched": False, "formal_data_written": False, "scratch_removed": True, "preexisting_empty_staging_entry_preserved": "enriched_rebuild", "basis": "ExtConfigStore(scratch).upsert passes scratch to invalidation; invalidate_ext_caches passes that explicit data_dir to strategy_cache.clear_cache, without substituting app settings."},
        "execution_boundary": {"immutable_manifest_inputs_verified_before_and_after": 2639, "runtime_cache_raw_hash_frozen": False, "runtime_cache_semantics_match_official_snapshot": True, "formal_factor_partitions_verified_before_and_after": 6396, "provider_or_network_pull_called": False, "backtest_screener_strategy_execution_or_matcher_called": False, "returns_or_trade_logs_read": False, "v5_reran_factor_build": False},
        "alpha_conclusion": "none; R297 validates counterfactual coverage and attachment only",
    }
    machine_hash = write_once(RESULT, (json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    report = f"""# R297 最终证据复核（覆盖反事实，非 PIT，非 alpha）

## 结论

R253 和 R257 的隔离构建在反事实目标日名单下各覆盖 250/250 个有效成分；生产系统真实装配函数对两个因子的非空值均逐值一致。002294.SZ 的 R253 值为 {coverage['new_symbol_r253_value']:.12f}，R257 值为 {coverage['new_symbol_r257_value']:.12f}。

这只证明反事实名单覆盖和系统装配可行。官方名单来自 2026-10-28，晚于目标日 2026-10-27，且没有核对调样公告生效日；不能认定为目标日历史成分、PIT、收益或有效 alpha。

## 成分记录更正

目标日生产 as-of R78 快照为 {coverage['historical_member_snapshot_diagnostic']['latest_r78_snapshot_date']}，共 {coverage['historical_member_snapshot_diagnostic']['latest_r78_snapshot_rows']} 行，不含 002294.SZ。较早 R157 快照曾有该代码 {coverage['historical_member_snapshot_diagnostic']['new_symbol_old_r157_snapshot_rows']} 行。此前失败断言把“所有旧 R157 快照都不含”当成条件，范围过宽；v3 改为检查最新生产 as-of R78。

## 数据与副作用核对

- 原输入清单 2,640 项中，除可变 TTL 缓存的原始字节哈希外，其余 2,639 项执行前后哈希、大小和修改时间一致；缓存成分与名称语义哈希与冻结官方名单一致。
- 正式三层因子 6,396 个分区执行前后哈希、大小和修改时间一致。临时构建目录已清理；data/.staging/enriched_rebuild 是原先已存在的空目录，本轮保留未动。
- 生产 builder 配置 upsert 触发了缓存失效代码，但显式传入的 data_dir 指向唯一 scratch，因此目标为 scratch 下的策略缓存。生产策略缓存和运行中的 3018 API 进程内存未触碰。
- 未调用 provider、回测、筛选、策略执行、撮合或下单；未读取收益和成交。本 v5 仅复核证据，没有重建因子。

## 面板与证据

候选 ID {candidate_id}，当前 GET 状态 {matches[0]['status']}，面板共 {len(items)} 条。本次只做 GET 复核，没有重复 POST。

预注册 SHA-256：{prereg_hash}。输入清单 SHA-256：{manifest_hash}。正式因子基线 SHA-256：{baseline_hash}。机器核验 JSON 和 SHA-256 sidecar：{RESULT.name}。

## 对寻找 alpha 的影响

R297 没有检验收益或预测力，不算找到 alpha，也不回答放宽门禁是否提高样本外收益。策略维持 research-only，生产维持 pause_production。后续要用可证明时间戳的 PIT 输入和未触碰的前瞻/时间外样本，直接比较 R275 期限门禁消融与匹配对照的系统收益；当前证据不足以放宽门禁。
"""
    report_hash = write_once(REPORT, report.encode("utf-8"))

    index = f"""# R297 最终证据索引

权威可读结论见 {REPORT.name}，机器证据见 {RESULT.name}。R297 是成分覆盖与装配反事实审计，不是 alpha 检验。

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| r297_preregistration.json | 原始预注册，未修改 | {prereg_hash} |
| r297_input_manifest.jsonl | 原始冻结输入清单，未修改 | {manifest_hash} |
| r297_formal_factor_baseline.json | 正式三层因子 6,396 分区基线 | {baseline_hash} |
| r297_scope_correction.json | TTL 缓存原始哈希的透明范围更正 | {scope_hash} |
| r297_execution_erratum_v4.json | 方法/失败更正及 v4 脚本哈希 | {erratum_hash} |
| audit_r297_counterfactual_membership_v4.py | 隔离因子构建和真实系统装配脚本 | {audit_hash} |
| run_r297_scoped_audit_v4.py | 输入/面板校验 runner | {runner_hash} |
| finalize_r297_evidence_v5.py | 最终证据和副作用复核脚本 | {script_hash} |
| r297_panel_registration.json | 单次登记与 GET 读回证明 | {registration_hash} |
| r297_panel_status_recheck_v4.json | v4 执行前只读 GET 证明 | {panel_recheck_hash} |
| r297_counterfactual_membership_audit_v4.json | 原 v4 机器结果，保留未覆盖 | {result_v4_hash} |
| r297_result_v4.md | 原 v4 报告，保留追溯 | {report_v4_hash} |
| {RESULT.name} | 最终机器证据链与副作用复核 | {machine_hash} |
| {REPORT.name} | 最终可读结果与边界 | {report_hash} |

冻结核验：2,639/2,639 不可变输入一致，6,396/6,396 正式分区一致。面板候选 {candidate_id} 为 pending。R257 继续 research-only，生产 pause_production。
"""
    index_hash = write_once(INDEX, index.encode("utf-8"))
    print(json.dumps({"machine_result_sha256": machine_hash, "report_sha256": report_hash, "index_sha256": index_hash, "panel_status": matches[0]["status"], "panel_count": len(items), "alpha_found": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
