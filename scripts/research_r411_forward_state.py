#!/usr/bin/env python3
"""R411: read-only forward state snapshot for frozen candidate 4."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r411_forward_state_preregistration.json"
OUTPUT = E / "r411_forward_state_2026-11-22.json"
RESULT = E / "r411_forward_state_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r411_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inferred_open_positions(trades: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_symbol.setdefault(str(trade["symbol"]), []).append(trade)
    open_positions = []
    for _symbol, rows in by_symbol.items():
        rows = sorted(rows, key=lambda row: (str(row["entry_date"]), str(row["exit_date"])))
        last = rows[-1]
        if str(last["exit_date"]) >= as_of:
            open_positions.append(last)
    return open_positions


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R411 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    members = prereg["members"]
    weights = {factor: 1.0 / len(members) for factor in members}
    module = portfolio_module()
    service = module._service()
    config = module._backtest_config(
        weights,
        {factor: "high" for factor in members},
        start=date.fromisoformat(prereg["start"]),
        end=date.fromisoformat(prereg["end"]),
        max_positions=int(prereg["max_positions"]),
        cost=module._cost_parameters("2x"),
        max_hold_days=int(prereg["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    payload = {
        "round": "R411", "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG), "as_of": prereg["end"],
        "members": members, "weights": weights,
        "status": "read_only_forward_snapshot", "trade_count": len(result.trades),
        "last_trades": result.trades[-10:],
        "target_positions": inferred_open_positions(result.trades, prereg["end"]),
        "decision": {"active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False, "note": "This is a research snapshot; it is not a user paper account or an order."},
        "known_limits": [*prereg["known_limits"], "The target positions are inferred from the trade ledger; they are not an engine-level final-position snapshot."],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R411 候选 4 前瞻状态快照", "",
        f"- 数据截止：{prereg['end']}",
        f"- 核心成员：{members}",
        "- 这是只读研究快照，不是模拟账户、不是订单。", "",
        "| 最近交易 | 日期 | 收益 | 退出原因 |", "| --- | --- | ---: | --- |",
    ]
    for trade in result.trades[-10:]:
        lines.append(f"| {trade['symbol']} | {trade['exit_date']} | {trade['pnl_pct']:.2%} | {trade['exit_reason']} |")
    lines += ["", "## 边界", "", "- 当前数据截止日不是下一个交易日，不能替代未来纸面验证。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
