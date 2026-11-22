from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r502_industry_size_neutral_leg_replacement_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r502_industry_size_neutral_leg_replacement.py"


def _module():
    spec = importlib.util.spec_from_file_location("r502_replace", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r502_frozen_scope_and_null_policy() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R502"
    assert len(payload["replacements"]) == 1
    assert payload["replacements"][0]["remove"] == "ext_alt_hs300_cash_flow_stability_4q_low"
    assert payload["replacements"][0]["add"] == "ext_alt_hs300_ocf_margin_4q"
    assert payload["base_weights"]["value_quality"] == 0.05
    assert payload["fixed_execution"]["no_leverage"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r502_replacement_weights_are_normalized() -> None:
    module = _module()
    base = module.base_weights()
    replaced = module.replaced_weights(
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_ocf_margin_4q",
    )
    assert abs(sum(base.values()) - 1.0) < 1e-12
    assert abs(sum(replaced.values()) - 1.0) < 1e-12
    assert "ext_alt_hs300_cash_flow_stability_4q_low" not in replaced
    assert (
        replaced["ext_alt_hs300_ocf_margin_4q"] == base["ext_alt_hs300_cash_flow_stability_4q_low"]
    )


def test_r502_outputs_are_confirmation_only() -> None:
    output = E / "r502_industry_size_neutral_leg_replacement_2026-11-22.json"
    result = E / "r502_industry_size_neutral_leg_replacement_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
