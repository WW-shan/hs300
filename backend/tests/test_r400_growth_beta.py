from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r400_growth_beta_preregistration.json"
RUNNER = ROOT / "scripts/research_r400_growth_beta.py"


def module():
    spec = importlib.util.spec_from_file_location("r400_beta_test", RUNNER)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_preregistration_freezes_cores_and_benchmark() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R400"
    assert len(payload["core_candidates"]) == 3
    assert payload["weights"]["core_total"] == 0.75
    assert payload["weights"]["beta_total"] == 0.25
    assert payload["benchmark"]["symbol"] == "000300.SH"
    assert payload["output_policy"]["active_version"] is None


def test_no_leverage_and_registered_beta_only() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["weights"]["core_total"] + payload["weights"]["beta_total"] == 1.0
    assert all(item["direction"] in {"high", "low"} for item in payload["beta_factors"])
