#!/usr/bin/env python3
"""R437: evidence-based future-data audit for extended financial builders."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r437_extended_financial_future_data_preregistration.json"
R436 = E / "r436_extended_financial_pit_2026-11-22.json"
BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
OUTPUT = E / "r437_extended_financial_future_data_2026-11-22.json"
RESULT = E / "r437_extended_financial_future_data_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function_source(source: str, name: str) -> str:
    lines = source.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = index
            break
    if start is None:
        raise RuntimeError(f"builder function not found: {name}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("def "):
            end = index
            break
    return "\n".join(lines[start:end])

def audit_function(source: str, name: str) -> dict[str, bool]:
    body = function_source(source, name)
    return {
        "builder_uses_statutory_deadline": "_statutory_deadline" in body,
        "effective_date_after_deadline": "+ pl.duration(days=1)" in body or 'offset_by("3d")' in body,
        "join_asof_backward_only": 'strategy="backward"' in body,
        "no_provider_announce_timestamp_mixing": "announce_date" not in body,
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R437 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = BUILDER.read_text(encoding="utf-8")
    r436 = json.loads(R436.read_text(encoding="utf-8"))
    checks = {
        "ext_alt_hs300_cash_flow_stability_4q_low": audit_function(source, "_cash_flow_stability_daily"),
        "ext_alt_hs300_ocf_margin_4q": audit_function(source, "_ocf_margin_4q_daily"),
    }
    results = {}
    for factor, evidence in checks.items():
        pit_ok = r436.get("results", {}).get(factor, {}).get("audit", {}).get("invalid_before_first_date") == 0
        prefix_ok = r436.get("results", {}).get(factor, {}).get("prefix_invariance") is True
        results[factor] = {
            "source_evidence": evidence,
            "r436_pit_ok": pit_ok,
            "r436_prefix_ok": prefix_ok,
            "future_data_clear": all(evidence.values()) and pit_ok and prefix_ok,
        }
    passed = all(item["future_data_clear"] for item in results.values())
    payload = {
        "round": "R437",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "r436_sha256": sha(R436),
        "builder_sha256": sha(BUILDER),
        "results": results,
        "decision": {
            "status": "future_data_audit_complete_no_promotion" if passed else "future_data_audit_failed",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R437 扩展财务因子未来数据审计", "", "| 因子 | 法定截止日 | 截止日后生效 | backward join | 无 announce 混用 | R436 PIT | 结论 |", "| --- | :---: | :---: | :---: | :---: | :---: | --- |"]
    for factor, item in results.items():
        e = item["source_evidence"]
        lines.append(f"| {factor} | {'是' if e['builder_uses_statutory_deadline'] else '否'} | {'是' if e['effective_date_after_deadline'] else '否'} | {'是' if e['join_asof_backward_only'] else '否'} | {'是' if e['no_provider_announce_timestamp_mixing'] else '否'} | {'通过' if item['r436_pit_ok'] else '失败'} | {'clear' if item['future_data_clear'] else 'unknown'} |")
    lines += ["", f"- 判定：**{'通过' if passed else '失败'}**", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
