from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

from app.factors.ext_factors import ensure_synced
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r368_strategies"
PRE_REG = EVIDENCE / "r368_capacity_preregistration.json"
PREFLIGHT = EVIDENCE / "run_r368_capacity_preflight.py"
RUNNER = EVIDENCE / "run_r368_capacity.py"
CANDIDATE_ID = "ai_hs300_weak_reversal_blend_size_h20_sl20_r368"
CONTROL_ID = "ai_hs300_quality_size_h20_sl20_r368"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> StrategyEngine:
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    return StrategyEngine(strategy_dirs=[STRATEGY_DIR])


def test_r368_keeps_size_neutral_score_pair_identical_across_capacity_sweep() -> None:
    engine = _engine()
    candidate = engine.get(CANDIDATE_ID)
    control = engine.get(CONTROL_ID)
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "cf_weak_reversal_blend": 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    }
    assert control.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }
    assert candidate.meta["neutralize"] == control.meta["neutralize"] == {
        "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0
    }
    assert candidate.basic_filter == control.basic_filter
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20


def test_r368_only_changes_position_capacity_and_preserves_execution_costs() -> None:
    runner = _load_module(RUNNER, "r368_capacity_runner_contract")
    low = runner.make_backtest_config(CANDIDATE_ID, date(2022, 5, 5), 2, 10)
    high = runner.make_backtest_config(CANDIDATE_ID, date(2022, 5, 5), 2, 20)
    assert low.max_positions == 10
    assert high.max_positions == 20
    assert low.max_exposure_pct == high.max_exposure_pct == 1.0
    assert low.position_sizing == high.position_sizing == "equal"
    assert low.holding_days == high.holding_days == 20
    assert low.regime_filter == high.regime_filter == {"states": ["weak"]}
    assert low.matching == high.matching == "open_t+1"
    assert low.fees_pct == high.fees_pct == 0.0004
    assert low.commission_pct == high.commission_pct == 0.0004
    assert low.stamp_tax_pct == high.stamp_tax_pct == 0.0005
    assert low.slippage_bps == high.slippage_bps == 10.0
    assert runner.CACHE.name == "r368_cache"


def test_r368_preregistration_freezes_capacity_pair_and_no_leverage() -> None:
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    assert prereg["round"] == "R368"
    assert prereg["rule"]["position_capacities"] == [10, 20]
    assert prereg["rule"]["holding_sessions"] == 20
    assert prereg["rule"]["stop_loss"] == -0.20
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["rule"]["neutralization"] == {"styles": ["log_float_mv"], "exclude": [], "shrink": 1.0}
    assert prereg["parameter_sweep"] is False
    assert prereg["primary_window"]["cost_multipliers"] == [2, 3]
    assert len(prereg["primary_window"]["phase_starts"]) == 6
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["leverage"] == "none"
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["decision"]["paper_target_generated"] is False
    assert prereg["decision"]["orders_generated"] is False


def test_r368_manifest_and_runner_preflight_agree() -> None:
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    preflight = _load_module(PREFLIGHT, "r368_capacity_preflight_contract")
    runner = _load_module(RUNNER, "r368_capacity_runner_manifest")
    assert preflight.PRE_REG == runner.PRE_REG == PRE_REG
    assert preflight.OUTPUT == runner.PREFLIGHT
    assert preflight.input_fingerprints() == runner.input_fingerprints()
    assert prereg["frozen_inputs"]["test_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]
    assert any("r367_size_neutralization_backtest.json" in path for path in prereg["frozen_inputs"]["prior_evidence_sha256"])
    assert not any("r362_" in path for path in prereg["frozen_inputs"]["prior_evidence_sha256"])
