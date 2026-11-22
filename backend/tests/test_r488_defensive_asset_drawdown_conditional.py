from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r488_defensive_asset_drawdown_conditional_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r488_defensive_asset_drawdown_conditional.py"


def _module():
    spec = importlib.util.spec_from_file_location("r488_defensive", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r488_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R488"
    assert payload["source_selected_label"] == "cap_1"
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r488_rules_are_bounded() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    for name, rule in payload["rules"].items():
        total = (
            float(rule.get("equity", 0.0))
            + float(rule.get("bond", 0.0))
            + float(rule.get("gold", 0.0))
            + float(rule.get("cash", 0.0))
        )
        assert abs(total - 1.0) < 1e-12, (name, total)


def test_r488_conditional_switch_uses_previous_day_only() -> None:
    module = _module()
    curve = [
        {"date": "2024-01-02", "value": 1.0},
        {"date": "2024-01-03", "value": 0.94},
        {"date": "2024-01-04", "value": 0.95},
    ]
    regime = {"2024-01-02": {"state": "strong"}, "2024-01-03": {"state": "weak"}}
    weights = module.rule_weights(
        curve,
        regime,
        {"condition": "drawdown_le_-0.05", "equity": 0.7, "bond": 0.3, "fallback_equity": 1.0},
    )
    assert weights[0]["equity"] == 1.0
    assert weights[1]["equity"] == 1.0
    assert weights[2]["equity"] == 0.7


def test_r488_outputs_are_diagnostic_and_no_leverage() -> None:
    output = E / "r488_defensive_asset_drawdown_conditional_2026-11-22.json"
    result = E / "r488_defensive_asset_drawdown_conditional_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["conditional_t1_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["selected"]["test"]["weight_min"] <= 1.0
