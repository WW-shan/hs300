#!/usr/bin/env python3
"""R469: descriptive attribution of the selected R468 operating-efficiency sleeve."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r469_operating_efficiency_attribution_preregistration.json"
SOURCE = E / "r468_operating_efficiency_sleeve_2026-11-22.json"
OUTPUT = E / "r469_operating_efficiency_attribution_2026-11-22.json"
RESULT = E / "r469_operating_efficiency_attribution_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def remove_top_trades(trades: list[dict[str, Any]], *, count: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: float(trade.get("pnl_amount") or 0.0), reverse=True)
    removed = ordered[:count]
    total = sum(float(trade.get("pnl_amount") or 0.0) for trade in trades)
    removed_total = sum(float(trade.get("pnl_amount") or 0.0) for trade in removed)
    return {
        "removed_count": len(removed),
        "removed_pnl": removed_total,
        "remaining_pnl": total - removed_total,
        "removed_symbols": [trade.get("symbol") for trade in removed],
    }


def monthly(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in curve:
        grouped[str(item["date"])[:7]].append(item)
    rows = []
    for month, items in sorted(grouped.items()):
        first = float(items[0]["value"])
        last = float(items[-1]["value"])
        rows.append({"month": month, "return": last / first - 1.0 if first else 0.0, "start": items[0]["date"], "end": items[-1]["date"]})
    return rows


def symbol_contribution(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0.0, "wins": 0.0})
    for trade in trades:
        symbol = str(trade.get("symbol") or "")
        pnl = float(trade.get("pnl_amount") or 0.0)
        grouped[symbol]["pnl"] += pnl
        grouped[symbol]["trades"] += 1
        grouped[symbol]["wins"] += int(pnl > 0)
    total = sum(item["pnl"] for item in grouped.values())
    return [
        {"symbol": symbol, "pnl": item["pnl"], "trades": int(item["trades"]), "wins": int(item["wins"]), "share_of_pnl": item["pnl"] / total if total else 0.0}
        for symbol, item in sorted(grouped.items(), key=lambda pair: pair[1]["pnl"], reverse=True)
    ]


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R469 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R469 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    selected = source["selected"]
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r469_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r469_r464")
    p5 = r464.load_p5()
    panel, symbols, _coverage = r468.build_panel(p5)
    service = p5.portfolio._service()
    sample = {
        "weights": selected["weights"],
        "direction": selected["direction"],
        "max_positions": int(selected["max_positions"]),
        "max_hold_days": int(selected["max_hold_days"]),
        "max_exposure_pct": float(selected["max_exposure_pct"]),
        "entry_score": float(selected["entry_score"]),
        "exit_score": float(selected["exit_score"]),
    }
    # Re-run with the frozen R468 sample and collect trades; R468's public
    # result intentionally strips curves but retains the selected parameters.
    result = r468.run_sample(
        p5,
        service,
        panel,
        symbols,
        sample,
        start=prereg["windows"]["test_start"],
        end=prereg["windows"]["test_end"],
        cost_label="2x",
    )
    # run_sample is curve-oriented; execute one exact engine run for trades.
    from datetime import date
    seed = next(iter(sample["weights"]))
    cost = p5.portfolio._cost_parameters("2x")
    config = p5.portfolio._backtest_config(
        sample["weights"],
        {seed: sample["direction"]},
        start=date.fromisoformat(prereg["windows"]["test_start"]),
        end=date.fromisoformat(prereg["windows"]["test_end"]),
        max_positions=int(sample["max_positions"]),
        cost=cost,
        max_hold_days=int(sample["max_hold_days"]),
    )
    config.params = {**(config.params or {}), "entry_score": float(sample["entry_score"]), "exit_score": float(sample["exit_score"])}
    config.max_exposure_pct = float(sample["max_exposure_pct"])
    config.symbols = list(symbols)
    engine_result = service.run(config)
    if engine_result.error:
        raise RuntimeError(engine_result.error)
    trades = [trade if isinstance(trade, dict) else service._trade_to_dict(trade) for trade in engine_result.trades]
    curve = list(result["curve"])
    months = monthly(curve)
    negative = [row for row in months if row["return"] < 0]
    by_symbol = symbol_contribution(trades)
    removals = {str(count): remove_top_trades(trades, count=count) for count in prereg["diagnostics"]["remove_top_profitable_trades"]}
    total_pnl = sum(float(trade.get("pnl_amount") or 0.0) for trade in trades)
    payload = {
        "round": "R469",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_round": "R468",
        "source_label": selected["label"],
        "trade_count": len(trades),
        "profitable_trades": sum(float(trade.get("pnl_amount") or 0.0) > 0 for trade in trades),
        "losing_trades": sum(float(trade.get("pnl_amount") or 0.0) <= 0 for trade in trades),
        "total_pnl_amount": total_pnl,
        "monthly": months,
        "negative_month_count": len(negative),
        "worst_month": min(months, key=lambda row: row["return"]) if months else None,
        "symbol_contribution": by_symbol,
        "top_positive_symbols": by_symbol[:10],
        "top_negative_symbols": sorted(by_symbol, key=lambda row: row["pnl"])[:10],
        "top_trade_removal": removals,
        "decision": {**prereg["output_policy"], "status": "operating_efficiency_attribution_complete_no_promotion"},
        "diagnostics": {"no_leverage": True, "five_x_warning_only": True},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R469 营运效率 sleeve 收益归因",
        "",
        f"- 源：R468 `{selected['label']}`",
        f"- 交易数：{len(trades)}（盈利 {payload['profitable_trades']} / 亏损 {payload['losing_trades']}）",
        f"- 检验期总收益：{selected['test']['stats']['total_return']:.2%}",
        f"- 负收益月份：{len(negative)}/{len(months)}",
    ]
    if payload["worst_month"]:
        lines.append(f"- 最差月份：{payload['worst_month']['month']} {payload['worst_month']['return']:.2%}")
    lines += ["", "## 去除最大盈利交易后的总盈亏", "", "| 去除数 | 去除盈亏 | 剩余盈亏 |", "| ---: | ---: | ---: |"]
    for count, row in removals.items():
        lines.append(f"| {count} | {row['removed_pnl']:.2f} | {row['remaining_pnl']:.2f} |")
    lines += ["", "## 贡献最大的股票", "", "| 股票 | 盈亏 | 交易数 | 盈利数 | 占比 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in by_symbol[:10]:
        lines.append(f"| {row['symbol']} | {row['pnl']:.2f} | {row['trades']} | {row['wins']} | {row['share_of_pnl']:.2%} |")
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
