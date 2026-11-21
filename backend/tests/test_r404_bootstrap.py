from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r404_bootstrap_preregistration.json"
RUNNER = ROOT / "scripts/research_r404_bootstrap.py"


def load_module():
    spec = importlib.util.spec_from_file_location("r404_bootstrap", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_is_frozen_and_no_promotion():
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R404"
    assert payload["bootstrap"]["replications"] == 5000
    assert payload["weight_grid"]["core"] == [0.7, 0.8, 0.9]
    assert payload["primary"] == {"core_weight": 0.8, "beta_weight": 0.2}
    assert payload["output_policy"]["active_version"] is None
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar


def test_paired_bootstrap_is_deterministic_and_bounded():
    module = load_module()
    returns = [0.001, -0.002, 0.003, 0.001, -0.001] * 40
    first = module.paired_block_bootstrap(returns, block_days=20, horizon=len(returns), replications=50, seed=7)
    second = module.paired_block_bootstrap(returns, block_days=20, horizon=len(returns), replications=50, seed=7)
    assert first == second
    assert first["total_p05"] <= first["total_p50"] <= first["total_p95"]
    assert first["replications"] == 50
    assert 0.0 <= first["probability_positive"] <= 1.0


def test_committed_r404_records_negative_p05_and_no_promotion():
    path = E / "r404_bootstrap_2026-11-22.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "bootstrap_mc_complete_no_promotion"
    assert payload["decision"]["active_version"] is None
    assert all(row["windows"]["test"]["strategy"]["total_p05"] < 0 for row in payload["rows"])
    assert all(row["pass_2x"] is False for row in payload["rows"])
    sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
