from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r503_r499_rolling_year_regime_confirmation_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r503_r499_rolling_year_regime_confirmation.py"


def _module():
    spec = importlib.util.spec_from_file_location("r503_rolling", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r503_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R503"
    assert len(payload["rolling_starts"]) == 15
    assert payload["rolling_starts"][-1] == "2025-10-14"
    assert payload["source_r499"] == "r499_weak_state_value_quality_tilt_2026-11-22.json"
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r503_one_year_end_and_metrics() -> None:
    module = _module()
    assert module.one_year_end("2025-10-14") == "2026-10-13"
    curve = [
        {"date": "2025-10-14", "value": 100.0},
        {"date": "2025-10-15", "value": 101.0},
        {"date": "2026-10-14", "value": 110.0},
    ]
    metrics = module.curve_metrics(curve)
    assert abs(metrics["total_return"] - 0.10) < 1e-12


def test_r503_outputs_are_confirmation_only() -> None:
    output = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.json"
    result = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["diagnostics"]["five_x_warning_only"] is True
