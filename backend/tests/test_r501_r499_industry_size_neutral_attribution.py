from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r501_r499_industry_size_neutral_attribution_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r501_r499_industry_size_neutral_attribution.py"


def _module():
    spec = importlib.util.spec_from_file_location("r501_neutral", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r501_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R501"
    assert payload["legs"]["value_quality"] == "high"
    assert payload["neutralization"] == {
        "industry": "industry_l1",
        "styles": ["log_float_mv"],
        "shrink": 1.0,
    }
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r501_ic_summary_uses_signed_rank_correlation() -> None:
    module = _module()
    panel = __import__("polars").DataFrame(
        {
            "date": ["2025-01-02"] * 4,
            "symbol": ["A", "B", "C", "D"],
            "factor": [1.0, 2.0, 3.0, 4.0],
            "ret": [0.01, 0.02, 0.03, 0.04],
        }
    )
    result = module.window_ic(panel, "factor", "ret", minimum=4)
    assert result["n_dates"] == 1
    assert result["mean_ic"] is not None
    assert result["mean_ic"] > 0.99


def test_r501_outputs_are_diagnostic_only() -> None:
    output = E / "r501_r499_industry_size_neutral_attribution_2026-11-22.json"
    result = E / "r501_r499_industry_size_neutral_attribution_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["source_rounds"] == ["R499", "R500"]
