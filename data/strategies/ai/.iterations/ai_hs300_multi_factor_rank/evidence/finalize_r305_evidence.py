from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> str:
    expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    actual = sha(path)
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch: {path.name}")
    return actual


def write_once(path: Path, raw: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> None:
    prereg = EVIDENCE / "r305_data_gap_diagnostic_preregistration.json"
    audit = EVIDENCE / "r305_member_bar_gap_audit.json"
    intent = EVIDENCE / "r305_panel_registration_intent.json"
    preflight_raw = EVIDENCE / "r305_panel_registration_preflight.raw.json"
    post = EVIDENCE / "r305_panel_registration_post.json"
    post_raw = EVIDENCE / "r305_panel_registration_post.raw.json"
    readback = EVIDENCE / "r305_panel_registration_readback.json"
    readback_raw = EVIDENCE / "r305_panel_registration_readback.raw.json"
    audit_script = EVIDENCE / "audit_r305_member_bar_gap.py"
    registration_script = EVIDENCE / "register_r305_panel_once.py"
    final_audit_path = EVIDENCE / "r305_final_integrity_audit.json"
    report_path = EVIDENCE / "r305_result.md"
    index_path = EVIDENCE / "r305_round_index.md"
    for output in (final_audit_path, report_path, index_path):
        if output.exists() or output.with_name(output.name + ".sha256").exists():
            raise RuntimeError(f"R305 final evidence exists; refusing overwrite: {output.name}")

    evidence_paths = (
        prereg, audit, intent, preflight_raw, post, post_raw,
        readback, readback_raw, audit_script, registration_script,
    )
    verified = {path.name: verify_sidecar(path) for path in evidence_paths}
    audit_data = json.loads(audit.read_text(encoding="utf-8"))
    intent_data = json.loads(intent.read_text(encoding="utf-8"))
    post_data = json.loads(post.read_text(encoding="utf-8"))
    readback_data = json.loads(readback.read_text(encoding="utf-8"))
    pre_items = json.loads(preflight_raw.read_text(encoding="utf-8"))["items"]
    final_items = json.loads(readback_raw.read_text(encoding="utf-8"))["items"]
    pre_statuses = {str(item["id"]): item.get("status") for item in pre_items}
    final_statuses = {str(item["id"]): item.get("status") for item in final_items}
    new_id = "cc701e29d6b1450982d2375dd50ce08b"
    if len(pre_items) != 22 or len(final_items) != 23 or new_id not in final_statuses:
        raise RuntimeError("R305 panel raw readbacks have unexpected candidate counts or ID")
    if any(final_statuses.get(key) != value for key, value in pre_statuses.items()):
        raise RuntimeError("A pre-existing panel candidate status changed")
    if final_statuses[new_id] != "pending":
        raise RuntimeError("R305 candidate is not pending")
    if not intent_data.get("guards_pass") or not readback_data.get("registration_pass"):
        raise RuntimeError("R305 panel intent or final readback did not pass")
    if post_data.get("http_status") != 200 or readback_data.get("readback_http_status") != 200:
        raise RuntimeError("R305 POST or GET did not return HTTP 200")

    coverage = audit_data["exact_key_coverage"]
    status_counts = audit_data["auxiliary_qfq_match_for_canonical_absence"]["trade_status_counts"]
    if coverage["member_date_symbol_keys"] != 419700 or coverage["canonical_absent"] != 3586:
        raise RuntimeError("R305 audit totals are inconsistent")
    if status_counts != {"0": 58, "1": 2988, "None": 540}:
        raise RuntimeError("R305 auxiliary status counts are inconsistent")
    input_hashes = audit_data["input_sha256"]
    if audit_data["input_count"] != len(input_hashes):
        raise RuntimeError("R305 input inventory count is inconsistent")
    changed_inputs = [
        relative for relative, expected in input_hashes.items()
        if not (ROOT / relative).is_file() or sha(ROOT / relative) != expected
    ]
    if changed_inputs:
        raise RuntimeError(f"Frozen R305 inputs changed after audit: {changed_inputs[:10]}")

    report = f"""# R305 成员日行情缺口溯源（2026-10-29）

## 研究状态

本轮是事后数据来源审计，不是 alpha 假设或收益证据。3,586 条缺口在本审计冻结前曾做过初步探索，因此数字均标为事后诊断结果，不冒充预注册发现。没有读取 OHLCV 数值、复权价格或收益，没有运行回测、alpha 扫描、provider/builder，也没有写生产行情或策略。

## 数据核对

- R78 有 1,399 个查询日、419,700 个精确 (query_date, symbol) 成员键，覆盖 2020-12-31 至 2026-10-14。
- 与主日线 data/kline_daily 按精确 (date, symbol) 连接，416,114 个有行，3,586 个缺行，覆盖率 99.14558%。同一批成员在 enriched 日线也缺 3,586 行。
- 成员覆盖区间内没有重复主行情键。本轮只读键列，所以不能据此声称缺行对应 OHLC 空值或坏价格。
- 与旧 R40 bootstrap_missing.parquet 的 qfq 键按日期和代码匹配：3,046 条匹配，其中 tradestatus=1 为 2,988 条，tradestatus=0 为 58 条；540 条无辅助记录。1 只能说明该辅助源把行标为可交易，不能解释主数据为何漏行；0 是该源报非交易，尚非独立停牌证明。不能把整个缺口归因于停牌，也不能静默补价。
- 缺失按年：2020 年 9、2021 年 1,598、2022 年 543、2023 年 488、2024 年 543、2025 年 329、2026 年 76。540 条未解释记录主要集中于 601198.SH (131)、601059.SH (36)、601995.SH (26)、000425.SZ (23) 等。

## 面板及证明

R305 预注册标记了事后审计性质，并在执行前做面板 GET 预检；候选以一次 POST 登记，未重试，再做 GET 回读。GET/POST/GET 均为 HTTP 200，候选数 22→23，新 ID {new_id} 为 pending，既有 22 个候选状态全部未变。面板原始 GET/POST/GET body 均保存并附 SHA-256。

R304-A14 的冻结 NO-GO 不变：一个官方调入代码清单尚未闭合，且其缺失键 600745.SH / 2020-06-10 在本轮 R78 窗口之外；本轮既未重开该门槛，也未研究该键的行情数值。缺口审计不能用于删除事件或放宽 A14 门槛。

冻结输入清单含 {audit_data['input_count']:,} 个文件（R78、辅助缓存及成员日期覆盖的日线分区）。审计前后输入 SHA-256 清单一致，最终复核仍匹配。预注册哈希为 {verified[prereg.name]}；机器结果哈希为 {verified[audit.name]}。

主要文件：{prereg.name}、{audit_script.name}、{audit.name}、r305_panel_registration_intent.json、r305_panel_registration_post.json、r305_panel_registration_readback.json、原始 GET/POST/GET body 及 r305_final_integrity_audit.json。以上文件均有独立 SHA-256 sidecar。

## 判定

缺行不是单纯的停牌问题：2,988 条辅助记录被来源标为可交易，另有 540 条无来源解释。若后续做因子或回测，必须使用有可核验行情的样本规则或恢复上游数据并另行冻结，不得直接前向填充或把未知状态当作停牌。本轮没有发现或确认新的 alpha；R257 仍未通过集中度和样本外门槛，生产状态继续 pause_production。
"""
    report_digest = write_once(report_path, report.encode("utf-8"))
    index = f"""# R305 Evidence Index

- Classification: posthoc data-provenance audit only; no returns or alpha tests.
- Candidate: {new_id}, pending; panel GET/POST/GET HTTP 200, 22→23, existing statuses unchanged.
- Inputs: {audit_data['input_count']:,} SHA-256 checked files; unchanged at final verification.
- Result: {audit.name} SHA-256 {verified[audit.name]}.
- Report: {report_path.name} SHA-256 {report_digest}.
- Audit script: {audit_script.name} SHA-256 {verified[audit_script.name]}.
- Registration script: {registration_script.name} SHA-256 {verified[registration_script.name]}.
- R304-A14 remains NO-GO; R257 remains research-only; production remains pause_production.
"""
    index_digest = write_once(index_path, index.encode("utf-8"))
    final_record = {
        "round": "R305",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "evidence_integrity_and_read_only_final_input_hash_verification",
        "checks": {
            "all_registered_evidence_sidecars_match": True,
            "frozen_input_files_match_post_audit_hashes": True,
            "frozen_input_count": len(input_hashes),
            "preflight_candidates": len(pre_items),
            "readback_candidates": len(final_items),
            "all_preexisting_statuses_unchanged": True,
            "new_candidate_id": new_id,
            "new_candidate_status": final_statuses[new_id],
            "post_get_http_200": True,
            "audit_totals_consistent": True,
            "no_return_or_backtest": True,
        },
        "sha256": {**verified, report_path.name: report_digest, index_path.name: index_digest},
        "protected_statuses": {
            key: final_statuses[key]
            for key in (
                "1ed2c75285b74fc6a521cc73f75f7320",
                "ecd1805dde7a4a8d971718116bd773f6",
                new_id,
            )
        },
    }
    record_raw = (json.dumps(final_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    record_digest = write_once(final_audit_path, record_raw)
    print(json.dumps({"final_audit_sha256": record_digest, **final_record["checks"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
