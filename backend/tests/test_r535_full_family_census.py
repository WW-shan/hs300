from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r535_full_family_census.py"


def _module():
    spec = importlib.util.spec_from_file_location("r535_full_family_census_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_statistical_contract() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "statistical_screen"' in text
    assert "r535a_full_family_census_preregistration.json" in text


def test_registry_enumerates_all_85_families_and_472_parameter_points() -> None:
    module = _module()
    families = module.all_family_trials(ROOT)
    assert len(families) == 85
    assert sum(len(row["parameters"]) for row in families) == 472
    assert {row["tier"] for row in families} == {"P1", "P2", "P3", "P4"}


def test_training_neighbours_include_each_side_for_interior_and_two_for_endpoint() -> None:
    module = _module()
    assert module.training_neighbours([5, 10, 20, 40, 60], 20) == [10, 40]
    assert module.training_neighbours([5, 10, 20, 40, 60], 5) == [10, 20]
    assert module.training_neighbours([5, 10, 20, 40, 60], 60) == [20, 40]


def test_family_selection_uses_training_icir_and_rejects_non_plateau() -> None:
    module = _module()
    rows = [
        {
            "parameter": 5,
            "train_ic": 0.02,
            "train_icir": 0.20,
            "coverage": 1.0,
            "train_p05": 0.02,
            "plateau_ok": True,
        },
        {
            "parameter": 10,
            "train_ic": 0.03,
            "train_icir": 0.30,
            "coverage": 1.0,
            "train_p05": 0.01,
            "plateau_ok": True,
        },
        {
            "parameter": 20,
            "train_ic": 0.04,
            "train_icir": 0.50,
            "coverage": 1.0,
            "train_p05": 0.01,
            "plateau_ok": False,
        },
    ]
    selected = module.select_family_parameter(rows, current_parameter=20)
    assert selected is not None
    assert selected["parameter"] == 10


def test_family_selection_returns_none_without_training_candidate() -> None:
    module = _module()
    rows = [
        {
            "parameter": 5,
            "train_ic": -0.01,
            "train_icir": -0.10,
            "coverage": 1.0,
            "train_p05": -0.02,
            "plateau_ok": False,
        }
    ]
    assert module.select_family_parameter(rows, current_parameter=5) is None


def test_final_gate_uses_block20_ci_but_only_block10_40_sign_sensitivity() -> None:
    module = _module()
    row = {
        "coverage": 1.0,
        "train_ic": 0.03,
        "full_ic": 0.02,
        "validation_ic": 0.01,
        "recent_ic": 0.005,
        "plateau_ok": True,
        "final_bootstrap": {
            "10": {"mean": 0.03, "ci_low": -0.001},
            "20": {"mean": 0.03, "ci_low": 0.002},
            "40": {"mean": 0.03, "ci_low": -0.001},
        },
    }
    gates = module.final_gate_results(row, q_value=0.05)
    assert gates["train_block20_ci_positive"] is True
    assert gates["block10_40_mean_positive"] is True
    assert all(gates.values())


def test_final_gate_rejects_negative_block10_mean() -> None:
    module = _module()
    row = {
        "coverage": 1.0,
        "train_ic": 0.03,
        "full_ic": 0.02,
        "validation_ic": 0.01,
        "recent_ic": 0.005,
        "plateau_ok": True,
        "final_bootstrap": {
            "10": {"mean": -0.001, "ci_low": -0.02},
            "20": {"mean": 0.03, "ci_low": 0.002},
            "40": {"mean": 0.03, "ci_low": -0.001},
        },
    }
    gates = module.final_gate_results(row, q_value=0.05)
    assert gates["block10_40_mean_positive"] is False


def test_run_study_finalizes_fdr_and_counts_with_injected_family_results(monkeypatch) -> None:
    module = _module()
    families = [
        {"family_id": "a", "tier": "P2", "parameters": []},
        {"family_id": "b", "tier": "P3", "parameters": []},
    ]
    monkeypatch.setattr(module, "all_family_trials", lambda root: families)
    monkeypatch.setattr(module, "_load_script_module", lambda name: type("R530", (), {
        "load_pit_membership": staticmethod(lambda root: "membership"),
        "_load_context": staticmethod(lambda root, membership: "context"),
    })())

    def fake_scan(family_row, context, membership):
        return {
            "family_id": family_row["family_id"],
            "tier": family_row["tier"],
            "family": {"data_class": "price"},
            "status": "finalist_pending_fdr",
            "errors": [],
            "trials": [],
            "selected": {
                "parameter": 5,
                "coverage": 1.0,
                "train_ic": 0.03,
                "train_p05": 0.01,
                "full_ic": 0.02,
                "validation_ic": 0.01,
                "recent_ic": 0.005,
                "plateau_ok": True,
                "validation": {"p_value": 0.01},
                "final_bootstrap": {
                    "10": {"mean": 0.03, "ci_low": 0.001},
                    "20": {"mean": 0.03, "ci_low": 0.002},
                    "40": {"mean": 0.03, "ci_low": 0.001},
                },
            },
        }

    monkeypatch.setattr(module, "scan_family", fake_scan)
    payload, _ = module.run_study(ROOT)
    assert payload["library_census"]["family_count"] == 2
    assert payload["conclusion_counts"] == {"admitted_for_research": 2}


def test_committed_r535a_result_is_contract_bound_and_keeps_negative_knowledge() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r535a_full_family_census_preregistration.json"
    result = research / "r535a_full_family_census_result.json"
    markdown = research / "r535a_full_family_census_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "statistical_screen"
    assert payload["conclusion"] == "rejected"
    assert payload["preregistration_sha256"] == hashlib.sha256(
        prereg.read_bytes()
    ).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert payload["metrics"]["library_census"]["family_count"] == 85
    assert payload["metrics"]["library_census"]["parameter_point_count"] == 472
    assert payload["metrics"]["conclusion_counts"] == {
        "data_gap": 8,
        "rejected": 77,
    }
    finalists = [
        row for row in payload["metrics"]["families"] if row.get("selected") is not None
    ]
    assert len(finalists) == 22
    assert payload["metrics"]["admitted_families"] == []
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
