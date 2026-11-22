from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r522_r506_fresh_start_year_audit_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r522_r506_fresh_start_year_audit.py"


def _module():
    assert RUNNER.exists(), f"missing R522 runner: {RUNNER}"
    spec = importlib.util.spec_from_file_location("r522_audit", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r522_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R522"
    assert len(payload["starts"]) == 5
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["output_policy"]["active_version"] is None
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r522_window_end_is_limited_to_available_end() -> None:
    module = _module()
    end = date(2026, 11, 5)
    assert module.audit_window(date(2025, 10, 14), end) == (date(2025, 10, 14), end)
    assert module.audit_window(date(2026, 2, 16), end) == (date(2026, 2, 16), end)


def test_r522_short_window_is_labeled_partial_not_full_year() -> None:
    module = _module()
    assert module.window_label(date(2025, 10, 14), date(2026, 11, 5)) == "full_year"
    assert module.window_label(date(2026, 2, 16), date(2026, 11, 5)) == "partial"


def test_r522_frozen_execution_matches_r506() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    execution = prereg["fixed_execution"]
    assert execution["max_positions"] == 10
    assert execution["max_hold_days"] == 25
    assert execution["entry_score"] == 70.0
    assert execution["exit_score"] == 40.0
    assert execution["top_rank"] == 20
    assert execution["matching"] == "open_t+1"


def test_r522_bootstrap_does_not_reuse_curve_levels() -> None:
    module = _module()
    curve = [
        {"date": "2024-01-02", "value": 1.0},
        {"date": "2024-01-03", "value": 1.01},
        {"date": "2024-01-04", "value": 1.00},
    ]
    result = module.block_bootstrap(curve, block=1, paths=100, seed=522)
    assert result["n"] == 2
    assert result["total_p05"] <= result["total_p50"]


def test_r522_outputs_when_present_are_research_only() -> None:
    output = E / "r522_r506_fresh_start_year_audit_2026-11-23.json"
    result = E / "r522_r506_fresh_start_year_audit_2026-11-23.md"
    if not output.exists() or not result.exists():
        return
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["production_enabled"] is False
