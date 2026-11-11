from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"


def _load_scan_module() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "research_builtin_parameter_families.py"
    spec = importlib.util.spec_from_file_location("research_builtin_parameter_families_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load_scan_module()


def test_load_p2_families_returns_all_registered_builtin_families() -> None:
    families = scan.load_p2_families(ROOT / "data")

    assert len(families) == 31
    assert {family.family_id for family in families} >= {
        "short_momentum_reversal",
        "medium_momentum",
        "bollinger_position",
        "amihud",
        "limit_up_gene",
        "intraday_return",
    }


def test_variant_id_is_family_and_parameter_only() -> None:
    assert scan.variant_id("medium_momentum", 60) == "medium_momentum__p_60"


def test_comparison_basis_prefers_recommended_then_current() -> None:
    assert scan.comparison_basis({"recommended_parameter": 20, "current_parameter": 60}) == (
        20,
        "recommended",
    )
    assert scan.comparison_basis({"recommended_parameter": None, "current_parameter": 60}) == (
        60,
        "current_fallback",
    )


def test_correlation_summary_detects_identity_and_opposite_series() -> None:
    rows = []
    for day in range(1, 4):
        for symbol, left in enumerate((1.0, 2.0, 4.0, 8.0), start=1):
            rows.append({
                "symbol": f"S{symbol}",
                "date": date(2024, 1, day),
                "left": left + day,
                "right": left + day,
                "opposite": -(left + day),
            })
    frame = pl.DataFrame(rows)

    same = scan.correlation_summary(frame, "left", "right", minimum_symbols=4)
    opposite = scan.correlation_summary(frame, "left", "opposite", minimum_symbols=4)

    assert same["mean"] == pytest.approx(1.0)
    assert same["mean_abs"] == pytest.approx(1.0)
    assert same["max_abs"] == pytest.approx(1.0)
    assert opposite["mean"] == pytest.approx(-1.0)
    assert opposite["max_abs"] == pytest.approx(1.0)
    assert same["n_dates"] == 3


def test_correlation_summary_fails_closed_without_common_dates() -> None:
    frame = pl.DataFrame({
        "symbol": ["S1", "S2", "S3", "S4"],
        "date": [date(2024, 1, 1)] * 4,
        "left": [1.0, 2.0, None, None],
        "right": [1.0, None, 3.0, 4.0],
    })

    with pytest.raises(ValueError, match="no common"):
        scan.correlation_summary(frame, "left", "right", minimum_symbols=4)


def test_p2_research_fingerprint_is_isolated_from_other_family_batches() -> None:
    families = scan.load_p2_families(ROOT / "data")
    payload = scan.p2_research_registry_payload(families)

    assert payload["schema_version"] == 1
    assert len(payload["families"]) == 31
    assert "idio_vol" not in {family["family_id"] for family in payload["families"]}
    assert all("formula_template" in family for family in payload["families"])
    assert "fixed_factor_ids" not in payload
