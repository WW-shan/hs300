from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import polars as pl
import pytest

from app.factor_research.parameters import ParameterFamily

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
START = date(2021, 6, 1)
END = date(2026, 11, 5)


def _load_scan_module() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "research_extended_parameter_families.py"
    spec = importlib.util.spec_from_file_location(
        "research_extended_parameter_families_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load_scan_module()


def _synthetic_family(
    *,
    family_id: str,
    evaluator: str,
    cross_section_type: str,
    grid: list[int],
    current: int,
    direction: str = "high",
) -> ParameterFamily:
    return ParameterFamily.model_validate(
        {
            "family_id": family_id,
            "label": family_id,
            "data_class": "price",
            "cross_section_type": cross_section_type,
            "direction": direction,
            "evaluator": evaluator,
            "parameter_name": "window",
            "parameter_grid": grid,
            "current_parameter": current,
            "minimum_history": 1,
            "neutralization": (
                ["industry", "size"]
                if cross_section_type == "stock_cross_sectional"
                else []
            ),
            "member_factor_ids": [f"{family_id}_{current}d"],
            "status": "tunable",
        }
    )


def _synthetic_stock_context() -> dict:
    dates = [date(2024, 1, 2) + timedelta(days=index) for index in range(40)]
    rows = []
    for symbol_index in range(60):
        symbol = f"S{symbol_index:03d}"
        industry = f"I{symbol_index % 4}"
        signal = (symbol_index - 30) / 30.0
        for day_index, day in enumerate(dates):
            forward = signal * 0.01 + (day_index % 5 - 2) * 0.0001
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "forward_return_5d": forward if day_index < 35 else None,
                    "industry_l1": industry,
                    "log_float_mv": symbol_index / 10.0,
                    "signal": signal,
                }
            )
    prices = pl.DataFrame(rows)

    def evaluator(context: dict, parameter: int):
        del context
        frame = prices.select(
            [
                "symbol",
                "date",
                (pl.col("signal") * (1.0 + parameter / 1000.0)).alias(
                    "factor_value"
                ),
            ]
        )
        return frame, "factor_value", f"synthetic({parameter})"

    return {
        "prices": prices,
        "calendar": prices.select(["symbol", "date"]),
        "minimum_cross_section": 30,
        "data_fingerprint": "sha256:test",
        "evaluators": {"synthetic_stock": evaluator},
    }


def _synthetic_market_context() -> dict:
    dates = []
    current = date(2023, 1, 2)
    while len(dates) < 760:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    future_signal = [
        ((index * 37) % 101 - 50) / 5000.0 for index in range(len(dates))
    ]
    noise = [((index * 19) % 11 - 5) / 1_000_000.0 for index in range(len(dates))]
    market = pl.DataFrame(
        {
            "date": dates,
            "close": [100.0 + index * 0.01 for index in range(len(dates))],
            "noise": noise,
            "forward_return_5d": [
                future_signal[index] if index < len(dates) - 5 else None
                for index in range(len(dates))
            ],
        }
    )

    def evaluator(context: dict, parameter: int):
        del context
        frame = market.select(
            [
                "date",
                (
                    pl.col("forward_return_5d")
                    * (1.0 + parameter / 1000.0)
                    + pl.col("noise")
                ).alias("factor_value"),
            ]
        )
        return frame, "factor_value", f"synthetic_market({parameter})"

    return {
        "market": market,
        "data_fingerprint": "sha256:test",
        "evaluators": {"synthetic_market": evaluator},
    }


def test_load_p3_families_returns_all_registered_extended_families() -> None:
    families = scan.load_p3_families(ROOT / "data")

    assert len(families) == 23
    assert {family.family_id for family in families} >= {
        "industry_momentum",
        "insider_buy_event",
        "analyst_rating_level",
        "bond_futures_momentum",
        "market_skew",
    }


def test_stock_family_scan_rejects_single_point_and_emits_all_trials() -> None:
    family = _synthetic_family(
        family_id="synthetic_stock",
        evaluator="synthetic_stock",
        cross_section_type="stock_cross_sectional",
        grid=[10, 20],
        current=20,
    )

    result = scan.scan_stock_family(
        family,
        _synthetic_stock_context(),
        "sha256:test",
        START,
        END,
    )

    assert result["recommendation_status"] == "rejected"
    assert [trial["parameter"] for trial in result["trials"]] == [10, 20]
    assert all(
        trial["plateau_status"] == "single_point_unverified"
        for trial in result["trials"]
    )


def test_market_family_is_conditioner_only_and_requires_fdr() -> None:
    family = _synthetic_family(
        family_id="synthetic_market",
        evaluator="synthetic_market",
        cross_section_type="market_level",
        grid=[5, 10, 20],
        current=10,
    )

    result = scan.scan_market_family(
        family,
        _synthetic_market_context(),
        "sha256:test",
        date(2023, 2, 1),
        date(2025, 8, 1),
    )

    assert result["role"] == "conditioner"
    assert result["stock_alpha_eligible"] is False
    assert result["recommendation_status"] == "recommended"
    assert result["recommended_trial"]["q_value"] <= 0.10


def test_primary_block_ci_uses_block_twenty_only() -> None:
    trial = {
        "final_bootstrap": {
            "10": {"ci_low": -0.1},
            "20": {"ci_low": 0.02},
            "40": {"ci_low": -0.2},
        }
    }

    assert scan.primary_block_ci(trial) == 0.02


def test_p3_registry_fingerprint_is_isolated_from_other_batches() -> None:
    families = scan.load_p3_families(ROOT / "data")
    payload = scan.p3_research_registry_payload(families)

    assert payload["schema_version"] == 1
    assert len(payload["families"]) == 23
    assert "idio_vol" not in {family["family_id"] for family in payload["families"]}
    assert "medium_momentum" not in {
        family["family_id"] for family in payload["families"]
    }
    assert all("formula_template" in family for family in payload["families"])


@pytest.mark.skipif(
    not (ROOT / "data" / "ext_data" / "alt_hs300" / "timeseries").exists(),
    reason="local extension factor cache is unavailable",
)
def test_all_p3_registered_variants_match_existing_extension_table() -> None:
    result = scan.run_parity_checks(ROOT / "data")

    assert result["status"] == "pass"
    assert result["variant_count"] == 25
    assert all(variant["status"] == "pass" for variant in result["variants"])
