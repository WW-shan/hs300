from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import polars as pl

from app.factor_research.threshold_parameters import ThresholdFamily

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"


def _load_scan_module() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "research_threshold_parameter_families.py"
    spec = importlib.util.spec_from_file_location(
        "research_threshold_parameter_families_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load_scan_module()


def _threshold_family(
    *,
    family_id: str = "synthetic_threshold_absolute",
    grid: list[float] | None = None,
    current: float = 0.0,
) -> ThresholdFamily:
    return ThresholdFamily.model_validate(
        {
            "family_id": family_id,
            "label": family_id,
            "kind": "threshold",
            "source_factor_id": "factor_value",
            "mode": "absolute",
            "direction": "high",
            "parameter_name": "threshold",
            "parameter_grid": grid or [-0.3, 0.0, 0.3],
            "current_parameter": current,
            "neutralization": ["industry", "size"],
            "data_class": "financial",
            "minimum_history": 1,
            "formula_template": "synthetic >= {parameter}",
        }
    )


def _event_family(
    *,
    grid: list[float] | None = None,
    current: float = 10.0,
) -> ThresholdFamily:
    return ThresholdFamily.model_validate(
        {
            "family_id": "synthetic_event_holding",
            "label": "synthetic event holding",
            "kind": "event_holding",
            "source_factor_id": "ext_alt_hs300_repurchase_event_60d",
            "event_key": "synthetic_event",
            "direction": "high",
            "parameter_name": "holding_days",
            "parameter_grid": grid or [5.0, 10.0, 20.0],
            "current_parameter": current,
            "neutralization": ["industry", "size"],
            "data_class": "flow",
            "minimum_history": 1,
            "formula_template": "synthetic_event({parameter})",
        }
    )


def _synthetic_context(*, include_event: bool = False) -> tuple[dict, pl.DataFrame]:
    start = date(2024, 1, 2)
    dates = [start + timedelta(days=index) for index in range(600)]
    rows = []
    factor_rows = []
    for symbol_index in range(60):
        symbol = f"S{symbol_index:03d}"
        industry = f"I{symbol_index % 5}"
        signal = ((symbol_index * 13) % 61 - 30) / 30.0
        size = ((symbol_index * 7) % 60) / 10.0
        for day_index, day in enumerate(dates):
            noise = ((day_index * 17 + symbol_index * 11) % 13 - 6) * 0.00001
            factor_value = signal + noise
            forward = signal * 0.03 + noise * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "forward_return_5d": forward if day_index < 595 else None,
                    "industry_l1": industry,
                    "log_float_mv": size,
                }
            )
            factor_rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "factor_value": factor_value,
                }
            )
    prices = pl.DataFrame(rows)
    factor_panel = pl.DataFrame(factor_rows)
    context = {
        "prices": prices,
        "calendar": prices.select(["symbol", "date"]),
        "minimum_cross_section": 30,
        "data_fingerprint": "sha256:test",
    }
    if include_event:
        def evaluator(_context: dict, parameter: float):
            return (
                factor_panel,
                "factor_value",
                f"synthetic_event({int(parameter)})",
            )

        context["event_evaluators"] = {"synthetic_event": evaluator}
    return context, factor_panel


def test_load_p4_families_returns_all_registered_families() -> None:
    families = scan.load_p4_families(ROOT / "data")

    assert len(families) == 23
    assert sum(family.kind == "threshold" for family in families) == 18
    assert sum(family.kind == "event_holding" for family in families) == 5


def test_threshold_family_scan_emits_all_parameters_and_recommends_platform() -> None:
    context, factor_panel = _synthetic_context()
    family = _threshold_family()

    result = scan.scan_threshold_family(
        family,
        context,
        factor_panel,
        "sha256:test",
        date(2024, 6, 1),
        date(2025, 8, 1),
    )

    assert [trial["parameter"] for trial in result["trials"]] == [-0.3, 0.0, 0.3]
    assert result["recommendation_status"] == "recommended"
    assert result["recommended_parameter"] is not None
    assert all(trial["threshold_mode"] == "absolute" for trial in result["trials"])


def test_event_holding_scan_uses_registered_event_evaluator() -> None:
    context, _ = _synthetic_context(include_event=True)
    family = _event_family()

    result = scan.scan_event_holding_family(
        family,
        context,
        "sha256:test",
        date(2024, 6, 1),
        date(2025, 8, 1),
    )

    assert [trial["parameter"] for trial in result["trials"]] == [5.0, 10.0, 20.0]
    assert result["role"] == "event_holding_research"
    assert result["recommendation_status"] == "recommended"


def test_single_parameter_threshold_is_rejected_as_unverified() -> None:
    context, factor_panel = _synthetic_context()
    family = _threshold_family(grid=[0.0], current=0.0)

    result = scan.scan_threshold_family(
        family,
        context,
        factor_panel,
        "sha256:test",
        date(2024, 6, 1),
        date(2025, 8, 1),
    )

    assert result["recommendation_status"] == "rejected"
    assert result["trials"][0]["plateau_status"] == "single_point_unverified"


def test_p4_registry_payload_is_isolated_from_p1_p2_p3() -> None:
    families = scan.load_p4_families(ROOT / "data")
    payload = scan.p4_registry_payload(families)

    assert payload["schema_version"] == 1
    assert len(payload["families"]) == 23
    assert all(family["kind"] in {"threshold", "event_holding"} for family in payload["families"])
    assert "idio_vol" not in {family["family_id"] for family in payload["families"]}
