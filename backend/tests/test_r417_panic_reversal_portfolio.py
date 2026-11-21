from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r417_panic_reversal_portfolio_preregistration.json"


def test_r417_freezes_one_replacement_variants_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R417"
    assert payload["source_factor"] == "new_panic_reversal_5d"
    assert len(payload["baseline_members"]) == 8
    assert len(payload["variant_replacements"]) == 3
    assert payload["execution"]["max_exposure_pct"] == 1.0
    assert payload["execution"]["no_leverage"] is True


def test_r417_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r417_rejects_if_no_replacement_beats_baseline_train_and_validation() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r417_panic_reversal_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert len(payload["variants"]["baseline"]["members"]) == 8
    assert payload["selected_variant"] in {"baseline", *payload["variants"]}
    if payload["selected_variant"] == "baseline":
        assert payload["decision"]["status"] == "rejected"
    for name in ["r417_panic_reversal_portfolio_preregistration.json", "r417_panic_reversal_portfolio_2026-11-22.json", "r417_panic_reversal_portfolio_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
