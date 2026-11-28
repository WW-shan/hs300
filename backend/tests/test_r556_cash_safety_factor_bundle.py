from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r556_cash_safety_factor_bundle.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r556_cash_safety_factor_bundle", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r556_declares_statistical_screen_without_return_or_gbm_claims() -> None:
    module = _runner()
    design = module.scientific_design()

    assert module.RESEARCH_ID == "R556B"
    assert module.RESEARCH_TYPE == "statistical_screen"
    assert design["standard_validation"]["gbm"]["required"] is False
    assert design["standard_validation"]["parameter_mc"]["required"] is False


def test_r556_uses_the_rank_composite_for_reconciliation() -> None:
    module = _runner()
    assert module.FACTOR_ID == "uf_r550_cash_safety_rank"
    assert module.FACTOR_IDS[0] == "uf_r550_cash_safety_rank"


def test_r556_factor_bundle_definitions_are_unique_and_r550_scoped() -> None:
    module = _runner()
    ids = module.FACTOR_IDS
    assert len(ids) == len(set(ids))
    assert len(ids) == 11
    assert all(factor_id.startswith(("uf_r550_", "cf_r550_")) for factor_id in ids)
