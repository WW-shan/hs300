#!/usr/bin/env python3
"""R436: independent PIT audit for extended financial factor builders."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r436_extended_financial_pit_preregistration.json"
OUTPUT = E / "r436_extended_financial_pit_2026-11-22.json"
RESULT = E / "r436_extended_financial_pit_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_builder():
    path = ROOT / "scripts" / "build_alt_factor_data.py"
    spec = importlib.util.spec_from_file_location("r436_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def audit_frame(frame: pl.DataFrame, value_column: str) -> dict[str, object]:
    valid = frame.filter(pl.col(value_column).is_not_null()).sort(["symbol", "date"])
    invalid_after = valid.filter(pl.col("date") < pl.col("date").min())
    per_symbol = []
    for symbol in valid["symbol"].unique().to_list():
        rows = valid.filter(pl.col("symbol") == symbol).sort("date")
        if rows.is_empty():
            continue
        per_symbol.append({
            "symbol": symbol,
            "first_value_date": str(rows["date"].min()),
            "last_value_date": str(rows["date"].max()),
            "non_null_count": rows.height,
        })
    return {
        "rows": frame.height,
        "non_null_rows": valid.height,
        "invalid_before_first_date": invalid_after.height,
        "symbol_count": len(per_symbol),
        "symbols": per_symbol,
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R436 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R436 preregistration checksum mismatch")
    builder = load_builder()
    start = date.fromisoformat(prereg["windows"]["start"])
    end = date.fromisoformat(prereg["windows"]["end"])
    all_dates, symbols = builder._calendar()
    dates = [day for day in all_dates if start <= day <= end]
    factors = {
        "ext_alt_hs300_cash_flow_stability_4q_low": ("cash_flow_stability_4q_low", builder._cash_flow_stability_daily),
        "ext_alt_hs300_ocf_margin_4q": ("ocf_margin_4q", builder._ocf_margin_4q_daily),
    }
    results = {}
    for factor_id, (column, function) in factors.items():
        full = function(dates, symbols).sort(["symbol", "date"])
        audit = audit_frame(full, column)
        cutoff = dates[len(dates) // 2]
        prefix_dates = [day for day in dates if day <= cutoff]
        prefix = function(prefix_dates, symbols).sort(["symbol", "date"])
        full_prefix = full.filter(pl.col("date") <= cutoff)
        prefix_equal = prefix.equals(full_prefix)
        results[factor_id] = {
            "column": column,
            "audit": audit,
            "prefix_cutoff": str(cutoff),
            "prefix_invariance": prefix_equal,
        }
    passed = all(
        item["audit"]["non_null_rows"] > 0
        and item["audit"]["invalid_before_first_date"] == 0
        and item["prefix_invariance"]
        for item in results.values()
    )
    payload = {
        "round": "R436",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "windows": prereg["windows"],
        "results": results,
        "decision": {
            "status": "extended_financial_pit_audit_complete_no_promotion" if passed else "extended_financial_pit_audit_failed",
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
    lines = ["# R436 扩展财务因子 PIT 审计", "", "| 因子 | 非空行 | 首值日 | 截止日前异常 | 前缀不变 |", "| --- | ---: | --- | ---: | :---: |"]
    for factor, item in results.items():
        first = min((row["first_value_date"] for row in item["audit"]["symbols"]), default="-")
        lines.append(f"| {factor} | {item['audit']['non_null_rows']} | {first} | {item['audit']['invalid_before_first_date']} | {'是' if item['prefix_invariance'] else '否'} |")
    lines += ["", f"- 判定：**{'通过' if passed else '失败'}**", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
