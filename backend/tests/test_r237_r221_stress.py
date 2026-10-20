from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backtest_r237_r221_stress.py"


def _load_stress():
    spec = importlib.util.spec_from_file_location("backtest_r237_stress", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r237_stress_matrix_is_frozen() -> None:
    module = _load_stress()
    keys = [variant["key"] for variant in module.VARIANTS]
    assert len(keys) == 10
    assert keys[0] == "c3_mp5"
    assert "c3_mp10" in keys
    assert "c3_mp20" in keys
    assert "c4_mp10" in keys
    assert "c5_mp10" in keys
    assert "c6_mp10" in keys
    assert module.STRATEGY == "ai_hs300_sga_nonfin_quality_h20_sl20"
    costs = {variant["cost"] for variant in module.VARIANTS}
    positions = {variant["max_positions"] for variant in module.VARIANTS}
    assert costs == {3.0, 4.0, 5.0, 6.0}
    assert positions == {5, 10, 20}
    baseline = next(variant for variant in module.VARIANTS if variant["key"] == "c3_mp10")
    assert baseline["cost"] == 3.0
    assert baseline["max_positions"] == 10
