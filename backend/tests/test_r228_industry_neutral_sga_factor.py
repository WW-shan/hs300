from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r228", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r228_industry_neutral_percentile_and_minimum_group_size() -> None:
    builder = _load_builder()
    day = date(2022, 5, 5)
    frame = pl.DataFrame({
        "symbol": [f"A{i}" for i in range(6)] + [f"B{i}" for i in range(4)] + ["C0", "C1"],
        "date": [day] * 12,
        "industry_l1": ["A"] * 6 + ["B"] * 4 + [None, None],
        "sga_ratio_4q_low": [-0.6, -0.5, -0.4, -0.3, -0.2, -0.1, -0.4, -0.3, -0.2, -0.1, -0.3, None],
    })
    result = builder._sga_ratio_4q_industry_neutral_frame(frame).sort("symbol")
    values = dict(zip(result["symbol"].to_list(), result["sga_ratio_4q_industry_neutral"].to_list(), strict=True))

    for index in range(6):
        assert math.isclose(values[f"A{index}"], index / 5.0, rel_tol=1e-12)
    for index in range(4):
        assert values[f"B{index}"] is None
    assert values["C0"] is None
    assert values["C1"] is None


def test_r228_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_industry_neutral_h20_sl20")
    control = engine.get("ai_hs300_sga_industry_neutral_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_sga_ratio_4q_industry_neutral"] == 0.45
    assert "ext_alt_hs300_sga_ratio_4q_industry_neutral" not in control.meta["scoring"]
    assert "industry_l1" in candidate.required_features
    assert "industry_l1" in control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_sga_ratio_4q_industry_neutral": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
