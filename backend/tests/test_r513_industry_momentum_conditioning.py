from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r513_industry_momentum_conditioning_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r513_industry_momentum_conditioning.py"


def _module():
    spec = importlib.util.spec_from_file_location("r513_industry", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r513_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R513"
    assert payload["conditioning"]["lag_days"] == 1
    assert payload["conditioning"]["minimum_history_days"] == 120
    assert payload["conditioners"] == [
        "industry_mom_20d",
        "industry_rps_20d",
        "industry_breadth_20d",
    ]
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r513_state_labels_use_lag_and_expanding_terciles() -> None:
    module = _module()
    rows = [
        {"date": "2022-01-03", "conditioner": 1.0},
        {"date": "2022-01-04", "conditioner": 2.0},
        {"date": "2022-01-05", "conditioner": 3.0},
        {"date": "2022-01-06", "conditioner": 4.0},
    ]
    labelled = module.label_states(rows, min_history=2)
    assert labelled[0]["state"] is None
    assert labelled[1]["state"] is None
    assert labelled[2]["state"] in {"low", "mid", "high"}
    assert labelled[3]["state"] in {"low", "mid", "high"}


def test_r513_outputs_are_diagnostic_only() -> None:
    output = E / "r513_industry_momentum_conditioning_2026-11-22.json"
    result = E / "r513_industry_momentum_conditioning_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
