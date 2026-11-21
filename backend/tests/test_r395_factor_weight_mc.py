from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r395_factor_weight_mc.py"
PREREG = ROOT / "data/factor_research/research/r395_factor_weight_mc_preregistration.json"


def _module():
    spec = importlib.util.spec_from_file_location("r395_weight_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_freezes_factors_and_windows() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["source_set"]["set_id"] == "multi_dimension_v1"
    assert len(payload["factor_ids"]) == 8
    assert payload["windows"]["train_end"] < payload["windows"]["test_start"]
    assert payload["cost_protocol"]["five_x_hard_rejection"] is False
    assert payload["output_policy"]["active_version"] is None


def test_samples_are_deterministic_and_no_leverage() -> None:
    module = _module()
    first = module.generate_weight_samples()
    second = module.generate_weight_samples()
    assert first == second
    assert len(first) == 129
    for row in first:
        weights = row["weights"]
        assert set(weights) == set(module.FACTOR_IDS)
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert all(0.01 - 1e-12 <= value <= 0.30 + 1e-12 for value in weights.values())


def test_equal_weight_center_is_included() -> None:
    module = _module()
    center = module.generate_weight_samples()[0]
    assert center["label"] == "equal_weight"
    assert all(abs(value - 0.125) < 1e-12 for value in center["weights"].values())


def test_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r395.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")
