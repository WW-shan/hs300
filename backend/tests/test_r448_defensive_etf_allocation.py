from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r448_defensive_etf_allocation_preregistration.json"
OUTPUT = E / "r448_defensive_etf_allocation_2026-11-22.json"


def _module():
    path = ROOT / "scripts" / "research_r448_defensive_etf_allocation.py"
    spec = importlib.util.spec_from_file_location("r448_allocation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r448_preregistration_binds_diagnostic_five_x() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["max_weight_sum"] == 1.0
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["supersedes_rounds"] == ["R446", "R447"]


def test_r448_sidecars_and_rejection() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selected"]["name"] == "risk_parity_vol60"
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["pass"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == path.with_name(path.name + ".sha256").read_text().split()[0]


def test_r448_equal_weight_increment_is_directional_not_significant() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    increments = payload["additional_evidence"]["equal_weight_increment"]
    assert increments["train"]["annualized"] < 0
    assert increments["test"]["annualized"] < 0
    assert all(row["p05"] < 0 for row in increments.values())


def test_r448_no_lookahead_synthetic_flow() -> None:
    module = _module()
    import polars as pl
    rows = []
    prices = {"A": [10.0, 11.0, 11.0, 10.0, 10.0, 10.0], "B": [10.0] * 6, "C": [10.0, 9.0, 9.0, 9.0, 15.0, 18.0]}
    days = [date(2023, 12, 1), date(2023, 12, 29), date(2024, 1, 2), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)]
    for symbol, values in prices.items():
        for day, value in zip(days, values, strict=True):
            rows.append({"symbol": symbol, "date": day, "open": value, "close": value})
    # A fixed A/B book makes the timing explicit: half of A falls 11 -> 10
    # before the 2024-02-01 open, then the same book earns 2/1 -> 2/2. C's
    # 9 -> 15 move is not held, so it cannot enter the result.
    rule = {"name": "fixture", "kind": "fixed", "weights": {"A": 0.5, "B": 0.5, "C": 0.0}}
    curve = module.simulate(pl.DataFrame(rows), rule, list(prices), date(2023, 12, 1), date(2024, 2, 2), 0.0)
    assert curve[-1]["value"] == pytest.approx(1.0 - 0.5 * (11.0 - 10.0) / 11.0)


def test_r448_preflight_fails_closed_when_etf_cache_is_missing(monkeypatch, tmp_path) -> None:
    module = _module()
    monkeypatch.setattr(module, "DATA", tmp_path)
    with pytest.raises(FileNotFoundError, match="kline_etf_enriched"):
        module.preflight_etf_history(["510300.SH"], date(2024, 1, 1), date(2024, 1, 31))
