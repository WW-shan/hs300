from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r396_weight_stability.py"
PREREG = ROOT / "data/factor_research/research/r396_weight_stability_preregistration.json"
R395 = ROOT / "data/factor_research/research/r395_factor_weight_mc_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r396_stability", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_binds_r395_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["source_r395_sha256"] == hashlib.sha256(R395.read_bytes()).hexdigest()
    assert payload["selected_label"] == "lhs_020"
    assert payload["output_policy"]["active_version"] is None
    assert payload["cost_protocol"]["five_x_hard_rejection"] is False


def test_neighbourhood_is_deterministic_and_bounded() -> None:
    module = _module()
    first = module.neighbourhood()
    second = module.neighbourhood()
    assert first == second
    assert len(first) == 25
    assert all(abs(sum(row["weights"].values()) - 1.0) < 1e-9 for row in first)
    assert all(row["weights"] for row in first)
    assert len([row for row in first if row["kind"] == "leave_one_out"]) == 8


def test_no_leverage() -> None:
    module = _module()
    assert all(module.weights_within_limit(row["weights"]) for row in module.neighbourhood())


def test_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r396.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")
