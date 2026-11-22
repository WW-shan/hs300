from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r526_unified_pit_input_bundle_preregistration.json"
BUNDLE = E / "r526_unified_pit_bundle_window"
MANIFEST = E / "r526_unified_pit_bundle_manifest_2026-11-23.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r526_preregistration_is_frozen_and_research_only() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R526"
    assert payload["gates"]["membership_sessions"] == 1093
    assert payload["output_policy"]["active_version"] is None
    assert payload["output_policy"]["production_enabled"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]


def test_r526_bundle_has_small_frozen_inputs_and_manifest() -> None:
    assert BUNDLE.exists(), BUNDLE
    assert MANIFEST.exists(), MANIFEST
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["round"] == "R526"
    assert len(manifest["files"]) == 4
    assert all((BUNDLE / row["name"]).exists() for row in manifest["files"])
    assert all(_sha(BUNDLE / row["name"]) == row["sha256"] for row in manifest["files"])
    assert sum((BUNDLE / row["name"]).stat().st_size for row in manifest["files"]) < 15 * 1024 * 1024


def test_r526_membership_is_complete_daily_300() -> None:
    membership = pl.read_parquet(BUNDLE / "daily_snapshots.parquet")
    assert membership.height == 424200
    window = membership.filter(
        (pl.col("query_date") >= pl.date(2022, 5, 5))
        & (pl.col("query_date") <= pl.date(2026, 11, 5))
    )
    counts = window.group_by("query_date").agg(pl.len().alias("n"))
    assert counts.height == 1093
    assert counts["n"].min() == 300
    assert counts["n"].max() == 300


def test_r526_industry_and_extension_retain_lineage() -> None:
    industry = pl.read_parquet(BUNDLE / "industry_history.parquet")
    extension = pl.read_parquet(BUNDLE / "alt_hs300_required_fields.parquet")
    assert industry.height == 11981
    assert extension.height == 983637
    assert str(industry["effective_date"].min()) == "1990-01-01"
    assert set(extension.columns) == {
        "symbol", "date", "holder_top10_free_float_share", "equity_risk_premium",
        "credit_spread_5y_low", "term_spread_10y_2y", "industry_mom_20d", "stock_rel_industry_20d",
    }
