from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r388_frozen_turnover_attribution.py"
PREREG = EVIDENCE / "r388_turnover_attribution_preregistration.json"
REPORT = EVIDENCE / "r388_turnover_attribution.json"
RESULT = EVIDENCE / "r388_turnover_attribution_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r388_frozen_turnover_attribution", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_freezes_parameters_and_warning_only_cost() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R388"
    assert payload["freeze"]["parameter_search_allowed"] is False
    assert payload["freeze"]["strategy_change_allowed"] is False
    assert payload["freeze"]["parameters"] == {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 110,
        "overlay_target_vol": 0.17,
        "overlay_equity_weight_max": 0.37,
    }
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["output_policy"]["status"] == "attribution_complete_no_decision"
    assert payload["output_policy"]["orders_generated"] is False
    assert payload["output_policy"]["paper_target_generated"] is False
    assert payload["output_policy"]["production_enabled"] is False


def test_sleeve_turnover_uses_summed_notional_and_exact_cost_formula() -> None:
    module = _module()
    trade_rows = {
        "A": {
            "window_trades": {
                "buy_notional": 100.0,
                "sell_notional": 80.0,
                "entry_count": 2,
                "exit_count": 1,
            }
        },
        "B": {
            "window_trades": {
                "buy_notional": 50.0,
                "sell_notional": 40.0,
                "entry_count": 1,
                "exit_count": 1,
            }
        },
        "C": [
            {"window_trades": {"buy_notional": 10.0, "sell_notional": 5.0, "entry_count": 1, "exit_count": 0}},
            {"window_trades": {"buy_notional": 20.0, "sell_notional": 15.0, "entry_count": 1, "exit_count": 1}},
            {"window_trades": {"buy_notional": 30.0, "sell_notional": 25.0, "entry_count": 1, "exit_count": 1}},
        ],
    }

    result = module.attribute_sleeves(trade_rows, "2x", initial_capital=1000.0)

    assert result["total_buy_notional"] == pytest.approx(210.0)
    assert result["total_sell_notional"] == pytest.approx(165.0)
    assert result["one_way_turnover"] == pytest.approx(0.1875)
    assert result["sleeves"]["B"]["share_of_total_turnover"] == pytest.approx(90.0 / 375.0)
    assert result["estimated_stock_cost"] == pytest.approx(
        210.0 * (0.0004 + 10.0 / 10000.0)
        + 165.0 * (0.0004 + 0.0005 + 10.0 / 10000.0)
    )


def test_five_x_is_warning_only_even_when_turnover_is_high() -> None:
    module = _module()
    result = module.turnover_warning(
        {"one_way_turnover": 9.0},
        {"one_way_turnover_warning_threshold": 5.0, "five_x_is_warning_only": True},
        cost="5x",
    )

    assert result["warning"] is True
    assert result["hard_rejection"] is False


def test_runner_rejects_stale_r387_window(tmp_path, monkeypatch) -> None:
    module = _module()
    payload = json.loads(module.REPORT.read_text(encoding="utf-8"))
    payload["window"]["end"] = "2026-11-19"
    stale = tmp_path / "r387.json"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "REPORT", stale)

    with pytest.raises(RuntimeError, match="window"):
        module.verify_r387_inputs()


def test_runner_refuses_to_overwrite_existing_evidence(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r388.json"
    result = tmp_path / "r388.md"
    monkeypatch.setattr(module, "OUTPUT", output)
    monkeypatch.setattr(module, "RESULT", result)

    module.ensure_outputs_absent()
    output.write_text("occupied", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        module.ensure_outputs_absent()


def test_committed_r388_evidence_matches_final_runner_and_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")

    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar

    assert report["round"] == "R388"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["decision"]["status"] == "attribution_complete_no_decision"
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert report["no_leverage"] is True
    assert report["costs"]["5x"]["warning"]["hard_rejection"] is False
    assert "没有订单、纸面目标或生产配置" in markdown
