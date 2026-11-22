from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r504_r499_recent_relative_failure_attribution_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r504_r499_recent_relative_failure_attribution.py"


def _module():
    spec = importlib.util.spec_from_file_location("r504_attr", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r504_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R504"
    assert payload["windows"]["recent_start"] == "2025-10-14"
    assert payload["diagnostics"]["top_pnl_removal"] == [1, 5, 10, 20]
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r504_concentration_is_descriptive() -> None:
    module = _module()
    trades = [
        {"symbol": "A", "pnl_amount": 10.0, "direction": "long", "industry": "X"},
        {"symbol": "A", "pnl_amount": -5.0, "direction": "long", "industry": "X"},
        {"symbol": "B", "pnl_amount": 2.0, "direction": "long", "industry": "Y"},
    ]
    result = module.concentration_rows(trades, [1, 2])
    assert result["total_pnl"] == 7.0
    assert result["positive_trades"] == 2
    assert result["removed_1"] == -3.0


def test_r504_outputs_are_diagnostic_only() -> None:
    output = E / "r504_r499_recent_relative_failure_attribution_2026-11-22.json"
    result = E / "r504_r499_recent_relative_failure_attribution_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
