from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r378_cost_stress.py"
REPORT = EVIDENCE / "r378_cost_stress.json"


def _module():
    spec = importlib.util.spec_from_file_location("r378_cost_stress", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cost_stress_contract_scales_all_layers() -> None:
    module = _module()
    scenarios = module.SCENARIOS
    assert scenarios["3x"]["stock"]["fees"] > scenarios["2x"]["stock"]["fees"]
    assert scenarios["3x"]["portfolio_turnover"] > scenarios["2x"]["portfolio_turnover"]
    assert scenarios["3x"]["defensive_turnover"] > scenarios["2x"]["defensive_turnover"]
    assert scenarios["5x"]["portfolio_turnover"] > scenarios["3x"]["portfolio_turnover"]
    assert module.CENTRE == "fade05_h30_sl20"
    assert module.CONTROL == "fade05_h20_sl20"


def test_r378_evidence_is_hash_bound_and_3x_is_explicit() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for path in (
        EVIDENCE / "r378_preregistration.json",
        REPORT,
        EVIDENCE / "r378_result.md",
    ):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R378"
    assert payload["robust_3x"] is True
    assert all(payload["checks_3x"].values())
    assert payload["five_x_diagnostic"]["hard_rejection"] is False
    assert payload["rows"]["2x"]["fade05_h30_sl20"]["total_return"] > 0
    assert payload["decision"]["sealed_advantage_robust_at_3x"] is True
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
