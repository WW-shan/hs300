from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r497_admitted_platform_heldout_bootstrap_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r497_admitted_platform_heldout_bootstrap.py"


def _module():
    spec = importlib.util.spec_from_file_location("r497_bootstrap", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r497_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R497"
    assert payload["source_selected_label"] == "p20_h50_e60_x50_c2"
    assert payload["seeds"]["absolute"] != payload["seeds"]["paired_excess"]
    assert payload["output_policy"]["active_version"] is None
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r497_source_sample_is_unchanged() -> None:
    source = json.loads((E / "r496_admitted_five_factor_platform_2026-11-22.json").read_text())
    assert source["selected"]["label"] == "p20_h50_e60_x50_c2"
    assert len(source["selected"]["weights"]) == 5
    assert abs(sum(source["selected"]["weights"].values()) - 1.0) < 1e-12


def test_r497_outputs_are_frozen_and_rejected() -> None:
    output = E / "r497_admitted_platform_heldout_bootstrap_2026-11-22.json"
    result = E / "r497_admitted_platform_heldout_bootstrap_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selected_label"] == "p20_h50_e60_x50_c2"
    assert payload["gate"]["checks"]["sample_unchanged"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "rejected"
