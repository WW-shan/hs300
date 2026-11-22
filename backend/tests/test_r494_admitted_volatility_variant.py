from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r494_admitted_volatility_variant_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r494_admitted_volatility_variant.py"


def _module():
    spec = importlib.util.spec_from_file_location("r494_admitted", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r494_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R494"
    assert payload["replaced_leg"]["removed"] == "ext_alt_hs300_vol_of_vol_20d_low"
    assert payload["replaced_leg"]["added"] == "ext_alt_hs300_vol_of_vol_60d_low"
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r494_has_exactly_one_replaced_leg() -> None:
    module = _module()
    weights, directions = module.member_weights()
    assert len(weights) == 5
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    assert "ext_alt_hs300_vol_of_vol_20d_low" not in weights
    assert "ext_alt_hs300_vol_of_vol_60d_low" in weights
    assert directions["ext_alt_hs300_vol_of_vol_60d_low"] == "high"


def test_r494_outputs_are_confirmation_only() -> None:
    output = E / "r494_admitted_volatility_variant_2026-11-22.json"
    result = E / "r494_admitted_volatility_variant_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "single_pre_registered_variant"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert "ext_alt_hs300_vol_of_vol_60d_low" in payload["weights"]
    assert "ext_alt_hs300_vol_of_vol_20d_low" not in payload["weights"]
