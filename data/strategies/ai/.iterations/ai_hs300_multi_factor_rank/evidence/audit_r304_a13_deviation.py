from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ITERATION = ROOT.parent
ROUND = "R304-A13"
CHUNK = ROOT / "r304_A13_constituent_chunk.js"
BUNDLE = ROOT / "r304_official_frontend_bundle.js"
PROBE = ROOT / "r304_A13_local_route_probe.json"
AMENDMENT = ROOT / "r304_preregistration_amendment_A12.json"
A12_RECORD = ROOT / "r304_A12_final_record_addendum.json"
OUTPUT_JSON = ROOT / "r304_A13_deviation_audit.json"
OUTPUT_MD = ROOT / "r304_A13_deviation_audit.md"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def main() -> None:
    for path in (OUTPUT_JSON, OUTPUT_MD):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"Refusing to overwrite existing evidence: {path.name}")

    chunk_text = CHUNK.read_text(encoding="utf-8")
    bundle_text = BUNDLE.read_text(encoding="utf-8")
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    a12_record = json.loads(A12_RECORD.read_text(encoding="utf-8"))
    probe = json.loads(PROBE.read_text(encoding="utf-8"))

    expected_contract = {
        "searchInput": "this.sampleInfo",
        "pageNum": "this.pagination.current",
        "pageSize": "this.pagination.pageSize",
        "sortField": "this.sorter.sortField",
        "sortOrder": "this.sorter.sortOrder",
    }
    request_contract_present = all(
        f"{key}:{value}" in chunk_text for key, value in expected_contract.items()
    )
    request_has_date_parameter = any(
        token in chunk_text for token in ("tradeDate:", "effectiveDate:", "asOfDate:", "startDate:", "endDate:")
    )
    query_date_present = 'index-sample-information-trade-date-new' in chunk_text and 'updateDate=e.data' in chunk_text
    route_mapping_present = 'sampleTable:' in bundle_text and '/indexInfo/index-sample-information' in bundle_text and 'dataService.sampleTable,t' in chunk_text
    if not request_contract_present or request_has_date_parameter or not query_date_present or not route_mapping_present:
        raise RuntimeError("Saved static bundle no longer matches the recorded A13 observations")

    amendment_sidecar = AMENDMENT.with_name(AMENDMENT.name + ".sha256").read_text(encoding="ascii").split()[0]
    a12_script = ROOT / "r304_A12_final_record_addendum.py"
    a12_script_sidecar = a12_script.with_name(a12_script.name + ".sha256").read_text(encoding="ascii").split()[0]
    a12_report = ROOT / "r304_A12_final_report_addendum.md"
    a12_report_sidecar = a12_report.with_name(a12_report.name + ".sha256").read_text(encoding="ascii").split()[0]
    evidence_map = a12_record["evidence_sha256"]
    listed_checks = {
        name: {"recorded": expected, "actual": file_sha(ROOT / name), "match": expected == file_sha(ROOT / name)}
        for name, expected in evidence_map.items()
    }
    mismatch_names = [name for name, result in listed_checks.items() if not result["match"]]
    if mismatch_names != ["r304_preregistration_amendment_A12.json"]:
        raise RuntimeError(f"Unexpected A12 record hash reconciliation: {mismatch_names}")

    captured_at = datetime.now(UTC).isoformat()
    chunk_mtime = datetime.fromtimestamp(CHUNK.stat().st_mtime, UTC).isoformat()
    report = f"""# R304-A13 请求偏差与官方成分查询静态审计

记录时间（UTC）：{captured_at}

## 请求偏差

A13 静态 chunk 下载在发出请求前没有单独预注册。先前 PowerShell `Invoke-WebRequest` 报 `NullReferenceException`，现存记录无法确认请求是否已 dispatch；随后 Python 获取到该静态 JS 文件 HTTP 200、16,208 字节，SHA-256 `{file_sha(CHUNK)}`。因此至少一次请求已确认成功，总请求数为 1 次已确认、最多可能 2 次；不得表述为“确定只请求一次”。下载文件时间戳为 {chunk_mtime}，它不是精确的网络 dispatch 时间。此次是页面静态资源，不是成分查询 API；没有调用页面所列 API。

原 `r304_A13_local_route_probe.json` 记录的是下载 chunk **之前**对主页 bundle 的本地解析，`network_requests=0` 只适用于那一步，不覆盖后续静态资源请求。原件与其 SHA sidecar均保留。由于 chunk 和探查脚本当时没有 sidecar，本次只在事后追加哈希 sidecar；它们证明当前文件内容，不证明请求发生时的时间顺序。

## 静态调用契约

- 主页 bundle SHA-256：`{file_sha(BUNDLE)}`；chunk SHA-256：`{file_sha(CHUNK)}`。
- 页面把 `sampleTable` 映射为 POST `/indexInfo/index-sample-information`。页面调用时只传 `searchInput`、`pageNum`、`pageSize`、`sortField`、`sortOrder`。代码中未发现日期/生效日参数。
- 页面另行 GET `/csindex-home/indexInfo/index-sample-information-trade-date-new`，结果只赋给显示用的 `updateDate`；没有传入成分查询请求。
- 这说明已观察页面的查询契约没有历史日期字段；不能据此断言服务端不接受额外参数，也不能断言没有其他官方历史接口。未发 API 请求，不猜参数、不继续探测。

## A12 证据哈希更正

A12 final record 的 29 项记录哈希中，28 项仍匹配，唯一不匹配的是 `r304_preregistration_amendment_A12.json`：record/旧 sidecar 记录 `{evidence_map['r304_preregistration_amendment_A12.json']}`，当前文件为 `{file_sha(AMENDMENT)}`。A12 dispatch 在 07:45:25 UTC 记录的 amendment 哈希仍是旧值；该 amendment 后来被追加 `next_discovery_scope`，但没有同步更新原 sidecar。保留旧 sidecar和原 final record，不倒填或覆盖。

另外，`r304_A12_final_record_addendum.py` 当前哈希 `{file_sha(a12_script)}`，旧 sidecar 为 `{a12_script_sidecar}`；该脚本也在最终记录之后被修改。A12 可读报告当前哈希 `{file_sha(a12_report)}` 与其 sidecar `{a12_report_sidecar}` 相符。A12 final record JSON 自身及 sidecar相符。详细逐项回核见本文件旁的机器记录。

## 结论与边界

没有得到按历史日期取成分的官方证据；2018-01-02 完整种子与 PIT 事件链仍未闭合，继续 no-go。没有做收益回测、Alpha 搜索或生产数据/策略写入。沿用已登记的 R304 面板候选 `1ed2c75285b74fc6a521cc73f75f7320`；最近一次 A11 读回为唯一匹配、`pending`。本轮不重复注册、不改候选状态。后续任何官方网络请求必须先单独预注册、留好 dispatch intent，再按一次性请求规则执行。
"""

    audit = {
        "round": ROUND,
        "classification": "retrospective_deviation_and_local_contract_audit_no_api_call",
        "captured_at_utc": captured_at,
        "preregistration": {
            "registered_before_chunk_request": False,
            "historical_preregistration_created": False,
            "initial_local_probe_network_requests": probe.get("network_requests"),
            "initial_probe_scope": "homepage bundle inspection before chunk download only",
        },
        "chunk_request": {
            "powershell_attempt": "Invoke-WebRequest NullReferenceException; dispatch state unknown",
            "python_attempt": {"status": 200, "bytes": CHUNK.stat().st_size, "sha256": file_sha(CHUNK)},
            "confirmed_successful_requests": 1,
            "possible_total_requests_min": 1,
            "possible_total_requests_max": 2,
            "artifact_mtime_utc_not_dispatch_time": chunk_mtime,
            "chunk_sidecar_created_post_hoc": not CHUNK.with_name(CHUNK.name + ".sha256").exists(),
        },
        "static_contract": {
            "main_bundle_sha256": file_sha(BUNDLE),
            "chunk_sha256": file_sha(CHUNK),
            "component_endpoint": "/indexInfo/index-sample-information",
            "component_method": "POST",
            "body_fields": expected_contract,
            "body_contract_present": request_contract_present,
            "date_parameter_in_component_body": request_has_date_parameter,
            "update_date_endpoint": "/csindex-home/indexInfo/index-sample-information-trade-date-new",
            "update_date_only_displayed": query_date_present,
            "route_mapping_verified_in_saved_bundle": route_mapping_present,
            "historical_server_support": "not established; no API request sent",
        },
        "A12_hash_reconciliation": {
            "final_record_listed_items": len(listed_checks),
            "matching_items": sum(item["match"] for item in listed_checks.values()),
            "mismatches": mismatch_names,
            "item_checks": listed_checks,
            "amendment_current_sha256": file_sha(AMENDMENT),
            "amendment_old_sidecar_sha256": amendment_sidecar,
            "amendment_hash_bound_in_A12_dispatch": json.loads((ROOT / "r304_A12_dispatch.json").read_text(encoding="utf-8"))["amendment_sha256"],
            "A12_record_script_current_sha256": file_sha(a12_script),
            "A12_record_script_old_sidecar_sha256": a12_script_sidecar,
            "A12_report_current_sha256": file_sha(a12_report),
            "A12_report_sidecar_sha256": a12_report_sidecar,
            "A12_record_json_current_sha256": file_sha(A12_RECORD),
            "A12_record_json_sidecar_sha256": A12_RECORD.with_name(A12_RECORD.name + ".sha256").read_text(encoding="ascii").split()[0],
        },
        "panel": {
            "candidate_id": "1ed2c75285b74fc6a521cc73f75f7320",
            "latest_readback_round": "R304-A11",
            "latest_observed_status": "pending",
            "new_registration": False,
            "status_mutation": False,
        },
        "pit_reconstruction": "no-go",
        "return_backtest_or_alpha_search": False,
        "production_mutation": False,
    }

    write_once(OUTPUT_JSON, (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    write_once(OUTPUT_MD, report.encode("utf-8"))

    # These sidecars attest the files as currently found, not the historical fetch time.
    for path in (CHUNK, ROOT / "run_r304_a13_local_route_probe.py"):
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists():
            with sidecar.open("x", encoding="ascii") as stream:
                stream.write(f"{file_sha(path)}  {path.name}\n")

    for filename, marker in (
        ("LEDGER.md", "## R304-A13 偏差审计与成分查询静态结论"),
        ("RESEARCH_SUMMARY.md", "## R304-A13 成分查询能力核验"),
    ):
        target = ITERATION / filename
        if not target.exists():
            raise FileNotFoundError(target)
        current = target.read_bytes()
        marker_bytes = marker.encode("utf-8")
        if marker_bytes not in current:
            if not current.endswith((b"\n", b"\r")):
                with target.open("ab") as stream:
                    stream.write(b"\r\n")
            entry = (
                f"\r\n{marker}\r\n\r\n"
                f"{report.split('## 请求偏差', 1)[1].split('## A12 证据哈希更正', 1)[0].strip()}\r\n\r\n"
                f"PIT 结论保持 no-go；无收益回测、Alpha 搜索或生产写入。完整偏差与哈希证明见 `evidence/{OUTPUT_MD.name}`、`evidence/{OUTPUT_JSON.name}`。\r\n"
            ).encode("utf-8")
            with target.open("ab") as stream:
                stream.write(entry)

    script_sidecar = Path(__file__).with_name(Path(__file__).name + ".sha256")
    with script_sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{file_sha(Path(__file__))}  {Path(__file__).name}\n")

    print(json.dumps({
        "audit_json_sha256": file_sha(OUTPUT_JSON),
        "audit_report_sha256": file_sha(OUTPUT_MD),
        "a12_record_list_matches": audit["A12_hash_reconciliation"]["matching_items"],
        "a12_record_list_total": len(listed_checks),
        "a12_record_mismatch": mismatch_names,
        "chunk_sha256": file_sha(CHUNK),
        "request_count_range": [1, 2],
        "pit_reconstruction": "no-go",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
