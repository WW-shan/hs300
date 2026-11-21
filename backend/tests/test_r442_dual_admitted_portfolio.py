from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r442_dual_admitted_portfolio_preregistration.json"


def test_r442_freezes_four_dual_admitted_factors() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R442"
    assert len(payload["members"]) == 4
    assert payload["members"] == sorted(payload["members"])
    assert payload["execution"]["no_leverage"] is True


def test_r442_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r442_records_best_point_but_rejects_tail() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r442_dual_admitted_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    best = next(row for row in payload["grid"] if row["max_positions"] == 10 and row["max_hold_days"] == 40)
    assert best["test_2x"]["stats"]["annual_return"] > 0.10
    assert best["test_3x"]["stats"]["annual_return"] > 0
    for name in ["r442_dual_admitted_portfolio_preregistration.json", "r442_dual_admitted_portfolio_2026-11-22.json", "r442_dual_admitted_portfolio_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
