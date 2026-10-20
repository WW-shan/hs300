from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("backtest_r239_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r239_concentration_aggregation() -> None:
    module = _load_audit()
    trades = [
        {"symbol": "A", "name": "A", "pnl_amount": 100.0},
        {"symbol": "B", "name": "B", "pnl_amount": -20.0},
        {"symbol": "C", "name": "C", "pnl_amount": 50.0},
        {"symbol": "A", "name": "A", "pnl_amount": 25.0},
    ]
    industry = {"A": "电子", "B": "医药", "C": "电子"}
    summary = module._aggregate(trades, industry)

    assert summary["n_trades"] == 4
    assert summary["distinct_symbols"] == 3
    assert summary["total_pnl_amount"] == 155.0
    assert summary["gross_profit"] == 175.0
    assert summary["gross_loss"] == 20.0
    assert summary["top1_symbol"]["symbol"] == "A"
    assert summary["top1_symbol"]["pnl_amount"] == 125.0
    assert summary["top1_net_share"] > 0.80
    assert summary["top_industry_gross_share"] > 0.85
    assert summary["max_symbol_loss_to_gross"] == pytest.approx(20.0 / 175.0, abs=1e-6)


def test_r239_strategy_is_frozen() -> None:
    module = _load_audit()
    assert module.STRATEGY == "ai_hs300_sga_nonfin_quality_h5_sl20"
    assert [segment[0] for segment in module.SEGMENTS] == ["discovery", "validation"]
