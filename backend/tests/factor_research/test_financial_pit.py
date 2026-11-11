from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.factor_research import financial_pit
from app.factor_research.financial_pit import (
    audit_financial_pit,
    promote_financial_taxonomy,
)


def _write_financial_fixture(root: Path) -> None:
    metrics = root / "financials" / "metrics"
    metrics.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.SH"],
        "period_end": [date(2026, 3, 31)],
        "announce_date": [date(2026, 4, 5)],
        "bps": [4.0],
        "roe": [20.0],
        "gross_margin": [30.0],
        "net_margin": [12.0],
        "revenue_yoy": [8.0],
        "net_income_yoy": [6.0],
        "debt_to_asset_ratio": [40.0],
    }).write_parquet(metrics / "part.parquet")

    for day in range(1, 9):
        partition = root / "kline_daily_enriched" / f"date=2026-04-{day:02d}"
        partition.mkdir(parents=True)
        pl.DataFrame({
            "symbol": ["600000.SH"],
            "date": [date(2026, 4, day)],
            "close": [10.0],
        }).write_parquet(partition / "part.parquet")


def test_financial_pit_audit_verifies_announcement_gate(tmp_path: Path) -> None:
    _write_financial_fixture(tmp_path)

    result = audit_financial_pit(
        tmp_path,
        factor_ids=["roe_latest", "pb_latest"],
        symbols=["600000.SH"],
        start=date(2026, 4, 1),
        end=date(2026, 4, 8),
        include_dividend=False,
    )

    assert result["status"] == "verified"
    assert result["verified_factor_ids"] == ["pb_latest", "roe_latest"]
    assert result["factors"]["roe_latest"]["mismatch_count"] == 0
    assert result["factors"]["roe_latest"]["first_value_date"] == "2026-04-06"
    assert result["factors"]["pb_latest"]["mismatch_count"] == 0


def test_financial_pit_audit_detects_future_leak(
    tmp_path: Path, monkeypatch
) -> None:
    _write_financial_fixture(tmp_path)
    original = financial_pit.attach_fundamental_factors

    def leaking_attach(panel: pl.DataFrame, snapshot: pl.DataFrame, names):
        attached = original(panel, snapshot, names)
        return attached.with_columns(
            pl.when(pl.col("date") >= date(2026, 4, 5))
            .then(pl.lit(20.0))
            .otherwise(pl.col("roe_latest"))
            .alias("roe_latest")
        )

    monkeypatch.setattr(financial_pit, "attach_fundamental_factors", leaking_attach)
    result = audit_financial_pit(
        tmp_path,
        factor_ids=["roe_latest"],
        symbols=["600000.SH"],
        start=date(2026, 4, 1),
        end=date(2026, 4, 8),
        include_dividend=False,
    )

    assert result["factors"]["roe_latest"]["mismatch_count"] > 0
    assert result["status"] == "partial"
    assert result["verified_factor_ids"] == []


def test_taxonomy_promotion_requires_audit_membership() -> None:
    records = [
        {"factor_id": "roe_latest", "pit_hint": "declared"},
        {"factor_id": "accrual", "pit_hint": "declared"},
        {"factor_id": "ext_alt_hs300_dividend_yield", "pit_hint": "declared"},
    ]
    audit = {"verified_factor_ids": ["roe_latest", "ext_alt_hs300_dividend_yield"]}
    evidence = {"path": "factor_research/pit/runs/a.json", "sha256": "sha256:" + "a" * 64}

    promoted = promote_financial_taxonomy(records, audit, evidence)

    assert promoted[0]["pit_hint"] == "verified"
    assert promoted[0]["future_data_status"] == "clear"
    assert evidence in promoted[0]["evidence_refs"]
    assert promoted[1]["pit_hint"] == "declared"
    assert "future_data_status" not in promoted[1]
    assert promoted[2]["pit_hint"] == "verified"
    assert evidence in promoted[2]["evidence_refs"]
