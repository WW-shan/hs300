from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r498_drawdown_conditional_attribution_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r498_drawdown_conditional_attribution.py"


def _module():
    spec = importlib.util.spec_from_file_location("r498_attribution", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r498_preregistration_freezes_r463_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R498"
    assert payload["portfolio"]["max_positions"] == 10
    assert payload["portfolio"]["max_hold_days"] == 25
    assert payload["portfolio"]["max_exposure_pct"] == 1.0
    assert payload["weak_state"]["threshold_source"] == "train_and_validation_only"
    assert payload["output_policy"]["active_version"] is None
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r498_weak_state_uses_only_train_and_validation() -> None:
    module = _module()
    rows = [
        {"date": "2022-05-05", "score": 10.0, "above_ma20_pct": 0.2},
        {"date": "2022-05-06", "score": 20.0, "above_ma20_pct": 0.3},
        {"date": "2022-05-09", "score": 30.0, "above_ma20_pct": 0.6},
        {"date": "2022-05-10", "score": 40.0, "above_ma20_pct": 0.7},
    ]
    threshold = module.fit_weak_threshold(rows, start="2022-05-05", end="2022-05-09")
    assert 10.0 <= threshold["score_threshold"] <= 20.0
    labelled = module.apply_weak_state(rows, threshold)
    assert labelled[0]["weak"] is False
    assert labelled[1]["weak"] is True
    assert labelled[2]["weak"] is True
    assert labelled[3]["weak"] is False


def test_r498_leg_contribution_and_outputs_are_frozen() -> None:
    module = _module()
    panel = __import__("polars").DataFrame(
        {
            "date": ["2022-05-05", "2022-05-05", "2022-05-06", "2022-05-06"],
            "symbol": ["A", "B", "A", "B"],
            "factor": [1.0, 2.0, 2.0, 1.0],
            "forward_return_5d": [0.02, -0.01, 0.03, 0.01],
        }
    )
    result = module.leg_contribution(panel, "factor", "forward_return_5d", minimum=2)
    assert result["n_dates"] == 2
    assert result["mean"] is not None
    output = E / "r498_drawdown_conditional_attribution_2026-11-22.json"
    result_md = E / "r498_drawdown_conditional_attribution_2026-11-22.md"
    if output.exists() or result_md.exists():
        for path in (output, result_md):
            assert (
                _sha(path)
                == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
            )
    if output.exists():
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["portfolio"]["max_positions"] == 10
        assert payload["portfolio"]["max_hold_days"] == 25
        assert payload["decision"]["active_version"] is None
        assert payload["test_used_for_selection"] is False
        assert payload["recent_used_for_selection"] is False
