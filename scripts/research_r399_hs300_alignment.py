#!/usr/bin/env python3
"""R399: align frozen R398 results to the actual HS300 index."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
SOURCE = E / "r398_strict_mc_2026-11-22.json"
ROLLING = E / "r398_rolling_2026-11-22.json"
PREREG = E / "r399_hs300_alignment_preregistration.json"
OUTPUT = E / "r399_hs300_alignment_2026-11-22.json"
RESULT = E / "r399_hs300_alignment_2026-11-22.md"
SYMBOL = "000300.SH"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_return(start: date, end: date) -> float | None:
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(SYMBOL, start, end, columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        return None
    return float(frame["close"][-1] / frame["close"][0] - 1.0)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R399 output exists: {path.name}")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    if not PREREG.exists():
        prereg = {
            "round": "R399",
            "classification": "benchmark alignment to actual HS300 index; no promotion",
            "source_sha256": sha(SOURCE),
            "index_symbol": SYMBOL,
            "index_identity": "CSI 300 / 沪深300",
            "correction": "R398 benchmark_return was from 000001.SH (上证指数), not HS300.",
            "output_policy": {"status": "hs300_alignment_complete_no_promotion", "active_version": None},
        }
        raw = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        PREREG.write_bytes(raw)
        PREREG.with_name(PREREG.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {PREREG.name}\n", encoding="ascii")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R399 preregistration changed")
    candidates = source["validation_stable_candidates"]
    aligned = []
    for row in candidates:
        benchmark = index_return(date(2025, 5, 6), date(2026, 11, 5))
        if benchmark is None:
            raise RuntimeError("HS300 index history unavailable")
        aligned.append({
            "candidate_id": row["id"], "members": row["members"],
            "max_positions": row["max_positions"], "max_hold_days": row["max_hold_days"],
            "strategy_total_return_2x": row["test_2x"]["total_return"],
            "strategy_annual_2x": row["test_2x"]["annual"],
            "strategy_sharpe_2x": row["test_2x"]["sharpe"],
            "strategy_mdd_2x": row["test_2x"]["mdd"],
            "hs300_total_return": benchmark,
            "hs300_excess": row["test_2x"]["total_return"] - benchmark,
            "absolute_pass": row["test_gate"],
            "hs300_excess_pass": row["test_2x"]["total_return"] > benchmark,
        })
    # Attach HS300 relative performance to the frozen rolling windows.
    rolling_copy = json.loads(ROLLING.read_text(encoding="utf-8"))
    for report in rolling_copy["reports"]:
        for window in report["windows"]:
            benchmark = index_return(date.fromisoformat(window["start"]), date.fromisoformat(window["end"]))
            window["hs300_return"] = benchmark
            window["hs300_excess"] = window["2x"]["total_return"] - benchmark if benchmark is not None else None
    payload = {
        "round": "R399", "classification": "benchmark alignment to actual HS300 index; no promotion",
        "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG),
        "source_sha256": sha(SOURCE), "rolling_source_sha256": sha(ROLLING), "index_symbol": SYMBOL,
        "test_window": {"start": "2025-05-06", "end": "2026-11-05"},
        "candidates": aligned,
        "hs300_pass_count": sum(row["hs300_excess_pass"] for row in aligned),
        "rolling_reports": rolling_copy["reports"],
        "decision": {"status": "hs300_alignment_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    test_benchmark = index_return(date(2025, 5, 6), date(2026, 11, 5))
    lines = [
        "# R399 HS300 基准对齐", "",
        f"真实基准：`{SYMBOL}`（沪深300）。R398 原先 `benchmark_return` 是 `000001.SH`（上证指数），本报告修正该口径。", "",
        f"- 盲测区间沪深300收益：{test_benchmark:.2%}",
        "- 最近一年（2025-05-06..2026-11-05）HS300 大涨，前三名候选虽为正收益，但都没有跑赢 HS300；不能表述为最近一年全面跑赢基准。",
        f"- R398 绝对门槛通过：{source['test_pass_count']}",
        f"- 扣除 HS300 后超额为正：{payload['hs300_pass_count']}",
        "", "| 候选 | 仓数 | 持有 | 策略收益 | HS300 | 超额 | 策略Sharpe | HS300超额通过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in sorted(aligned, key=lambda x: (-x["hs300_excess"], -x["strategy_sharpe_2x"])):
        lines.append(f"| {row['candidate_id']} | {row['max_positions']} | {row['max_hold_days']} | {row['strategy_total_return_2x']:.2%} | {row['hs300_total_return']:.2%} | {row['hs300_excess']:+.2%} | {row['strategy_sharpe_2x']:.3f} | {'是' if row['hs300_excess_pass'] else '否'} |")
    lines += ["", "## 结论边界", "", "- 这是基准口径修正，不重新选择因子或参数。", "- 当前 HS300 名单回填历史仍有幸存者偏差。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
