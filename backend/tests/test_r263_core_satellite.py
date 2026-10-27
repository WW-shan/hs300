from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backtest_r263_core_satellite.py"


def _load():
    spec = importlib.util.spec_from_file_location("r263_core_satellite", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_monthly_combine_rebalances_at_month_boundary() -> None:
    mod = _load()
    dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 2, 3), date(2025, 2, 4)]
    core = pl.DataFrame({"date": dates, "value": [1.0, 1.1, 1.21, 1.331]})
    sat = pl.DataFrame({"date": dates, "value": [1.0, 0.9, 0.99, 0.891]})

    out = mod._monthly_combine(core, sat, 0.7, 0.3)

    assert abs(out["value"][1] - 1.04) < 1e-9
    assert abs(out["value"][2] - 1.144) < 1e-9
    assert abs(out["value"][3] - 1.144 * 1.04) < 1e-9


def test_slice_rebase_starts_at_one() -> None:
    mod = _load()
    frame = pl.DataFrame(
        {
            "date": [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)],
            "value": [10.0, 11.0, 12.0],
        }
    )

    out = mod._slice_rebase(frame, date(2025, 1, 3), date(2025, 1, 6))

    assert out["value"].to_list() == [1.0, 12.0 / 11.0]
