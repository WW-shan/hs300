from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r495_admitted_five_factor_tail_confirmation_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r495_admitted_five_factor_tail_confirmation.py"


def _module():
    spec = importlib.util.spec_from_file_location("r495_admitted", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r495_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R495"
    assert len(payload["members"]) == 5
    assert "ext_alt_hs300_vol_of_vol_20d_low" not in payload["members"]
    assert "ext_alt_hs300_vol_of_vol_60d_low" in payload["members"]
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r495_weights_are_admitted_only() -> None:
    module = _module()
    weights, directions = module.member_weights()
    assert len(weights) == 5
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    assert set(directions) == set(weights)
    assert all(direction == "high" for direction in directions.values())


def test_r495_outputs_are_admitted_only() -> None:
    output = E / "r495_admitted_five_factor_tail_confirmation_2026-11-22.json"
    result = E / "r495_admitted_five_factor_tail_confirmation_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload["weights"]) == {
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_idio_vol_40d_low",
        "ext_alt_hs300_vol_of_vol_60d_low",
    }
    assert payload["decision"]["active_version"] is None
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
