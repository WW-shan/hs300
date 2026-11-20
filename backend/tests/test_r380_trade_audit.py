from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r380_trade_audit_v2.py"
REPORT = EVIDENCE / "r380_trade_audit_v2.json"


def _module():
    spec = importlib.util.spec_from_file_location("r380_trade_audit_v2", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hard_execution_misses_do_not_count_no_slot_or_reentry() -> None:
    module = _module()
    stats_payload = {
        "execution": {
            "buy_limit_up": 5,
            "buy_suspended": 0,
            "buy_invalid_price": 0,
            "buy_cash": 0,
            "buy_exposure": 0,
            "buy_no_slot": 999,
            "buy_same_day_reentry": 500,
            "sell_limit_down": 1,
            "sell_suspended": 0,
            "sell_invalid_price": 0,
        }
    }
    trades = [{"blocked_exit_days": 0} for _ in range(95)]
    result = module.execution_audit(stats_payload, trades)

    assert result["hard_entry_misses"] == 5
    assert result["hard_exit_misses"] == 1
    assert result["hard_entry_miss_rate"] == 0.05
    assert abs(result["hard_exit_miss_rate"] - 1 / 96) < 1e-12
    assert result["no_slot_count"] == 999
    assert result["same_day_reentry_count"] == 500


def test_trade_scaling_applies_sleeve_weight_once() -> None:
    module = _module()
    bundle = {
        "risk_dates": [date(2024, 1, 2), date(2024, 1, 3)],
        "rebalances": [{"date": "2024-01-02", "target_equity_weight": 1.0}],
        "sleeve_weights": [{"date": "2024-01-02", "weight_a": 0.3, "weight_b": 0.3, "weight_c": 0.1}],
    }
    trade = {
        "sleeve": "C",
        "symbol": "000001.SZ",
        "entry_date": "2024-01-02",
        "exit_date": "2024-01-03",
        "entry_value": 100.0,
        "exit_value": 110.0,
        "pnl_amount": 10.0,
    }
    scaled = module.scale_trades([trade], bundle)[0]

    # C cohort entry_value is already on the one-third cohort account; applying
    # an extra 1/3 here would understate the C sleeve by three times.
    assert scaled["portfolio_entry_notional"] == 10.0
    assert scaled["portfolio_exit_notional"] == 11.0
    assert scaled["portfolio_pnl_amount"] == 1.0


def test_capacity_is_monotonic_and_reports_the_maximum_passing_level() -> None:
    module = _module()
    trade = {
        "symbol": "000001.SZ",
        "entry_date": "2024-01-02",
        "exit_date": "2024-01-03",
        "portfolio_entry_notional": 100_000.0,
        "portfolio_exit_notional": 100_000.0,
    }
    amounts = {
        ("000001.SZ", date(2024, 1, 2)): 100_000_000.0,
        ("000001.SZ", date(2024, 1, 3)): 100_000_000.0,
    }
    result = module.capacity_audit([trade], amounts)

    p95 = [row["p95_participation"] for row in result["capital_levels"]]
    assert p95 == sorted(p95)
    assert result["maximum_passing_level_cny"] is not None
    assert len(result["missing_daily_amount"]) == 0


def test_r380_evidence_is_hash_bound_and_explicit_about_engine_vintage() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for name in (
        "r380_preregistration.json",
        "r380_attempt1_failure.json",
        "r380_preregistration_amendment_A1.json",
        "r380_trade_audit_v2.json",
        "r380_result_v2.md",
    ):
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R380"
    assert payload["amendment"] == "A1"
    assert payload["old_engine_vintage_label"] == "pre_fix_vintage_R374_R379"
    assert payload["reconciliation_pass"] is True
    assert len(payload["reconciliation_checks"]) == 15
    assert all(payload["reconciliation_checks"].values())
    assert payload["audit_checks"] == {"concentration": True, "execution": True, "capacity": True}
    assert payload["all_pass"] is True
    assert payload["decision"]["status"] == "candidate_for_paper_trading_review"
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False


def test_current_engine_audit_numbers_and_capacity_limits() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    current = payload["current_portfolio"]
    assert current["center_2x"]["full"]["annual"] > current["control_2x"]["full"]["annual"]
    assert current["center_2x"]["sealed"]["total_return"] > current["control_2x"]["sealed"]["total_return"]
    assert current["center_3x"]["full"]["sharpe"] >= 0.80
    assert payload["trade_counts"]["total"] == 3484
    assert payload["trade_counts"]["C"] == 2467
    assert payload["concentration_full"]["distinct_symbols"] >= 80
    assert payload["concentration_full"]["top5_gross_share"] <= 0.75
    assert payload["execution"]["hard_entry_miss_rate"] <= 0.05
    assert payload["execution"]["hard_exit_miss_rate"] == 0.0
    assert payload["capacity"]["one_hundred_million_gate_passes"] is True
    assert payload["capacity"]["maximum_passing_level_cny"] == 250_000_000
