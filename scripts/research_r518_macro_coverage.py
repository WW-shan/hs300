#!/usr/bin/env python3
"""R518: macro and flow coverage audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r518_macro_coverage_preregistration.json"
ROOT_EXT = DATA / "ext_data" / "alt_hs300" / "timeseries"
OUTPUT = E / "r518_macro_coverage_2026-11-22.json"
RESULT = E / "r518_macro_coverage_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R518 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    paths = sorted(ROOT_EXT.glob("date=*/part.parquet"))
    cols = list(prereg["conditioners"])
    rows: list[dict[str, Any]] = []
    for path in paths:
        day = path.parent.name.split("=")[1]
        frame = pl.read_parquet(path, columns=["symbol", *cols]).with_columns(
            pl.lit(day).alias("date")
        )
        rows.append(frame)
    all_rows = pl.concat(rows)
    coverage: dict[str, Any] = {}
    for column in cols:
        valid = all_rows.filter(pl.col(column).is_not_null())
        dates = valid["date"].unique().sort().to_list()
        coverage[column] = {
            "dates": len(dates),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "symbols": valid["symbol"].n_unique(),
            "recent_available": bool(dates and dates[-1] >= prereg["recent_start"]),
            "full_history": len(dates) >= prereg["gates"]["full_history_dates_min"],
            "recent_complete": bool(dates and dates[-1] >= prereg["gates"]["recent_last_date_min"]),
        }
    full = sorted(
        name for name, row in coverage.items() if row["full_history"] and row["recent_complete"]
    )
    partial = sorted(
        name
        for name, row in coverage.items()
        if not (row["full_history"] and row["recent_complete"])
    )
    payload = {
        "round": "R518",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "coverage": coverage,
        "full_history_conditioners": full,
        "partial_or_stale_conditioners": partial,
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    ).encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R518 宏观与资金条件变量覆盖审计",
        "",
        "| 变量 | 日期数 | 首日 | 末日 | 近一年可用 | 全覆盖 |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for column, row in coverage.items():
        lines.append(
            f"| `{column}` | {row['dates']} | {row['first']} | {row['last']} | "
            f"{'是' if row['recent_complete'] else '否'} | {'是' if row['full_history'] else '否'} |"
        )
    lines += (
        [
            "",
            "## 可用条件变量",
            "",
            f"- 全长+近期完整：{', '.join(full) if full else '无'}",
            f"- 部分或过期：{', '.join(partial) if partial else '无'}",
            "",
            "## 边界",
            "",
        ]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
