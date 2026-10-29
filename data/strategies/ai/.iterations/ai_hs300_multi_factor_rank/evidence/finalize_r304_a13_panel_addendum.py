from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ITERATION = EVIDENCE.parent
OUTPUT_JSON = EVIDENCE / "r304_A13_final_record_addendum.json"
OUTPUT_MD = EVIDENCE / "r304_A13_final_report_addendum.md"
MARKER = "## R304-A13 面板登记收尾与最终核验"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n")


def main() -> None:
    if OUTPUT_JSON.exists() or OUTPUT_MD.exists():
        raise RuntimeError("A13 final addendum exists; refusing overwrite")

    audit_path = EVIDENCE / "r304_A13_deviation_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    plan = json.loads((EVIDENCE / "r304_A13_posthoc_panel_registration.json").read_text(encoding="utf-8"))
    registration = json.loads((EVIDENCE / "r304_A13_panel_registration.json").read_text(encoding="utf-8"))
    if audit.get("pit_reconstruction") != "no-go":
        raise RuntimeError("Unexpected A13 PIT conclusion")
    if plan.get("record_type") != "post_hoc_panel_registration_preregistration":
        raise RuntimeError("A13 panel record does not disclose post-hoc registration")
    if not registration.get("registration_pass"):
        raise RuntimeError("A13 panel registration did not pass")
    if registration["candidate"]["status"] != "pending" or registration["existing_r304_candidate"]["status"] != "pending":
        raise RuntimeError("Unexpected A13 or original R304 status")
    if registration["candidate_count_before"] != 20 or registration["candidate_count_after"] != 21:
        raise RuntimeError("Unexpected A13 panel count")
    if not registration.get("existing_statuses_unchanged_vs_A11"):
        raise RuntimeError("A prior candidate status changed")

    included = [
        "audit_r304_a13_deviation.py",
        "r304_A13_deviation_audit.json",
        "r304_A13_deviation_audit.md",
        "r304_A13_constituent_chunk.js",
        "run_r304_a13_local_route_probe.py",
        "r304_A13_local_route_probe.json",
        "r304_A13_posthoc_panel_registration.json",
        "register_r304_a13_panel_once.py",
        "r304_A13_panel_registration_intent.json",
        "r304_A13_panel_registration_post.json",
        "r304_A13_panel_registration.json",
    ]
    evidence_hashes = {name: sha(EVIDENCE / name) for name in included}
    sidecar_checks = {}
    for name in included:
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256")
        recorded = sidecar.read_text(encoding="ascii").split()[0] if sidecar.exists() else None
        actual = sha(path)
        sidecar_checks[name] = {"recorded": recorded, "actual": actual, "match": recorded == actual}
    if not all(item["match"] for item in sidecar_checks.values()):
        raise RuntimeError("An A13 evidence SHA-256 sidecar is missing or inconsistent")

    report = (
        "# R304-A13 面板登记收尾\n\n"
        "## 结果\n\n"
        f"已将 A13 事后偏差审计单独登记到研究面板。预检、POST、最终 GET 均 HTTP 200；候选数 20→21。"
        f"新候选 ID {registration['candidate']['id']}，状态 pending，名称明确标记为事后审计、非预注册/非回测。"
        f"原 R304 ID {registration['existing_r304_candidate']['id']} 仍唯一且 pending；A11 基线已有候选状态全部未改变。"
        "登记只记录审计，不会追溯修复 chunk 获取未预注册的问题。\n\n"
        f"POST 回包保存在 r304_A13_panel_registration_post.json。最终 GET 的原始响应 body 未单独保存，"
        f"仅保留响应 SHA-256 {registration['readback_response_sha256']} 和读回候选摘要；没有重发请求。\n\n"
        "## 研究结论\n\n"
        "离线静态 bundle 显示，成分列表 POST /indexInfo/index-sample-information 的页面请求体只含搜索词、页码、页长和排序字段。"
        "单独的最新交易日 GET 只更新页面显示字段。本页面代码没有历史日期参数；服务端是否接受额外参数、"
        "其他官方接口是否支持历史查询，本轮均未验证，也没有调用成分查询 API。\n\n"
        "A13 chunk 请求没有事前单独预注册。PowerShell 调用报错后 dispatch 状态无法确认；Python 随后成功取得 HTTP 200。"
        "因此成功请求至少一次，实际总次数可能为一次或两次。该偏差已如实留档。A12 final record 的 29 项证据哈希有 28 项匹配；"
        "唯一差异是事后追加内容后的 A12 amendment 与旧 sidecar 不一致。旧文件、旧哈希和 dispatch 记录均保留。\n\n"
        "PIT 成分重建仍为 no-go：缺少 2018-01-02 完整官方种子及闭合的有效日事件链。"
        "本轮没有收益回测、因子收益或 Alpha 搜索，没有修改生产数据或策略。pending 不代表结果通过。\n\n"
        "机器最终记录 r304_A13_final_record_addendum.json 覆盖本轮材料哈希；逐项 sidecar 均匹配。\n"
    )
    final = {
        "round": "R304-A13",
        "classification": "final_append_only_record_of_posthoc_static_audit_and_panel_registration",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "prior_audit_sha256": sha(audit_path),
        "panel_plan_sha256": sha(EVIDENCE / "r304_A13_posthoc_panel_registration.json"),
        "registration_intent_sha256": sha(EVIDENCE / "r304_A13_panel_registration_intent.json"),
        "panel_post_proof_sha256": sha(EVIDENCE / "r304_A13_panel_registration_post.json"),
        "panel_readback_proof_sha256": sha(EVIDENCE / "r304_A13_panel_registration.json"),
        "panel": {
            "preflight_http_status": registration["preflight_http_status"],
            "post_http_status": registration["post_http_status"],
            "readback_http_status": registration["readback_http_status"],
            "candidate_count_before": registration["candidate_count_before"],
            "candidate_count_after": registration["candidate_count_after"],
            "a13_candidate_id": registration["candidate"]["id"],
            "a13_candidate_status": registration["candidate"]["status"],
            "original_r304_candidate_id": registration["existing_r304_candidate"]["id"],
            "original_r304_candidate_status": registration["existing_r304_candidate"]["status"],
            "existing_statuses_unchanged_vs_A11": registration["existing_statuses_unchanged_vs_A11"],
            "get_raw_body_archived": False,
            "get_response_sha256": registration["readback_response_sha256"],
            "post_retried": False,
        },
        "chunk_request": audit["chunk_request"],
        "static_contract": audit["static_contract"],
        "A12_hash_reconciliation": audit["A12_hash_reconciliation"],
        "pit_reconstruction": "no-go",
        "return_backtest_or_alpha_search": False,
        "production_mutation": False,
        "evidence_sha256": evidence_hashes,
        "evidence_sidecar_checks": sidecar_checks,
    }
    write_once(OUTPUT_JSON, (json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    write_once(OUTPUT_MD, report.encode("utf-8"))

    ledger_entry = (
        f"\r\n{MARKER}\r\n\r\n"
        f"A13 静态成分查询契约审计已留档，并按事后偏差单独登记。面板 GET/POST/GET 均 HTTP 200，候选数 20→21；"
        f"A13 pending，原 R304 pending，A11 既有候选状态全部未变。页面成分 POST 只见关键词/分页/排序参数，"
        f"页面日期 GET 只更新显示字段；本轮未调用 API，不能据此断言服务端或其他官方接口没有历史能力。"
        f"chunk 下载未预注册，PowerShell dispatch 不确定，Python 成功一次，总请求数可能 1–2。"
        f"A12 29 项哈希 28 项匹配，唯一差异为事后追加的 A12 amendment；旧记录和 sidecar 保留。"
        f"面板最终 GET 原始 body 未保存，只留响应 SHA 与读回摘要。PIT 仍 no-go，无收益回测/Alpha 搜索/生产写入。"
        f"完整证明见 evidence/{OUTPUT_MD.name}、evidence/{OUTPUT_JSON.name}。\r\n"
    ).encode("utf-8")
    for filename in ("LEDGER.md", "RESEARCH_SUMMARY.md"):
        target = ITERATION / filename
        raw = target.read_bytes()
        if MARKER.encode("utf-8") in raw:
            raise RuntimeError(f"A13 final ledger entry already exists in {filename}")
        if not raw.endswith((b"\n", b"\r")):
            ledger_entry = b"\r\n" + ledger_entry
        with target.open("ab") as stream:
            stream.write(ledger_entry)

    script_sidecar = Path(__file__).with_name(Path(__file__).name + ".sha256")
    with script_sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{sha(Path(__file__))}  {Path(__file__).name}\n")
    print(json.dumps({
        "final_json_sha256": sha(OUTPUT_JSON),
        "final_report_sha256": sha(OUTPUT_MD),
        "panel_candidate_count": [registration["candidate_count_before"], registration["candidate_count_after"]],
        "a13_candidate_id": registration["candidate"]["id"],
        "a13_status": registration["candidate"]["status"],
        "r304_status": registration["existing_r304_candidate"]["status"],
        "all_prior_statuses_unchanged": registration["existing_statuses_unchanged_vs_A11"],
        "pit_reconstruction": "no-go",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
