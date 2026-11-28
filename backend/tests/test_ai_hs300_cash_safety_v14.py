from __future__ import annotations

from pathlib import Path

from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
STRATEGY = ROOT / "data" / "strategies" / "ai" / "ai_hs300_cash_safety_v14.py"


def test_cash_safety_strategy_freezes_research_signal_and_risk_contract() -> None:
    loaded = StrategyEngine._load_file(STRATEGY)

    assert loaded.meta["id"] == "ai_hs300_cash_safety_v14"
    assert loaded.execution_backend == "matrix_native"
    assert loaded.stop_loss == -0.12
    assert loaded.max_hold_days == 60
    params = {item["id"]: item for item in loaded.meta["params"]}
    assert params["entry_score"]["default"] == 70.0
    assert params["exit_score"]["default"] == 40.0
    assert params["max_industry_positions"]["default"] == 3
    assert loaded.meta["scoring"] == {}
    assert loaded.matrix_strategy is not None
    assert loaded.matrix_strategy.required_fields_for_params({}) == frozenset(
        {"uf_r550_cash_safety_rank"}
    )
