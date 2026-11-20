from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r379_hold30_monte_carlo.py"
REPORT = EVIDENCE / "r379_hold30_monte_carlo.json"


def _module():
    spec = importlib.util.spec_from_file_location("r379_hold30_monte_carlo", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_latin_hypercube_is_stratified_and_deterministic() -> None:
    module = _module()
    first = module.latin_hypercube(24, 5, 379)
    second = module.latin_hypercube(24, 5, 379)
    assert first == second
    assert len(first) == 24
    for dimension in range(5):
        column = sorted(row[dimension] for row in first)
        # One sample per equal-width stratum.
        assert [int(value * 24) for value in column] == list(range(24))


def test_r379_pairs_every_sample_with_both_cells_and_stays_unlevered() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["round"] == "R379"
    assert payload["samples"] == 96
    assert len(payload["rows"]) == 96
    for row in payload["rows"]:
        assert set(row["parameters"]) == {
            "c_satellite_cap",
            "equity_target_vol",
            "overlay_lookback_days",
            "overlay_target_vol",
            "overlay_equity_weight_max",
        }
        assert "centre" in row
        assert "control" in row
        assert row["sealed_advantage"] == (
            row["centre"]["sealed"]["total_return"] - row["control"]["sealed"]["total_return"]
        )
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False


def test_r379_evidence_is_hash_bound_and_thresholds_are_reported_together() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for path in (
        EVIDENCE / "r379_preregistration.json",
        REPORT,
        EVIDENCE / "r379_result.md",
    ):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    summary = payload["summary"]
    # Both the strict pre-registered gate and the post-hoc v2 reading are kept,
    # so a reader can see the 11 failures were only the 2022 sign test.
    assert summary["full_pass_count"] == 85
    assert summary["posthoc_gate_policy_v2_full_pass_count"] == 96
    assert summary["posthoc_failed_only_on_strict_2022"] == 11
    assert summary["posthoc_worst_2022_among_failures"] >= -0.05
    assert summary["sealed_advantage_count"] == 96
    assert payload["plateau"]["parameter_robust"] is True
