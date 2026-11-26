from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r550_cash_safety_confirmation.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r550_cash_safety_confirmation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r550_declares_post_hoc_fixed_variant() -> None:
    module = _runner()
    assert module.RESEARCH_ID == "R550"
    assert module.RESEARCH_TYPE == "project_formal"
    assert module.FIXED_VARIANT == "cash_safety"
    assert module.FIXED_ADDONS == (
        "cf_r547_cash_quality",
        "cf_r547_value_quality_safety",
    )
    assert module.RESEARCH_PREREGISTRATION.endswith(
        "r550_cash_safety_confirmation_preregistration.json"
    )


def test_r550_uses_the_r549b_discovered_validation_positive_variant() -> None:
    module = _runner()
    design = module.scientific_design()
    assert design["selection_basis"] == "post_hoc_fixed_from_r549_discovery"
    assert design["recent_is_independent_oos"] is False
    assert design["execution"]["matching"] == "open_t+1"
    assert design["execution"]["cost_multiplier"] == 2
    assert design["execution"]["leverage"] == "none"
