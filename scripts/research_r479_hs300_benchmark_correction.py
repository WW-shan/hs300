#!/usr/bin/env python3
"""R479: append-only HS300 benchmark correction for R477."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r479_hs300_benchmark_correction_preregistration.json"
R477 = E / "r477_frozen_library_diagnostics_2026-11-22.json"
OUTPUT = E / "r479_hs300_benchmark_correction_2026-11-22.json"
RESULT = E / "r479_hs300_benchmark_correction_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R479 output exists; refusing overwrite: {path.name}")


def load_hs300_closes(start: str, end: str) -> pl.DataFrame:
    paths = sorted((ROOT / "data" / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("data/kline_index_daily")
    frame = (
        pl.scan_parquet(
            [str(path) for path in paths],
            extra_columns="ignore",
        )
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    if frame.is_empty():
        raise RuntimeError("000300.SH benchmark has no rows in the requested window")
    return frame


def total_return(frame: pl.DataFrame) -> float:
    first = float(frame["close"][0])
    last = float(frame["close"][-1])
    return round(last / first - 1.0, 6)


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R477)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r477 = json.loads(R477.read_text(encoding="utf-8"))
    windows: dict[str, Any] = {}
    for window in ("test", "recent"):
        start = (
            prereg["windows"]["test_start"]
            if window == "test"
            else prereg["windows"]["recent_start"]
        )
        end = prereg["windows"]["end"]
        hs300 = load_hs300_closes(start, end)
        strategy_return = float(r477["runs"][window]["stats"]["total_return"])
        hs300_return = total_return(hs300)
        windows[window] = {
            "start": start,
            "end": end,
            "strategy_total_return": strategy_return,
            "hs300_total_return": hs300_return,
            "excess_return": round(strategy_return - hs300_return, 6),
            "superseded_shanghai_total_return": r477["diagnostics"][window]["benchmark_return"],
            "hs300_rows": hs300.height,
            "hs300_first_date": str(hs300["date"][0]),
            "hs300_last_date": str(hs300["date"][-1]),
        }
    payload = {
        "round": "R479",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r477_sha256": sha(R477),
        "benchmark_symbol": prereg["benchmark"]["symbol"],
        "benchmark_name": prereg["benchmark"]["name"],
        "superseded_benchmark_symbol": prereg["superseded_benchmark"]["symbol"],
        "windows": windows,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R479 沪深300 基准口径更正",
        "",
        "R477 原 `benchmark_return` 实际来自引擎的上证指数 `000001.SH`。本轮只做 append-only 更正，不重跑、不重选策略。",
        "",
        "| 窗口 | 策略总收益 | 沪深300 总收益 | 超额 | 原上证口径 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for window, item in windows.items():
        lines.append(
            f"| {window} | {item['strategy_total_return']:.2%} | "
            f"{item['hs300_total_return']:.2%} | {item['excess_return']:+.2%} | "
            f"{item['superseded_shanghai_total_return']:.2%} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "- 基准统一改为 `000300.SH`；R477 的历史上证口径仅作为旧标签保留。",
        "- 校正不改变策略结论：R476/R477 仍为拒绝，`active_version` 仍为 null。",
        "",
        "## 边界",
        "",
    ] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
