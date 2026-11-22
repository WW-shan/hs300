from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r500_r499_recent_tail_attribution_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r500_r499_recent_tail_attribution.py"


def _module():
    spec = importlib.util.spec_from_file_location("r500_tail", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys_modules = __import__("sys").modules
    sys_modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r500_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R500"
    assert payload["source_result"] == "r499_weak_state_value_quality_tilt_2026-11-22.json"
    assert payload["windows"]["recent_start"] == "2025-10-14"
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r500_monthly_and_paired_helpers() -> None:
    module = _module()
    curve = [
        {"date": "2025-10-14", "value": 100.0},
        {"date": "2025-10-31", "value": 90.0},
        {"date": "2025-11-03", "value": 99.0},
    ]
    monthly = module.monthly_returns(curve)
    assert len(monthly) == 2
    assert abs(monthly["2025-10"]["return"] - (-0.10)) < 1e-12
    paired = module.paired_values(
        [("2025-10-14", 0.01), ("2025-10-15", -0.02)],
        {"2025-10-14": 0.005},
    )
    assert paired == [("2025-10-14", 0.005)]


def test_r500_outputs_are_diagnostic_only() -> None:
    output = E / "r500_r499_recent_tail_attribution_2026-11-22.json"
    result = E / "r500_r499_recent_tail_attribution_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_round"] == "R499"
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["diagnostics"]["five_x_warning_only"] is True
