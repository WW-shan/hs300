from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
BASE = Path("data/strategies/ai/.iterations/ai_hs300_multi_factor_rank")
EV = ROOT / BASE / "evidence"
API = "http://127.0.0.1:3018"
GUARDS = {
    "R288": "280b43c9d2364b5589d9d0edc73d1144", "R289": "58dc4329c85e4df88271aa9b42276089",
    "R290": "2451e5a386d5489584e4121f42d97792", "R291": "4b5134a2f8a1441d8034dbb484b8e238",
    "R292": "cd6f5bc7773b44c9bedb69430f363220", "R293": "dbc1248434fb457f8312eadf47d459da",
    "R294": "4df004f0146b4e818f1f8ac16b31ebde", "R295": "51aefb61873a470a99abf7f66a614dfe",
    "R296": "48ca7b2f1105431d97c148e3e0574f4c", "R297": "25d66a13bc214247a93c233b2a32a7cd",
    "R298": "01aae1c6f0314612bec472bdd3140a69",
}


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_once(p: Path, b: bytes) -> str:
    with p.open("xb") as f:
        f.write(b)
    d = sha_bytes(b)
    with p.with_name(p.name + ".sha256").open("x", encoding="ascii") as f:
        f.write(f"{d}  {p.name}\n")
    return d


def get_json(path: str):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        return response.status, raw, json.loads(raw)


def main() -> int:
    prereg_path = EV / "r299_preregistration.json"
    result_path = EV / "r299_result.json"
    final_paths = [EV / n for n in ("r299_result.md", "r299_final_panel_readback.json", "r299_input_integrity_final.json", "r299_round_index.md")]
    if any(p.exists() or p.with_name(p.name + ".sha256").exists() for p in final_paths):
        raise FileExistsError("R299 final evidence already exists; refusing overwrite")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    for p in (prereg_path, result_path):
        sidecar = p.with_name(p.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha_file(p) != sidecar:
            raise RuntimeError(f"checksum mismatch: {p.name}")
    registration_path = EV / "r299_panel_registration.json"
    intent_path = EV / "r299_panel_registration_intent.json"
    post_path = EV / "r299_panel_registration_post.json"
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    post_proof = json.loads(post_path.read_text(encoding="utf-8"))
    for p in (registration_path, intent_path, post_path):
        sidecar = p.with_name(p.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha_file(p) != sidecar:
            raise RuntimeError(f"panel registration proof checksum mismatch: {p.name}")

    manifest_path = EV / "r299_input_manifest.jsonl"
    manifest = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest_sha = sha_file(manifest_path)
    if (not registration.get("registration_pass")
            or registration.get("intent_sha256") != sha_file(intent_path)
            or registration.get("post_proof_sha256") != sha_file(post_path)
            or intent.get("preregistration_sha256") != sha_file(prereg_path)
            or intent.get("input_manifest_sha256") != manifest_sha
            or intent.get("candidate_count_before") != 15
            or post_proof.get("http_status") != 200
            or post_proof.get("retry_policy") != "not retried"):
        raise RuntimeError("R299 single-registration proof does not match frozen inputs")
    addendum_path = EV / "r299_manifest_execution_addendum.json"
    manifest_sidecar = manifest_path.with_name(manifest_path.name + ".sha256").read_text(encoding="ascii").split()[0]
    if manifest_sha != manifest_sidecar:
        raise RuntimeError("R299 input manifest checksum mismatch")
    if not addendum_path.exists() or not addendum_path.with_name(addendum_path.name + ".sha256").exists():
        raise FileNotFoundError("R299 execution addendum and SHA-256 sidecar are required")
    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    sidecar = addendum_path.with_name(addendum_path.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha_file(addendum_path) != sidecar or addendum.get("parent_manifest_sha256") != manifest_sha:
        raise RuntimeError("R299 execution addendum checksum or parent mismatch")
    if addendum.get("round") != "R299" or addendum.get("classification") != "post_freeze_execution_harness_integrity_addendum":
        raise RuntimeError("unexpected R299 execution addendum classification")
    allowed_changes = addendum.get("execution_script_changes", [])
    allowed_by_path = {item["path"]: item for item in allowed_changes}
    if len(allowed_by_path) != len(allowed_changes):
        raise RuntimeError("duplicate path in R299 execution addendum")
    mismatches = []
    authorized_changes = []
    for item in manifest:
        p = ROOT / item["path"]
        stat = p.stat()
        actual = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha_file(p)}
        if any(actual[k] != item[k] for k in actual):
            change = allowed_by_path.get(item["path"])
            authorized = (change is not None
                          and change.get("original_sha256") == item["sha256"]
                          and change.get("original_bytes") == item["bytes"]
                          and change.get("original_mtime_ns") == item["mtime_ns"]
                          and change.get("current_sha256") == actual["sha256"]
                          and change.get("current_bytes") == actual["bytes"]
                          and change.get("current_mtime_ns") == actual["mtime_ns"]
                          and change.get("change_class") == "execution_harness_integrity_only")
            if authorized:
                authorized_changes.append({"path": item["path"], "expected": item, "actual": actual,
                                          "change_class": change["change_class"]})
            else:
                mismatches.append({"path": item["path"], "expected": item, "actual": actual})
    authorized_paths = {item["path"] for item in authorized_changes}
    if set(allowed_by_path) != authorized_paths:
        mismatches.append({"path": "<execution_addendum_entries>",
                           "expected_paths": sorted(allowed_by_path),
                           "authorized_changed_paths": sorted(authorized_paths)})
    integrity = {
        "round": "R299", "classification": "post_run_frozen_input_integrity_audit",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest_sha, "manifest_count": len(manifest),
        "verified_unchanged_count": len(manifest) - len(authorized_changes) - len(mismatches),
        "authorized_execution_harness_change_count": len(authorized_changes),
        "authorized_execution_harness_changes": authorized_changes,
        "execution_addendum_sha256": sha_file(addendum_path) if addendum_path.exists() else None,
        "mismatch_count": len(mismatches), "mismatches": mismatches, "pass": not mismatches,
    }
    if mismatches:
        raise RuntimeError(f"frozen input mismatch count {len(mismatches)}")

    status, raw, api = get_json("/api/backtest/candidates")
    if status != 200:
        raise RuntimeError(f"panel GET returned HTTP {status}")
    items = api.get("items", [])
    candidate = prereg["panel_candidate"]
    expected = candidate["config"]
    matches = [x for x in items if x.get("kind") == candidate["kind"] and x.get("name") == candidate["name"]
               and x.get("source_id") == candidate["source_id"] and x.get("config", {}).get("start") == expected["start"]
               and x.get("config", {}).get("end") == expected["end"]
               and x.get("config", {}).get("commission_pct") == expected["commission_pct"]
               and x.get("config", {}).get("fees_pct") == expected["fees_pct"]]
    guards = {label: next((x for x in items if x.get("id") == cid), None) for label, cid in GUARDS.items()}
    guard_pass = all(guards[k] is not None and guards[k].get("status") == "pending" for k in GUARDS)
    readback = {
        "round": "R299", "classification": "final_read_only_panel_status_recheck",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "api_base": API,
        "endpoint": "/api/backtest/candidates", "http_status": status,
        "response_sha256": sha_bytes(raw), "candidate_count_before": intent["candidate_count_before"],
        "candidate_count": len(items), "candidate_count_delta": len(items) - intent["candidate_count_before"],
        "r299_match_count": len(matches), "r299_candidate": matches[0] if len(matches) == 1 else None,
        "protected_candidate_statuses": {k: v.get("status") if v else None for k, v in guards.items()},
        "protected_statuses_pass": guard_pass,
        "all_pass": (len(items) == 16 and len(items) - intent["candidate_count_before"] == 1
                      and len(matches) == 1 and matches[0].get("status") == "pending" and guard_pass),
    }
    if not readback["all_pass"]:
        raise RuntimeError("R299 panel candidate or protected statuses failed final readback")

    integrity_sha = write_once(EV / "r299_input_integrity_final.json", (json.dumps(integrity, ensure_ascii=False, indent=2) + "\n").encode())
    panel_sha = write_once(EV / "r299_final_panel_readback.json", (json.dumps(readback, ensure_ascii=False, indent=2) + "\n").encode())
    gates = result["gates"]
    candidate_stats = result["execution"]["candidate_3x"]
    control_stats = result["execution"]["control_3x"]
    stress = result["execution"]["candidate_4x_5x"]
    concentration = result["concentration"]
    costs = result["cost_semantics"]
    report = [
        "# R299：R257 回测费用口径更正审计", "", "## 结论", "",
        f"R257 按修正费用复算后，原门槛{'全部通过' if gates['all_pass'] else '未全部通过'}。本轮是已知结果后的费用语义敏感性审计，不是新发现或独立 alpha 确认。R257 原集中度结果及 R298 发现期前亏损结论仍然有效，生产继续 pause_production。", "",
        "## 费用口径", "",
        "引擎中 commission_pct 显式设置时覆盖 fees_pct；fees_pct 仅作为未设置佣金时的兼容回退。R257/R298 同时设置 fees_pct=0.0006 和 commission_pct=0.0006，所以原回测每边只计 6bp 佣金，而不是报告写的 6bp 费用加 6bp 佣金。",
        f"- 原配置实际往返成本：{costs['declared_effective']['round_trip_pct'] * 10000:.1f}bp。",
        f"- 修正后往返成本：3x {costs['corrected_round_trip_by_stress']['3x']['round_trip_pct'] * 10000:.1f}bp；4x {costs['corrected_round_trip_by_stress']['4x']['round_trip_pct'] * 10000:.1f}bp；5x {costs['corrected_round_trip_by_stress']['5x']['round_trip_pct'] * 10000:.1f}bp。",
        "- 每次往返比原实际口径多计 3x 12bp、4x 16bp、5x 20bp。", "", "## 系统复算", "",
        f"区间 {result['window'][0]} 至 {result['window'][1]}，{result['calendar_days']} 个交易日；因子覆盖 {result['factor_coverage']:.3%}，{result['factor_days']}/{result['calendar_days']} 日有值。使用 StrategyBacktestService、原候选/匹配对照、T+1 开盘、30 名评分池、10 仓等权、20 日持有、-20% 止损，仅更正费用。",
        f"- 候选 3x：全样本 {candidate_stats['full']['total_return']:.2%}，Sharpe {candidate_stats['full']['sharpe']:.4f}，MDD {candidate_stats['full']['mdd']:.2%}；验证段 {candidate_stats['validation_2025_2026']['total_return']:.2%}。",
        f"- 对照 3x：全样本 {control_stats['full']['total_return']:.2%}，Sharpe {control_stats['full']['sharpe']:.4f}，MDD {control_stats['full']['mdd']:.2%}；验证段 {control_stats['validation_2025_2026']['total_return']:.2%}。",
        f"- 候选压力成本：4x {stress['4x']['total_return']:.2%}；5x {stress['5x']['total_return']:.2%}。",
        f"- 候选 {concentration['n_trades']} 笔、{concentration['distinct_symbols']} 只股票；头部 1/3/5 只净 P&L 份额 {concentration['top1_net_share']:.2%}/{concentration['top3_net_share']:.2%}/{concentration['top5_net_share']:.2%}。", "", "## 门槛", "",
        "因子与 IC 门槛沿用 R257 已冻结结果；收益、风险、成本和集中度门槛按新曲线重算，阈值未调整。",
        ", ".join(f"{k}={'通过' if v else '失败'}" for k, v in gates.items() if k != "all_pass"), "", "## 证据", "",
        f"预注册 SHA-256 {sha_file(prereg_path)}；输入清单 SHA-256 {sha_file(manifest_path)}（{len(manifest)} 项，{integrity['verified_unchanged_count']} 项未变、{integrity['authorized_execution_harness_change_count']} 个执行辅助脚本按附录核验）；执行附录 SHA-256 {integrity['execution_addendum_sha256']}；机器结果 SHA-256 {sha_file(EV / 'r299_result.json')}；面板最终 GET SHA-256 {panel_sha}；完整性审计 SHA-256 {integrity_sha}。",
        "只写入 R299 独立研究缓存与证据；未调用 provider、builder、策略/回测写 API、paper 账户或订单。面板候选只登记一次并保持 pending。", "",
    ]
    report_sha = write_once(EV / "r299_result.md", ("\n".join(report) + "\n").encode())

    proof_names = ["r299_feasibility.json", "r299_input_manifest.jsonl", "r299_manifest_execution_addendum.json", "r299_preregistration.json",
                   "r299_panel_registration_intent.json", "r299_panel_registration_post.json", "r299_panel_registration.json",
                   "r299_result.json", "r299_result.md", "r299_final_panel_readback.json", "r299_input_integrity_final.json"]
    index = ["# R299 证据索引", "", "- 分类：费用语义事后敏感性审计，不是独立 alpha 确认。",
             "- 预注册：r299_preregistration.json；冻结输入：r299_input_manifest.jsonl。",
             "- 系统回测：r299_result.json；说明：r299_result.md。",
             "- 面板：r299_panel_registration.json 和 r299_final_panel_readback.json。",
             "- 完整性：r299_input_integrity_final.json；缓存：data/research/r299_cost_audit_cache/。",
             "- 原 R257/R298 证据未覆盖；R257 保持 research-only，生产保持 pause_production。", "", "## SHA-256", ""]
    index.extend(f"- {name}: {sha_file(EV / name)}" for name in proof_names)
    index_sha = write_once(EV / "r299_round_index.md", ("\n".join(index) + "\n").encode())

    entry = (
        "\n## R299 回测费用口径更正审计（2026-10-29）\n\n"
        f"冻结输入清单 {len(manifest)} 项，完整性审计确认 {integrity['verified_unchanged_count']} 项未变、{integrity['authorized_execution_harness_change_count']} 个执行辅助脚本的哈希/大小/mtime 与事前 SHA-256 附录一致。\n\n"
        f"修正费用下候选全样本收益 {candidate_stats['full']['total_return']:.2%}、Sharpe {candidate_stats['full']['sharpe']:.4f}、MDD {candidate_stats['full']['mdd']:.2%}；对照收益 {control_stats['full']['total_return']:.2%}；候选 4x/5x 收益 {stress['4x']['total_return']:.2%}/{stress['5x']['total_return']:.2%}。集中度 C2/C3/C4 {'通过' if all(gates[k] for k in ('C2','C3','C4')) else '仍失败'}；所有原门槛 {'全部通过' if gates['all_pass'] else '未全部通过'}。本轮是事后敏感性审计，不是新 alpha 证据；R298 发现期前亏损及 PIT/多重比较限制仍在。R257 保持 research-only，生产保持 pause_production。\n\n"
        f"面板候选 {readback['r299_candidate']['id']} 单次 POST/GET 成功，HTTP 200、候选数 {len(items)}、状态 pending；R288-R298 保护项均保持 pending。证据索引 SHA-256 {index_sha}；可读报告 SHA-256 {report_sha}。\n"
    )
    for relative in (BASE / "LEDGER.md", BASE / "RESEARCH_SUMMARY.md"):
        with (ROOT / relative).open("a", encoding="utf-8", newline="") as stream:
            stream.write(entry)
    print(json.dumps({"decision": result["decision"], "gate_failures": [k for k, v in gates.items() if not v],
                      "candidate_id": readback["r299_candidate"]["id"], "candidate_count": len(items),
                      "result_sha256": sha_file(EV / "r299_result.json"), "report_sha256": report_sha,
                      "round_index_sha256": index_sha, "input_integrity_sha256": integrity_sha,
                      "panel_readback_sha256": panel_sha}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
