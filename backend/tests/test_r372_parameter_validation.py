from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r372_parameter_validation.py"
PREREG = EVIDENCE / "r372_preregistration.json"
REPORT = EVIDENCE / "r372_parameter_validation.json"


def _module():
    spec = importlib.util.spec_from_file_location("r372_parameter_validation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_replacement_preserves_total_weight_and_slot_count() -> None:
    module = _module()
    scoring = module._replace_members(
        {
            "old": 0.125,
            "other": 0.875,
        },
        {"old": "new"},
    )

    assert scoring == {"new": 0.125, "other": 0.875}
    assert sum(scoring.values()) == 1.0


def test_r372_evidence_is_hash_bound_and_keeps_mc_rejection() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    sidecar = REPORT.with_name(REPORT.name + ".sha256")
    expected = sidecar.read_text(encoding="ascii").split()[0]

    assert _sha256(REPORT) == expected
    assert payload["preregistration_sha256"] == _sha256(PREREG)
    assert payload["decision"]["core_platform_pass"] is True
    assert payload["decision"]["mc_replacement_accepted"] is False
    assert payload["decision"]["selected"] is None
    assert payload["decision"]["no_leverage"] is True
    assert payload["core_platform"]["cell_count"] == 27
    assert payload["core_platform"]["pass_count"] == 27
    assert payload["evaluation"]["mc_recommended_2x"]["checks"]["sharpe"] is False


def test_r372_preregistration_has_both_real_backtest_lines_and_no_leverage() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))

    assert {line["id"] for line in prereg["portfolio_line"]["lines"]} == {
        "base_current",
        "mc_recommended",
    }
    assert prereg["data_contract"]["leverage"] == "none"
    assert prereg["data_contract"]["matching"] == "open_t+1"
    assert prereg["evaluation"]["portfolio_2x"]["sharpe_min"] == 1.0
    assert prereg["evaluation"]["portfolio_2x"]["mdd_min"] == -0.20
