from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r432_parameter_family_interaction_preregistration.json"


def test_r432_freezes_three_parameter_variants() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R432"
    assert len(payload["variants"]) == 3
    assert payload["execution"]["no_leverage"] is True
    assert payload["variants"][2]["replace"]["ext_alt_hs300_idio_vol_40d_low"] == "ext_alt_hs300_idio_vol_20d_low"


def test_r432_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r432_rejects_mc_recommended_parameter_swaps() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r432_parameter_family_interaction_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_variant"] == "baseline"
    assert payload["decision"]["status"] == "rejected"
    for name in ["r432_parameter_family_interaction_preregistration.json", "r432_parameter_family_interaction_2026-11-22.json", "r432_parameter_family_interaction_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
