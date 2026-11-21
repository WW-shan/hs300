from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r419_admitted_four_factor_portfolio_preregistration.json"


def test_r419_freezes_four_admitted_factors_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R419"
    assert payload["admitted_members"] == [
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_idio_vol_40d_low",
        "ext_alt_hs300_vol_of_vol_60d_low",
    ]
    assert payload["execution"]["max_exposure_pct"] == 1.0
    assert payload["execution"]["no_leverage"] is True


def test_r419_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r419_records_selected_point_and_gate_consistency() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r419_admitted_four_factor_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selected"]["max_positions"] in {10, 20}
    assert payload["selected"]["max_hold_days"] in {30, 40}
    assert len(payload["grid"]) == 4
    assert payload["gate"]["pass"] == all(payload["gate"]["checks"].values())
    for name in ["r419_admitted_four_factor_portfolio_preregistration.json", "r419_admitted_four_factor_portfolio_2026-11-22.json", "r419_admitted_four_factor_portfolio_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
