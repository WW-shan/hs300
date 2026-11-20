from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r382_pit_trade_audit.py"


def _module():
    spec = importlib.util.spec_from_file_location("r382_pit_trade_audit", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entry_industry_uses_only_the_latest_effective_label() -> None:
    module = _module()
    history = pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ", "600000.SH"],
        "effective_date": [date(2020, 1, 1), date(2022, 7, 1), date(2020, 1, 1)],
        "industry_pit": ["48", "43", "45"],
    })
    trades = [
        {"symbol": "000001.SZ", "entry_date": "2022-06-30"},
        {"symbol": "000001.SZ", "entry_date": "2022-07-01"},
        {"symbol": "600000.SH", "entry_date": "2019-12-31"},
    ]

    enriched = module.attach_entry_industry(trades, history)

    assert [row["industry_pit"] for row in enriched] == ["48", "43", "未知"]


def test_concentration_uses_portfolio_weighted_pnl_instead_of_raw_account_pnl() -> None:
    module = _module()
    trades = [
        {
            "symbol": "000001.SZ",
            "name": "A",
            "industry_pit": "48",
            "pnl_amount": 10_000.0,
            "portfolio_pnl_amount": 100.0,
        },
        {
            "symbol": "600000.SH",
            "name": "B",
            "industry_pit": "45",
            "pnl_amount": 100.0,
            "portfolio_pnl_amount": 50.0,
        },
    ]

    result = module.aggregate_concentration(trades)

    assert result["total_pnl_amount"] == 150.0
    assert result["top1_symbol"]["symbol"] == "000001.SZ"
    assert abs(result["top1_gross_share"] - 100.0 / 150.0) < 1e-6
    assert abs(result["top_industry_gross_share"] - 100.0 / 150.0) < 1e-6


def test_unknown_industry_counts_against_trade_label_coverage() -> None:
    module = _module()
    trades = [
        {"symbol": "000001.SZ", "name": "A", "industry_pit": "48", "portfolio_pnl_amount": 1.0},
        {"symbol": "600000.SH", "name": "B", "industry_pit": "未知", "portfolio_pnl_amount": 1.0},
    ]

    result = module.aggregate_concentration(trades)
    checks = module.evaluate_concentration_gates(result)

    assert result["industry_trade_label_coverage"] == 0.5
    assert checks["industry_trade_label_coverage"] is False


def test_curve_reconciliation_requires_exact_r381_metrics_and_trade_counts() -> None:
    module = _module()
    r381 = {
        "current_portfolio": {
            "center_2x": {
                "full": {"annual": 0.1, "sharpe": 1.2, "mdd": -0.08},
                "recent": {"total_return": 0.04},
                "sealed": {"total_return": 0.2},
            },
            "control_2x": {
                "full": {"annual": 0.09, "sharpe": 1.1, "mdd": -0.09},
                "sealed": {"total_return": 0.18},
            },
            "center_3x": {"full": {"annual": 0.09, "sharpe": 1.1, "mdd": -0.09}},
        },
        "run_summary": {
            "A_2x": {"trades": 290},
            "B30_2x": {"trades": 732},
            "C_2x_0": {"trades": 800},
            "C_2x_1": {"trades": 800},
            "C_2x_2": {"trades": 867},
        },
    }
    current = {
        "center_2x": {
            "full": {"annual": 0.1, "sharpe": 1.2, "mdd": -0.08},
            "recent": {"total_return": 0.04},
            "sealed": {"total_return": 0.2},
        },
        "control_2x": {
            "full": {"annual": 0.09, "sharpe": 1.1, "mdd": -0.09},
            "sealed": {"total_return": 0.18},
        },
        "center_3x": {"full": {"annual": 0.09, "sharpe": 1.1, "mdd": -0.09}},
    }

    result = module.reconcile_r381(current, r381)

    assert result["curve_pass"] is True
    assert result["trade_count_pass"] is True
    assert result["all_pass"] is True

    current["center_2x"]["full"]["sharpe"] += 1e-6
    failed = module.reconcile_r381(current, r381)
    assert failed["all_pass"] is False


def test_r382_evidence_is_hash_bound_and_no_r380_concentration_is_reused() -> None:
    module = _module()
    payload = module.load_report()
    for name in (
        "r382_preregistration.json",
        "r382_pit_trade_audit.json",
        "r382_result.md",
        "run_r382_pit_trade_audit.py",
    ):
        assert len(module.verify_checksum(EVIDENCE / name)) == 64
    assert module.verify_checksum(EVIDENCE / "r382_preregistration.json") == payload["preregistration_sha256"]
    assert payload["round"] == "R382"
    assert payload["input_integrity_pass"] is True
    assert payload["reconciliation_pass"] is True
    assert payload["audit_method"]["pnl_basis"] == "portfolio_pnl_amount"
    assert payload["audit_method"]["industry_basis"] == "entry_date_pit_industry"
    assert payload["r380_method_correction"]["reported_pnl_basis"] == "unscaled_pnl_amount"
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
