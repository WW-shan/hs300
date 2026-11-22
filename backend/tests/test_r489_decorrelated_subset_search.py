from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r489_decorrelated_subset_search_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r489_decorrelated_subset_search.py"


def _module():
    spec = importlib.util.spec_from_file_location("r489_subset", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r489_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R489"
    assert payload["subset_sizes"] == [6, 7, 8]
    assert payload["selection"]["max_finalists"] == 64
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r489_subsets_are_unique_and_normalized() -> None:
    module = _module()
    rows = module.generate_samples()
    members = json.loads(PREREG.read_text(encoding="utf-8"))["universe"]["members"]
    seen = set()
    for row in rows:
        key = tuple(sorted(row["weights"]))
        assert key not in seen
        seen.add(key)
        assert len(key) in {6, 7, 8}
        assert set(key).issubset(members)
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["max_new_positions_per_day"] == 1
        assert row["max_exposure_pct"] <= 1.0
