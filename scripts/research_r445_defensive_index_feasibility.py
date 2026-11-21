#!/usr/bin/env python3
"""R445: feasibility audit for long-history defensive index rotation."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r445_defensive_index_feasibility_preregistration.json"
OUTPUT = E / "r445_defensive_index_feasibility_2026-11-22.json"
RESULT = E / "r445_defensive_index_feasibility_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R445 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    candidates = list(dict.fromkeys([*prereg["candidates"]["equity_core"], *prereg["candidates"]["equity_satellites"], *prereg["candidates"]["defensive"]]))
    frames = {}
    for symbol in candidates:
        frame = repo.get_index_daily(symbol, date(2018, 1, 1), date(2026, 11, 20), columns=["date", "close"]).drop_nulls().sort("date")
        frames[symbol] = frame
    benchmark = frames[prereg["checks"]["require_benchmark"]]
    benchmark_dates = set(benchmark["date"].to_list())
    rows = {}
    for symbol, frame in frames.items():
        dates = set(frame["date"].to_list())
        overlap = sorted(dates & benchmark_dates)
        rows[symbol] = {
            "rows": frame.height,
            "first_date": str(frame["date"].min()) if frame.height else None,
            "last_date": str(frame["date"].max()) if frame.height else None,
            "overlap_rows_with_benchmark": len(overlap),
            "overlap_first": str(overlap[0]) if overlap else None,
            "overlap_last": str(overlap[-1]) if overlap else None,
            "missing_benchmark_dates": len(benchmark_dates - dates),
        }
    passed = all(row["rows"] > 0 and row["missing_benchmark_dates"] == 0 for row in rows.values())
    payload = {
        "round": "R445",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "candidates": candidates,
        "rows": rows,
        "decision": {
            "status": "defensive_index_feasibility_complete_no_promotion" if passed else "defensive_index_feasibility_partial",
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
    lines = ["# R445 防御指数数据可行性", "", "| 指数 | 行数 | 首日 | 末日 | 与300重叠 | 缺失300交易日 |", "| --- | ---: | --- | --- | ---: | ---: |"]
    for symbol, row in rows.items():
        lines.append(f"| {symbol} | {row['rows']} | {row['first_date']} | {row['last_date']} | {row['overlap_rows_with_benchmark']} | {row['missing_benchmark_dates']} |")
    lines += ["", f"- 判定：**{'通过' if passed else '部分数据不可用'}**", "- ETF 本地历史仅 268 天，本审计使用指数长历史，不等同可交易 ETF 净值。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
