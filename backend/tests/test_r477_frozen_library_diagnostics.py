from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r477_frozen_library_diagnostics_preregistration.json"
R476 = E / "r476_factor_set_expansion_mc_2026-11-22.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r477_preregistration_is_append_only_confirmation() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R477"
    assert payload["basis"]["new_parameter_search"] is False
    assert payload["basis"]["test_used_for_selection"] is False
    assert payload["basis"]["recent_used_for_selection"] is False
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r477_freezes_the_r476_selected_sample() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r476 = json.loads(R476.read_text(encoding="utf-8"))
    selected = r476["selected"]
    assert selected["label"] == prereg["source_selected_label"]
    assert len(selected["weights"]) == 9
    assert abs(sum(selected["weights"].values()) - 1.0) < 1e-12
    assert selected["added_factor"] in selected["weights"]
    assert selected["max_exposure_pct"] <= 1.0


def test_r477_outputs_are_diagnostic_only() -> None:
    output = E / "r477_frozen_library_diagnostics_2026-11-22.json"
    result = E / "r477_frozen_library_diagnostics_2026-11-22.md"
    for path in (output, result):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["new_parameter_search"] is False
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["gate"]["status"] == "diagnostic_only"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False


def test_r477_diagnostics_expose_trade_concentration() -> None:
    payload = json.loads((E / "r477_frozen_library_diagnostics_2026-11-22.json").read_text(encoding="utf-8"))
    concentration = payload["diagnostics"]["test"]["trade_concentration"]
    assert concentration["trade_count"] > 0
    assert concentration["top_10_pnl"] >= concentration["top_5_pnl"]
    assert concentration["remaining_after_top_10"] < concentration["total_pnl"]
