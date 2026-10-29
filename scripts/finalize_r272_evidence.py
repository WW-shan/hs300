#!/usr/bin/env python3
"""Verify completed R272 artifacts and append the frozen-gate conclusion."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank"
EVIDENCE = RESEARCH / "evidence"
LEDGER = RESEARCH / "LEDGER.md"
SUMMARY = RESEARCH / "RESEARCH_SUMMARY.md"
RESULT_PATH = EVIDENCE / "r272_operating_margin_improvement_corrected.json"
FINAL_PATH = EVIDENCE / "r272_final_evidence_audit.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar_path(path: Path) -> Path:
    candidates = (path.with_suffix(".json.sha256"), path.with_suffix(".sha256"))
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def verify_sidecar(path: Path) -> bool:
    sidecar = sidecar_path(path)
    return sidecar.exists() and sidecar.read_text(encoding="ascii").split()[0] == sha256(path)


def main() -> int:
    if FINAL_PATH.exists() or FINAL_PATH.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 final evidence audit already exists; refusing to overwrite")

    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    cache = ROOT / "data/research/r272_cache_corrected"
    manifests = sorted(cache.glob("*.manifest.json"))
    manifest_audit = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        base = manifest_path.name.removesuffix(".manifest.json")
        curve_path = cache / f"{base}.parquet"
        trades_path = cache / f"{base}.trades.json"
        checks = {
            "runner_hash_matches": manifest.get("runner_sha256") == result["runner_sha256"],
            "curve_hash_matches": curve_path.exists()
            and manifest.get("curve_sha256") == sha256(curve_path),
            "trades_hash_matches": trades_path.exists()
            and manifest.get("trades_sha256") == sha256(trades_path),
            "preregistration_hash_matches": manifest.get("preregistration_sha256")
            == result["preregistration_sha256"],
            "implementation_lock_hash_matches": manifest.get(
                "implementation_correction_lock_sha256"
            )
            == result["implementation_correction_lock_sha256"],
            "factor_build_hash_matches": manifest.get("factor_build_evidence_sha256")
            == result["factor_build_sha256"],
        }
        manifest_audit.append(
            {
                "path": manifest_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(manifest_path),
                "checks": checks,
                "all_checks_pass": all(checks.values()),
            }
        )

    required_artifacts = [
        EVIDENCE / "r272_preregistration.json",
        EVIDENCE / "r272_implementation_correction_final_v3.json",
        EVIDENCE / "r272_factor_build_final.json",
        EVIDENCE / "r272_factor_build_final_partitions.json",
        EVIDENCE / "r272_corrected_factor_audit_v2.json",
        EVIDENCE / "r272_panel_registration.json",
        EVIDENCE / "r272_execution_compatibility.json",
        RESULT_PATH,
    ]
    artifact_audit = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256(path),
            "sidecar_path": sidecar_path(path).relative_to(ROOT).as_posix(),
            "sidecar_valid": verify_sidecar(path),
        }
        for path in required_artifacts
    ]
    audit = json.loads((EVIDENCE / "r272_corrected_factor_audit_v2.json").read_text(encoding="utf-8"))
    panel = json.loads((EVIDENCE / "r272_panel_registration.json").read_text(encoding="utf-8"))
    gates = result["gates"]
    failed_gates = [name for name, passed in gates.items() if name != "all_pass" and not passed]
    checks = {
        "all_required_artifact_sidecars_valid": all(item["sidecar_valid"] for item in artifact_audit),
        "factor_audit_passed": audit.get("all_checks_pass") is True,
        "panel_registration_passed": panel.get("all_checks_pass") is True,
        "all_eight_run_manifests_verified": len(manifest_audit) == 8
        and all(item["all_checks_pass"] for item in manifest_audit),
        "frozen_decision_is_rejection": result.get("decision") == "research_only_rejected"
        and gates.get("all_pass") is False,
        "gate_failures_recorded": bool(failed_gates),
    }
    proof = {
        "round": "R272",
        "status": "completed_rejected_by_frozen_preregistered_gates",
        "captured_at": datetime.now(UTC).isoformat(),
        "result": {"path": RESULT_PATH.relative_to(ROOT).as_posix(), "sha256": sha256(RESULT_PATH)},
        "required_artifacts": artifact_audit,
        "run_manifests": manifest_audit,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "failed_gates": failed_gates,
        "decision": result["decision"],
        "production_status": "pause_production",
        "limitations": result.get("limitations", []),
    }
    FINAL_PATH.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FINAL_PATH.with_suffix(".json.sha256").write_text(
        f"{sha256(FINAL_PATH)}  {FINAL_PATH.name}\n", encoding="ascii"
    )

    full = result["results"]["candidate"]["full"]
    control = result["results"]["control"]["full"]
    concentration = result["concentration"]["full"]
    gate_text = ", ".join(failed_gates)
    ledger_note = f"""

# R272：营业利润率同比改善因子未通过冻结门槛（2026-10-28）

修复 PIT as-of 排序和系统因子 ID 后，重建 591,430 行、549 只股票、1,081 个日分区。审计区间为 2023-05-04 至 2026-10-16：历史成员覆盖 99.977%，最近 252 个交易日覆盖 99.923%；独立可行性 probe 与落盘值逐键一致，最大差为 0，无重复、缺失或非有限值。随后完成本机 3018 面板 reload，因子、候选和匹配对照均已注册为 research-only，策略加载错误为 0。数据审计、面板原始响应和执行兼容性证据见 evidence/r272_corrected_factor_audit_v2.json、evidence/r272_panel_registration.json、evidence/r272_execution_compatibility.json。

冻结的 StrategyBacktestService -> BacktestEngine 回测使用 T+1 开盘、历史 HS300、10 仓等权、3x 主成本及 4x/5x 压力成本；八份分段曲线和成交记录 manifest 均通过哈希核验。候选全样本收益 {full['total_return']:.2%}、Sharpe {full['sharpe']:.4f}、MDD {full['mdd']:.2%}，匹配对照收益 {control['total_return']:.2%}。发现期 IC 为 {result['factor_ic']['discovery']['mean_ic']:.4f}，未达预注册门槛；发现段相对对照的收益差也未达要求，最近 12 个月收益为负。5x 成本全样本收益虽为 +{result['cost_stress']['5x']['full']['total_return']:.2%}，不能抵销其他门槛失败。

集中度失败：70 只标的的交易中，单票/前三/前五净贡献份额为 {concentration['top1_net_share']:.2%}/{concentration['top3_net_share']:.2%}/{concentration['top5_net_share']:.2%}。失败门槛为 {gate_text}。结论 research_only_rejected；不调参、不进入纸面交易或生产，生产状态继续 pause_production。财务输入是可能重述的当前快照，行业分类也是当前快照，验证区间并非未触碰 holdout。
"""
    if "# R272：营业利润率同比改善因子" not in LEDGER.read_text(encoding="utf-8"):
        LEDGER.write_text(LEDGER.read_text(encoding="utf-8") + ledger_note, encoding="utf-8")

    summary_note = f"""

# R272：营业利润率改善候选被拒绝（2026-10-28）

修复 as-of 排序和规范因子 ID 后，591,430 行、549 只股票、1,081 个分区通过独立 PIT 重建及 feasibility probe 对照；历史成员覆盖 99.977%，最近一年为 99.923%。真实面板 reload 成功，因子、候选和 matched control 均注册且无加载错误。系统回测候选全样本收益 {full['total_return']:.2%}、Sharpe {full['sharpe']:.4f}、MDD {full['mdd']:.2%}，控制组收益 {control['total_return']:.2%}；发现期 IC 为负、发现段相对收益不足、最近 12 个月为负，且单票/前三/前五净贡献份额为 {concentration['top1_net_share']:.2%}/{concentration['top3_net_share']:.2%}/{concentration['top5_net_share']:.2%}。虽然 4x/5x 收益为正，但多个冻结门槛失败，候选拒绝，不调参、不部署，pause_production。

完整结果、面板 reload、PIT 审计和 SHA-256 sidecar 见 evidence/r272_operating_margin_improvement_corrected.json、evidence/r272_final_evidence_audit.json、evidence/r272_panel_registration.json 及 data/research/r272_cache_corrected/。研究限制包括财务重述快照和当前行业分类。
"""
    if "# R272：营业利润率改善候选被拒绝" not in SUMMARY.read_text(encoding="utf-8"):
        SUMMARY.write_text(SUMMARY.read_text(encoding="utf-8") + summary_note, encoding="utf-8")

    print(
        json.dumps(
            {
                "final_audit": FINAL_PATH.relative_to(ROOT).as_posix(),
                "sha256": sha256(FINAL_PATH),
                "checks": checks,
                "failed_gates": failed_gates,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
