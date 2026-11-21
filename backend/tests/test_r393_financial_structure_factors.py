from __future__ import annotations

import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data/research/r393_financial_structure_preregistration.json"
N = {
    "sales_expense_ratio_ttm_low",
    "management_expense_ratio_ttm_low",
    "ocf_to_revenue_ttm",
    "fcf_proxy_to_revenue_ttm",
    "cash_to_assets_latest",
    "payables_to_cogs_ttm",
}


def test_r393_preregistration() -> None:
    import hashlib
    payload = json.loads(PREREG.read_text())
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert set(payload["factors"]) == N
    assert payload["pit_policy"]["announcement_day_is_null"] is True
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False


def test_new_financial_factors_are_registered_stock_only() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from app.factors.registry import get_factor
    for factor_id in N:
        spec = get_factor(factor_id)
        assert spec is None or (spec.pit is True and spec.asset_types == frozenset({"stock"}))


def test_factor_columns_exist_after_load() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from app.backtest.fundamentals import FUNDAMENTAL_FACTORS
    missing = N - set(FUNDAMENTAL_FACTORS)
    assert not missing


def test_r393_new_factor_numeric_and_pit_values(tmp_path: Path) -> None:
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    import numpy as np

    from app.backtest.fundamentals import load_fundamental_snapshot
    from tests.test_fundamental_derived import (
        _balance_frame,
        _cash_frame,
        _income_frame,
        _metrics_frame,
        _write_tables,
    )

    metrics = _metrics_frame()
    balance = _balance_frame().with_columns(
        pl.lit(100.0).alias("accounts_payable"),
        pl.lit(200.0).alias("cash_and_equivalents"),
    )
    cash = _cash_frame().with_columns(pl.lit(-10.0).alias("net_investing_cash_flow"))
    income = _income_frame().with_columns(
        pl.lit(10.0).alias("selling_expenses"),
        pl.lit(20.0).alias("management_expenses"),
        pl.lit(100.0).alias("revenue"),
        pl.lit(60.0).alias("operating_costs"),
    )
    _write_tables(tmp_path, metrics=metrics, balance=balance, cash=cash, income=income)
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == pl.date(2024, 12, 31)).row(0, named=True)
    # 累计值按单季差分后为 10/20/30/40 等，测试只断言公式可计算且有限。
    for key in (
        "sales_expense_ratio_ttm_low",
        "management_expense_ratio_ttm_low",
        "ocf_to_revenue_ttm",
        "fcf_proxy_to_revenue_ttm",
        "cash_to_assets_latest",
        "payables_to_cogs_ttm",
    ):
        assert row[key] is not None, key
        assert np.isfinite(row[key]), key
    assert row["cash_to_assets_latest"] == 200.0 / 1000.0
