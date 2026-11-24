from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r536_finalist_cluster_fdr.py"


def _module():
    spec = importlib.util.spec_from_file_location("r536_finalist_cluster_fdr_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_statistical_contract_and_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "statistical_screen"' in text
    assert "r536a_finalist_cluster_fdr_preregistration.json" in text
    assert "r535a_full_family_census_result.json" in text


def test_finalist_source_has_exactly_22_selected_families() -> None:
    module = _module()
    finalists = module.load_finalists(ROOT)
    assert len(finalists) == 22
    assert len({row["family_id"] for row in finalists}) == 22
    assert all(row["selected"]["parameter"] is not None for row in finalists)


def test_cluster_representative_uses_highest_training_icir() -> None:
    module = _module()
    clusters = [["a", "b", "c"], ["d"]]
    rows = {
        "a": {"train_icir": 0.20, "train_ic": 0.04, "train_p05": 0.01, "parameter": 10},
        "b": {"train_icir": 0.40, "train_ic": 0.03, "train_p05": 0.02, "parameter": 20},
        "c": {"train_icir": 0.30, "train_ic": 0.05, "train_p05": 0.03, "parameter": 5},
        "d": {"train_icir": 0.10, "train_ic": 0.02, "train_p05": 0.01, "parameter": 60},
    }
    representatives = module.choose_cluster_representatives(clusters, rows)
    assert [row["family_id"] for row in representatives] == ["b", "d"]
    assert representatives[0]["cluster"] == ["a", "b", "c"]
    assert representatives[0]["cluster_size"] == 3


def test_non_finite_payload_values_become_null() -> None:
    import math

    module = _module()
    value = module.sanitize_finite({"rho": math.nan, "items": [math.inf, 1.0]})
    assert value == {"rho": None, "items": [None, 1.0]}


def test_committed_r536a_result_is_contract_bound_and_has_no_nan() -> None:
    import hashlib
    import json
    import math

    research = ROOT / "data/factor_research/research"
    prereg = research / "r536a_finalist_cluster_fdr_preregistration.json"
    result = research / "r536a_finalist_cluster_fdr_result.json"
    markdown = research / "r536a_finalist_cluster_fdr_result.md"
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
    assert len(payload["metrics"]["clusters"]) == 13
    assert len(payload["metrics"]["representatives"]) == 13
    assert payload["metrics"]["conclusion_counts"] == {"rejected": 13}
    assert payload["metrics"]["admitted_families"] == []

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, float):
            yield value

    assert all(math.isfinite(value) for value in walk(payload))
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
