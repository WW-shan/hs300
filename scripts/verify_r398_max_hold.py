#!/usr/bin/env python3
"""Verify that the R398 max-hold override materially changes the backtest."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "scripts" / "research_factor_set_portfolio.py"


def module():
    spec = importlib.util.spec_from_file_location("r398_hold_probe", PORTFOLIO)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def main() -> int:
    from datetime import date
    m = module()
    service = m._service()
    members = [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
    ]
    scoring = {factor: 1 / 3 for factor in members}
    directions = {factor: "high" for factor in members}
    results = {}
    for hold in (20, 40):
        config = m._backtest_config(
            scoring,
            directions,
            start=date(2022, 5, 5),
            end=date(2023, 5, 5),
            max_positions=10,
            cost=m._cost_parameters("2x"),
            max_hold_days=hold,
        )
        result = service.run(config)
        if result.error:
            raise RuntimeError(f"hold {hold}: {result.error}")
        results[hold] = dict(result.stats)
    if results[20] == results[40]:
        raise RuntimeError("max_hold_days override did not change backtest results")
    print({"hold_20": results[20], "hold_40": results[40]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
