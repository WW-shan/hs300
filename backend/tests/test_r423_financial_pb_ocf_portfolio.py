from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r423_financial_pb_ocf_portfolio_preregistration.json"


def test_r423_freezes_three_variants_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R423"
    assert len(payload["variants"]) == 3
    assert payload["variants"][0]["directions"]["pb_latest"] == "low"
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r423_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r423_rejects_if_no_variant_beats_baseline_train_and_validation() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r423_financial_pb_ocf_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_variant"] in payload["variants"]
    if payload["selected_variant"] == "baseline":
        assert payload["decision"]["status"] == "rejected"
    for name in ["r423_financial_pb_ocf_portfolio_preregistration.json", "r423_financial_pb_ocf_portfolio_2026-11-22.json", "r423_financial_pb_ocf_portfolio_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
