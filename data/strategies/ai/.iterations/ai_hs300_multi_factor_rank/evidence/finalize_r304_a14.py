from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = next(path for path in EVIDENCE.parents if (path / "CONTRIBUTING.md").is_file())
ROUND_DIR = EVIDENCE.parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, raw: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(raw)
    result = hashlib.sha256(raw).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{result}  {path.name}\n", encoding="ascii")
    return result


def main() -> None:
    coverage_path = EVIDENCE / "r304_A14_event_coverage_corrected.json"
    plan_path = EVIDENCE / "r304_A14_preregistration.json"
    registration_path = EVIDENCE / "r304_A14_panel_registration_readback.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    if coverage["preregistration_sha256"] != digest(plan_path):
        raise RuntimeError("Coverage audit does not match the panel-registered preregistration")
    if not registration.get("all_pass") or registration.get("candidate_count_after") != 22:
        raise RuntimeError("A14 panel readback failed")
    if not registration.get("R304_and_A13_pending") or not registration.get("existing_statuses_unchanged"):
        raise RuntimeError("A prior panel status changed")
    if coverage["event_count"] != 17 or coverage["official_addition_lists_complete"] != 16:
        raise RuntimeError("Unexpected routine-event reconciliation result")
    if coverage["missing_bar_code_date_rows"] != 1 or coverage["no_return_or_backtest"] is not True:
        raise RuntimeError("Unexpected coverage/no-return boundary")

    result = {
        "round": "R304-A14",
        "date": "2026-10-29",
        "classification": "posthoc_exploratory_hypothesis_feasibility_only_not_alpha",
        "hypothesis": "New HS300 additions may have pre-implementation drift from the first trading session strictly after official publication through the last session strictly before after-close implementation, relative to a pre-specified market/industry control.",
        "motivation_and_bias": "The question was proposed after seeing R46/R49. Those results motivate an exploratory hypothesis and are not evidence for it.",
        "preregistration_sha256": digest(plan_path),
        "panel": {
            "preflight_post_readback_http": [200, 200, 200],
            "candidate_count_before": registration["candidate_count_before"],
            "candidate_count_after": registration["candidate_count_after"],
            "candidate_id": registration["candidate"]["id"],
            "candidate_status": registration["candidate"]["status"],
            "R304_and_A13_pending": registration["R304_and_A13_pending"],
            "existing_statuses_unchanged": registration["existing_statuses_unchanged"],
        },
        "events": {
            "routine_event_count": coverage["event_count"],
            "official_addition_lists_complete": coverage["official_addition_lists_complete"],
            "notice_14225": "The official notice states 28 additions and 2022-06-10 after-close implementation; its official code list was not archived. R78 locally reproduces 28 additions from the 2022-06-10 and 2022-06-13 snapshots, but this does not count as official-list closure.",
            "temporary_changes_excluded": ["2025-02 notice 15546", "2025-07 notice 1006022", "2026-09 notice 3006227"],
        },
        "code_date_coverage": {
            "daily_partitions_checked": "2,139 partition dates available; only symbol/date read for the event windows",
            "expected_stock_session_rows": coverage["expected_bar_code_date_rows"],
            "missing_stock_session_rows": coverage["missing_bar_code_date_rows"],
            "missing_example": "600745 on 2020-06-10",
            "otherwise_complete_event_windows": "16/17, counting the locally reconstructed 2022-06 list; this does not pass the official-list gate",
        },
        "membership_crosscheck": {
            "exact_transition_matches_including_local_14225": f"{sum(event.get('historical_membership_transition', {}).get('exact_code_set_match') is True for event in coverage['routine_events'])}/{sum(bool(event.get('historical_membership_transition', {}).get('before_query_date')) for event in coverage['routine_events'])}",
            "timing_caveat": "The saved R78 snapshots omit dates from 2018-2020 and do not always capture the first post-effective session. Exact code sets match within 180 calendar days, but this is not an official event-date PIT membership series; the 2022-12 match appears on 2023-03-20.",
            "preannouncement_member_counts": "R157/R78 each supply 300 names on snapshots queried before all 17 announcements, with update_date no later than query_date. Control provenance and industry matching remain unvalidated.",
        },
        "decision": "NO-GO for return calculations or system backtest: one official addition list is not archived and one required stock-day row is missing. Do not drop the event or relax gates after seeing outcomes.",
        "work_done": "Read only symbol/date from daily-bar partitions; did not read OHLCV/adjusted prices/returns, run a backtest or alpha scan, or modify production data/strategies.",
        "evidence": {
            "preregistration": "r304_A14_preregistration.json",
            "coverage_audit": "r304_A14_event_coverage_corrected.json",
            "coverage_script": "audit_r304_a14_event_coverage.py",
            "panel_intent": "r304_A14_panel_registration_intent.json",
            "panel_post": "r304_A14_panel_registration_post.json",
            "panel_readback": "r304_A14_panel_registration_readback.json",
        },
    }
    evidence_files = [
        "r304_A14_preregistration.json",
        "r304_A14_panel_registration_intent.json",
        "r304_A14_panel_registration_post.json",
        "r304_A14_panel_registration_readback.json",
        "audit_r304_a14_event_coverage.py",
    ]
    for name in evidence_files:
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists() or sidecar.read_text(encoding="ascii").split()[0] != digest(path):
            raise RuntimeError(f"Evidence checksum mismatch: {name}")
    result["evidence_sha256"] = {name: digest(EVIDENCE / name) for name in evidence_files}
    result_path = EVIDENCE / "r304_A14_result.json"
    result_sha = write_new(result_path, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    result["result_json_sha256"] = result_sha
    markdown = (
        "# R304-A14 可行性审计结果\n\n"
        "这是受 R46/R49 启发的事后探索性假设，不是独立发现，也没有 alpha 结论。\n\n"
        "## 样本与覆盖\n\n"
        "- 17 组常规调样已列出，官方调入名单闭合 16 组。2022-06 公告 ID 14225 确认 2022-05-27 发布、28 只调入、2022-06-10 收市后生效；官方代码表未归档。本地 R78 在 2022-06-10 到 2022-06-13 的名单差异为 28 只，只能作为本地复原。\n"
        "- 2025-02、2025-07、2026-09 的临时调整单独排除。没有把公告日当作退市生效日。\n"
        "- 检查 2,813 个调入股窗口股票日组合，缺 1 条：2020-06-10 的 600745。其他 16 组代码日期覆盖完整，其中 2022-06 使用的是本地名单。日线审计仅读 `symbol`、`date`。\n"
        "- 对有 R78 事件后快照的 11 组比较，官方名单与保存的成员差异在 180 日内均有精确代码集匹配；R78 不是逐事件及时点精确档案，2022-12 的匹配日期为 2023-03-20。\n"
        "- R157/R78 有公告前 300 只成员快照，`update_date` 不晚于 `query_date`。这只证明存在候选对照池；行业匹配定义和来源仍需单独核实。\n\n"
        "## 结论\n\n"
        "按预注册门槛，本轮停止在可行性阶段。官方 2022-06 名单未闭合，2020-06 有一条日线缺失；不删事件、不补造官方名单，不进入收益计算或系统回测。因此当前没有收益结论。\n\n"
        f"面板 GET/POST/GET 均 HTTP 200，候选数 21→22；R304、R304-A13 与原有候选状态未变，新候选 `{registration['candidate']['id']}` 为 `pending`。原始请求、响应及 GET 读回均已保存在证据目录。\n\n"
        "机器记录见 `r304_A14_result.json`，SHA-256：`" + result_sha + "`。\n"
    )
    report_path = EVIDENCE / "r304_A14_result.md"
    report_sha = write_new(report_path, markdown.encode("utf-8"))
    print(json.dumps({"result_json_sha256": result_sha, "result_markdown_sha256": report_sha}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
