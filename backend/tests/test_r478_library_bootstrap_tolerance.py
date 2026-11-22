from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r478_library_bootstrap_tolerance_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r478_library_bootstrap_tolerance.py"


def _module():
    spec = importlib.util.spec_from_file_location("r478_tolerance", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r478_preregistration_does_not_relax_the_gate() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R478"
    assert payload["basis"]["near_zero_counts_as_positive"] is False
    assert payload["basis"]["hard_gate_changed"] is False
    assert payload["basis"]["test_used_for_selection"] is False
    assert payload["basis"]["recent_used_for_selection"] is False
    assert payload["tolerance"]["bootstrap_near_zero_tolerance"] == 0.01
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r478_classifies_tail_widths() -> None:
    module = _module()
    assert module.classify_p05(0.001) == "positive"
    assert module.classify_p05(0.0) == "positive"
    assert module.classify_p05(-0.009) == "near_zero"
    assert module.classify_p05(-0.01) == "near_zero"
    assert module.classify_p05(-0.0100001) == "negative"
