from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r496_admitted_five_factor_platform_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r496_admitted_five_factor_platform.py"


def _module():
    spec = importlib.util.spec_from_file_location("r496_platform", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r496_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R496"
    assert len(payload["members"]) == 5
    assert "ext_alt_hs300_vol_of_vol_20d_low" not in payload["members"]
    assert payload["fixed"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r496_samples_are_complete_and_normalized() -> None:
    module = _module()
    rows = module.generate_samples()
    assert len(rows) == 216
    members = set(json.loads(PREREG.read_text(encoding="utf-8"))["members"])
    for row in rows:
        assert set(row["weights"]) == members
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["max_exposure_pct"] <= 1.0


def test_r496_outputs_are_platform_confirmation_only() -> None:
    output = E / "r496_admitted_five_factor_platform_2026-11-22.json"
    result = E / "r496_admitted_five_factor_platform_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 216
    assert payload["eligible_count"] == 216
    assert payload["stable_count"] == 16
    assert payload["decision"]["active_version"] is None
    assert payload["selected"]["neighborhood"]["pass_rate"] >= 0.6
