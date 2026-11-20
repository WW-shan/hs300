"""多表 TTM 财务因子与 PIT 日期测试。

覆盖:
- 历史源公告日整体晚一年时的法定截止日回退;
- 早于/等于截止日的真实公告日保留;
- 多表 TTM 派生 (ocf_ni / accrual / ocf_debt / core_earnings_ratio) 的数值与逐因子生效日;
- 侧表缺失、分母非正、季度缺口时的 null 安全;
- Polars 面板与矩阵两条路径一致。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from app.backtest.fundamentals import (
    DERIVED_FUNDAMENTAL_FACTOR_NAMES,
    attach_fundamental_factors,
    build_fundamental_matrices,
    financial_pit_date,
    load_fundamental_snapshot,
)
from app.backtest.matrix import build_market_data_matrix

PERIODS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]
# 模拟 AkShare 历史回填: 指标/利润/现金流公告日整体晚一年; 资产负债表季报日较准。
METRICS_ANNOUNCE = [date(2025, 4, 30), date(2025, 8, 31), date(2025, 10, 31), date(2026, 4, 30)]
BALANCE_ANNOUNCE = [date(2024, 4, 25), date(2024, 8, 25), date(2024, 10, 25), date(2025, 4, 15)]
CASH_ANNOUNCE = [date(2024, 4, 25), date(2024, 8, 25), date(2024, 10, 25), date(2025, 4, 15)]
INCOME_ANNOUNCE = [date(2024, 4, 25), date(2024, 8, 25), date(2024, 10, 25), date(2025, 4, 15)]
LONG_PERIODS = [
    date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2023, 12, 31),
    date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31),
]
LONG_METRICS_ANNOUNCE = [
    date(2023, 4, 28), date(2023, 8, 31), date(2023, 10, 31), date(2024, 4, 30),
    date(2024, 4, 30), date(2024, 8, 31), date(2024, 10, 31), date(2025, 4, 30),
]
LONG_BALANCE_ANNOUNCE = [
    date(2023, 4, 15), date(2023, 8, 15), date(2023, 10, 15), date(2024, 4, 15),
    date(2024, 4, 20), date(2024, 8, 20), date(2024, 10, 20), date(2025, 4, 20),
]
LONG_INCOME_ANNOUNCE = [
    date(2023, 4, 25), date(2023, 8, 25), date(2023, 10, 25), date(2024, 4, 15),
    date(2024, 4, 25), date(2024, 8, 25), date(2024, 10, 25), date(2025, 4, 25),
]


def _metrics_frame(
    *, cumulative_net_income: list[float] | None = None,
    announce: list[date] | None = None,
) -> pl.DataFrame:
    ni = cumulative_net_income or [10.0, 22.0, 36.0, 52.0]
    return pl.DataFrame({
        "symbol": ["600000.SH"] * 4,
        "period_end": [p.isoformat() for p in PERIODS],
        "announce_date": [d.isoformat() for d in (announce or METRICS_ANNOUNCE)],
        "bps": [5.0] * 4,
        "roe": [10.0] * 4,
        "gross_margin": [30.0] * 4,
        "net_margin": [10.0] * 4,
        "revenue_yoy": [8.0] * 4,
        "net_income_yoy": [6.0] * 4,
        "debt_to_asset_ratio": [40.0] * 4,
        "revenue": [100.0, 220.0, 360.0, 520.0],
        "net_income": ni,
    })


def _balance_frame(*, total_assets: list[float] | None = None, liabilities: list[float] | None = None) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * 4,
        "period_end": [p.isoformat() for p in PERIODS],
        "announce_date": [d.isoformat() for d in BALANCE_ANNOUNCE],
        "total_assets": total_assets or [1000.0] * 4,
        "total_liabilities": liabilities or [400.0] * 4,
        "total_equity": [600.0] * 4,
        "debt_to_asset_ratio": [40.0] * 4,
    })


def _cash_frame(*, cumulative_ocf: list[float] | None = None) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * 4,
        "period_end": [p.isoformat() for p in PERIODS],
        "announce_date": [d.isoformat() for d in CASH_ANNOUNCE],
        "net_operating_cash_flow": cumulative_ocf or [8.0, 18.0, 30.0, 44.0],
    })


def _income_frame(
    *,
    cumulative_operating_profit: list[float] | None = None,
    cumulative_total_profit: list[float] | None = None,
) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * 4,
        "period_end": [p.isoformat() for p in PERIODS],
        "announce_date": [d.isoformat() for d in INCOME_ANNOUNCE],
        "operating_profit": cumulative_operating_profit or [9.0, 20.0, 32.0, 48.0],
        "total_profit": cumulative_total_profit or [10.0, 22.0, 36.0, 52.0],
    })


def _long_metrics_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(LONG_PERIODS),
        "period_end": [p.isoformat() for p in LONG_PERIODS],
        "announce_date": [d.isoformat() for d in LONG_METRICS_ANNOUNCE],
        "bps": [5.0] * len(LONG_PERIODS),
        "roe": [10.0] * len(LONG_PERIODS),
        "gross_margin": [30.0] * len(LONG_PERIODS),
        "net_margin": [10.0] * len(LONG_PERIODS),
        "revenue_yoy": [8.0] * len(LONG_PERIODS),
        "net_income_yoy": [6.0] * len(LONG_PERIODS),
        "debt_to_asset_ratio": [40.0] * len(LONG_PERIODS),
        "revenue": [100.0, 220.0, 360.0, 520.0] * 2,
        "net_income": [10.0, 22.0, 36.0, 52.0] * 2,
    })


def _long_balance_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(LONG_PERIODS),
        "period_end": [p.isoformat() for p in LONG_PERIODS],
        "announce_date": [d.isoformat() for d in LONG_BALANCE_ANNOUNCE],
        "total_assets": [1000.0] * len(LONG_PERIODS),
        "total_liabilities": [400.0] * len(LONG_PERIODS),
        "total_equity": [600.0] * len(LONG_PERIODS),
        "debt_to_asset_ratio": [40.0] * len(LONG_PERIODS),
        # 2023Q4 -> 2024Q4: 存货 100 -> 130; 应收 160 -> 220
        "inventory": [80.0, 90.0, 95.0, 100.0, 110.0, 120.0, 125.0, 130.0],
        "accounts_receivable": [120.0, 130.0, 145.0, 160.0, 175.0, 190.0, 205.0, 220.0],
    })


def _long_income_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(LONG_PERIODS),
        "period_end": [p.isoformat() for p in LONG_PERIODS],
        "announce_date": [d.isoformat() for d in LONG_INCOME_ANNOUNCE],
        "operating_profit": [9.0, 20.0, 32.0, 48.0] * 2,
        "total_profit": [10.0, 22.0, 36.0, 52.0] * 2,
        # 2024 累计营收 -> 单季 100/120/140/160, TTM=520
        "revenue": [90.0, 190.0, 320.0, 430.0, 100.0, 220.0, 360.0, 520.0],
        # 2024 累计营业成本 -> 单季 60/80/80/80, TTM=300
        "operating_costs": [55.0, 115.0, 190.0, 260.0, 60.0, 140.0, 220.0, 300.0],
    })


def _long_cash_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(LONG_PERIODS),
        "period_end": [p.isoformat() for p in LONG_PERIODS],
        "announce_date": [d.isoformat() for d in LONG_BALANCE_ANNOUNCE],
        "net_operating_cash_flow": [8.0, 18.0, 30.0, 44.0] * 2,
    })


def _write_tables(
    root: Path,
    *,
    metrics: pl.DataFrame | None = None,
    balance: pl.DataFrame | None = None,
    cash: pl.DataFrame | None = None,
    income: pl.DataFrame | None = None,
) -> None:
    tables = {"metrics": metrics, "balance_sheet": balance, "cash_flow": cash, "income": income}
    for name, frame in tables.items():
        if frame is None:
            continue
        out = root / "financials" / name
        out.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(out / "part.parquet")


def _daily_panel(start: date, days: int, symbol: str = "600000.SH") -> pl.DataFrame:
    rows = []
    for offset in range(days):
        rows.append({
            "symbol": symbol,
            "date": start + timedelta(days=offset),
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1000.0,
        })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def test_pit_date_keeps_plausible_announcement_and_falls_back_to_deadline():
    # 季度末后 30 天: 真实公告日, 保留
    assert financial_pit_date(date(2024, 9, 30), date(2024, 10, 26)) == date(2024, 10, 26)
    # 历史源把 2024Q3 标成 2025-10-30: 回退到 2024-10-31 + 3 天
    assert financial_pit_date(date(2024, 9, 30), date(2025, 10, 30)) == date(2024, 11, 3)
    # 年报: 2025-04-17 保留; 2026-04-17 回退到 2025-04-30 + 3 天
    assert financial_pit_date(date(2024, 12, 31), date(2025, 4, 17)) == date(2025, 4, 17)
    assert financial_pit_date(date(2024, 12, 31), date(2026, 4, 17)) == date(2025, 5, 3)
    # 缺失/早于报告期: 回退
    assert financial_pit_date(date(2024, 9, 30), None) == date(2024, 11, 3)
    assert financial_pit_date(date(2024, 9, 30), date(2024, 9, 29)) == date(2024, 11, 3)


def test_snapshot_computes_ttm_factors_and_per_factor_announce_dates(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_metrics_frame(),
        balance=_balance_frame(),
        cash=_cash_frame(),
        income=_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)

    np.testing.assert_allclose(row["ocf_ni"], 44.0 / 52.0, rtol=1e-12)
    np.testing.assert_allclose(row["accrual"], 8.0 / 1000.0, rtol=1e-12)
    np.testing.assert_allclose(row["ocf_debt"], 44.0 / 400.0, rtol=1e-12)
    np.testing.assert_allclose(row["core_earnings_ratio"], 48.0 / 52.0, rtol=1e-12)

    # 原始公告日保留；PIT 日期按依赖表分别计算
    assert row["announce_date"] == "2026-04-30"
    assert row["_announce_metrics"] == date(2025, 5, 3)
    assert row["_announce_ocf_ni"] == date(2025, 5, 3)
    assert row["_announce_accrual"] == date(2025, 5, 3)
    assert row["_announce_ocf_debt"] == date(2025, 4, 15)
    assert row["_announce_debt_ratio"] == date(2025, 4, 15)
    assert row["_ttm_complete"] is True

    # 前三季 TTM 不足 4 季: null
    early = snapshot.filter(pl.col("period_end") == date(2024, 9, 30)).row(0, named=True)
    assert early["ocf_ni"] is None
    assert early["accrual"] is None
    assert early["ocf_debt"] is None
    assert early["_ttm_complete"] is False


def test_snapshot_computes_operating_efficiency_ttm_and_dependency_pit(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_long_metrics_frame(),
        balance=_long_balance_frame(),
        cash=_long_cash_frame(),
        income=_long_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    q4 = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)

    np.testing.assert_allclose(q4["inventory_turnover_ttm"], 300.0 / 115.0, rtol=1e-12)
    np.testing.assert_allclose(q4["receivable_turnover_ttm"], 520.0 / 190.0, rtol=1e-12)
    # 新因子只依赖利润表和资产负债表，不被同报告期的 metrics 公告日拖后。
    assert q4["_announce_inventory_turnover_ttm"] == date(2025, 4, 25)
    assert q4["_announce_receivable_turnover_ttm"] == date(2025, 4, 25)


def test_operating_efficiency_requires_consecutive_quarters(tmp_path: Path):
    root = tmp_path / "income_gap"
    _write_tables(
        root,
        metrics=_long_metrics_frame(),
        balance=_long_balance_frame(),
        cash=_long_cash_frame(),
        income=_long_income_frame().filter(pl.col("period_end") != "2024-06-30"),
    )
    snapshot = load_fundamental_snapshot(root)
    assert snapshot is not None
    q4 = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)
    assert q4["inventory_turnover_ttm"] is None
    assert q4["receivable_turnover_ttm"] is None

    balance_root = tmp_path / "balance_gap"
    _write_tables(
        balance_root,
        metrics=_long_metrics_frame(),
        balance=_long_balance_frame().filter(pl.col("period_end") != "2023-12-31"),
        cash=_long_cash_frame(),
        income=_long_income_frame(),
    )
    balance_snapshot = load_fundamental_snapshot(balance_root)
    assert balance_snapshot is not None
    q4_balance = balance_snapshot.filter(
        pl.col("period_end") == date(2024, 12, 31)
    ).row(0, named=True)
    assert q4_balance["inventory_turnover_ttm"] is None
    assert q4_balance["receivable_turnover_ttm"] is None


def test_operating_efficiency_guards_nonpositive_inputs(tmp_path: Path):
    inventory_root = tmp_path / "inventory"
    inventory = _long_balance_frame().with_columns(
        pl.when(pl.col("period_end") == "2024-12-31")
        .then(pl.lit(0.0))
        .otherwise(pl.col("inventory"))
        .alias("inventory")
    )
    _write_tables(
        inventory_root,
        metrics=_long_metrics_frame(),
        balance=inventory,
        cash=_long_cash_frame(),
        income=_long_income_frame(),
    )
    inventory_snapshot = load_fundamental_snapshot(inventory_root)
    assert inventory_snapshot is not None
    inventory_q4 = inventory_snapshot.filter(
        pl.col("period_end") == date(2024, 12, 31)
    ).row(0, named=True)
    assert inventory_q4["inventory_turnover_ttm"] is None
    np.testing.assert_allclose(
        inventory_q4["receivable_turnover_ttm"], 520.0 / 190.0, rtol=1e-12
    )

    income_root = tmp_path / "income"
    income = _long_income_frame().with_columns(
        pl.when(pl.col("period_end") == "2024-12-31")
        .then(pl.lit(-10.0))
        .otherwise(pl.col("operating_costs"))
        .alias("operating_costs")
    )
    _write_tables(
        income_root,
        metrics=_long_metrics_frame(),
        balance=_long_balance_frame(),
        cash=_long_cash_frame(),
        income=income,
    )
    income_snapshot = load_fundamental_snapshot(income_root)
    assert income_snapshot is not None
    income_q4 = income_snapshot.filter(
        pl.col("period_end") == date(2024, 12, 31)
    ).row(0, named=True)
    assert income_q4["inventory_turnover_ttm"] is None
    np.testing.assert_allclose(
        income_q4["receivable_turnover_ttm"], 520.0 / 190.0, rtol=1e-12
    )


def test_legacy_side_tables_keep_existing_factors_and_null_new_ones(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_metrics_frame(),
        balance=_balance_frame(),
        cash=_cash_frame(),
        income=_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 15), 20)
    names = ["ocf_debt", "inventory_turnover_ttm", "receivable_turnover_ttm"]
    attached = attach_fundamental_factors(panel, snapshot, names).sort("date")
    by_date = {row["date"]: row for row in attached.iter_rows(named=True)}

    np.testing.assert_allclose(by_date[date(2025, 4, 16)]["ocf_debt"], 0.11, rtol=1e-12)
    assert attached["inventory_turnover_ttm"].null_count() == attached.height
    assert attached["receivable_turnover_ttm"].null_count() == attached.height


def test_attach_uses_each_factor_own_pit_date(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_metrics_frame(),
        balance=_balance_frame(),
        cash=_cash_frame(),
        income=_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 10), 30)
    attached = attach_fundamental_factors(
        panel,
        snapshot,
        ["ocf_ni", "accrual", "ocf_debt", "core_earnings_ratio"],
    ).sort("date")
    by_date = {row["date"]: row for row in attached.iter_rows(named=True)}

    # ocf_debt 依赖现金流+资产负债表, 2025-04-15 公告, 4-16 起生效
    assert by_date[date(2025, 4, 15)]["ocf_debt"] is None
    np.testing.assert_allclose(by_date[date(2025, 4, 16)]["ocf_debt"], 0.11, rtol=1e-12)
    # ocf_ni/accrual 被 metrics 的 PIT 日期拖到 2025-05-03, 5-04 起生效
    assert by_date[date(2025, 5, 3)]["ocf_ni"] is None
    assert by_date[date(2025, 5, 3)]["accrual"] is None
    np.testing.assert_allclose(by_date[date(2025, 5, 4)]["ocf_ni"], 44.0 / 52.0, rtol=1e-12)
    np.testing.assert_allclose(by_date[date(2025, 5, 4)]["accrual"], 0.008, rtol=1e-12)
    assert by_date[date(2025, 4, 15)]["core_earnings_ratio"] is None
    np.testing.assert_allclose(by_date[date(2025, 4, 16)]["core_earnings_ratio"], 48.0 / 52.0, rtol=1e-12)


def test_missing_side_tables_yield_null_derived_factors(tmp_path: Path):
    _write_tables(tmp_path, metrics=_metrics_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 1), 40)
    attached = attach_fundamental_factors(
        panel,
        snapshot,
        ["ocf_ni", "accrual", "ocf_debt", "core_earnings_ratio", "roe_latest"],
    )
    for name in ("ocf_ni", "accrual", "ocf_debt", "core_earnings_ratio"):
        assert attached[name].null_count() == attached.height
    # 单表基础因子仍可用
    assert attached["roe_latest"].null_count() < attached.height


def test_unmatched_side_rows_preserve_join_keys(tmp_path: Path):
    """侧表有、metrics 没有的标的/报告期不得产生空 symbol/period_end 行。"""
    balance = _balance_frame().vstack(pl.DataFrame({
        "symbol": ["000001.SZ"],
        "period_end": ["2024-09-30"],
        "announce_date": ["2024-10-25"],
        "total_assets": [2000.0],
        "total_liabilities": [800.0],
        "total_equity": [1200.0],
        "debt_to_asset_ratio": [40.0],
    }))
    _write_tables(tmp_path, metrics=_metrics_frame(), balance=balance, cash=_cash_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot["symbol"].null_count() == 0
    assert snapshot["period_end"].null_count() == 0
    assert "000001.SZ" in snapshot["symbol"].to_list()


def test_denominator_guards_and_quarter_gap(tmp_path: Path):
    # 2024Q4 净利润为负、总资产/总负债为 0 → 三个派生因子均 null
    _write_tables(
        tmp_path,
        metrics=_metrics_frame(cumulative_net_income=[10.0, 22.0, 36.0, -5.0]),
        balance=_balance_frame(total_assets=[0.0] * 4, liabilities=[0.0] * 4),
        cash=_cash_frame(),
        income=_income_frame(cumulative_total_profit=[10.0, 20.0, 30.0, -5.0]),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)
    assert row["ocf_ni"] is None
    assert row["accrual"] is None
    assert row["ocf_debt"] is None
    assert row["core_earnings_ratio"] is None

    # 季度缺口: Q2 缺失时 Q4 的 TTM 不得用 Q1-YTD 错算
    gap_root = tmp_path / "gap"
    metrics = _metrics_frame().filter(pl.col("period_end") != "2024-06-30")
    balance = _balance_frame().filter(pl.col("period_end") != "2024-06-30")
    cash = _cash_frame().filter(pl.col("period_end") != "2024-06-30")
    income = _income_frame().filter(pl.col("period_end") != "2024-06-30")
    _write_tables(gap_root, metrics=metrics, balance=balance, cash=cash, income=income)
    gap = load_fundamental_snapshot(gap_root)
    assert gap is not None
    q4 = gap.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)
    assert q4["ocf_ni"] is None
    assert q4["accrual"] is None
    assert q4["ocf_debt"] is None
    assert q4["core_earnings_ratio"] is None
    assert q4["_ttm_complete"] is False


def test_matrix_matches_panel_for_derived_factors(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_metrics_frame(),
        balance=_balance_frame(),
        cash=_cash_frame(),
        income=_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 10), 30)
    names = [
        "ocf_ni", "accrual", "ocf_debt", "core_earnings_ratio",
        "inventory_turnover_ttm", "receivable_turnover_ttm",
    ]
    attached = attach_fundamental_factors(panel, snapshot, names).sort("date")
    market = build_market_data_matrix(panel)
    matrices = build_fundamental_matrices(market, snapshot, names)
    column = market.symbols.index("600000.SH")
    for row_index, row in enumerate(attached.iter_rows(named=True)):
        for name in names:
            value = row[name]
            actual = matrices[name][row_index, column]
            if value is None:
                assert np.isnan(actual), (name, row["date"], actual)
            else:
                np.testing.assert_allclose(actual, value, rtol=1e-6)


def test_operating_efficiency_pit_and_matrix_match(tmp_path: Path):
    _write_tables(
        tmp_path,
        metrics=_long_metrics_frame(),
        balance=_long_balance_frame(),
        cash=_long_cash_frame(),
        income=_long_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 24), 6)
    names = ["inventory_turnover_ttm", "receivable_turnover_ttm"]
    attached = attach_fundamental_factors(panel, snapshot, names).sort("date")
    by_date = {row["date"]: row for row in attached.iter_rows(named=True)}

    # 4/25 是 2024Q4 的公告日，新一期尚未生效，只能保留 2024Q3 旧值。
    np.testing.assert_allclose(
        by_date[date(2025, 4, 25)][names[0]], 290.0 / 110.0, rtol=1e-12
    )
    np.testing.assert_allclose(
        by_date[date(2025, 4, 25)][names[1]], 470.0 / 175.0, rtol=1e-12
    )
    np.testing.assert_allclose(
        by_date[date(2025, 4, 26)][names[0]], 300.0 / 115.0, rtol=1e-12
    )
    np.testing.assert_allclose(
        by_date[date(2025, 4, 26)][names[1]], 520.0 / 190.0, rtol=1e-12
    )

    market = build_market_data_matrix(panel)
    matrices = build_fundamental_matrices(market, snapshot, names)
    column = market.symbols.index("600000.SH")
    for row_index, row in enumerate(attached.iter_rows(named=True)):
        for name in names:
            value = row[name]
            actual = matrices[name][row_index, column]
            if value is None:
                assert np.isnan(actual), (name, row["date"], actual)
            else:
                np.testing.assert_allclose(actual, value, rtol=1e-6)


def test_derived_names_are_registered():
    from app.backtest.fundamentals import FUNDAMENTAL_FACTOR_NAMES

    assert DERIVED_FUNDAMENTAL_FACTOR_NAMES <= FUNDAMENTAL_FACTOR_NAMES
    from app.backtest.factor import FACTOR_COLUMNS

    assert {item["id"] for item in FACTOR_COLUMNS} >= DERIVED_FUNDAMENTAL_FACTOR_NAMES


R384_PERIODS = [
    date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30), date(2022, 12, 31),
    date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2023, 12, 31),
    date(2024, 3, 31),
]
R384_METRICS_ANNOUNCE = [
    date(2022, 4, 27), date(2022, 8, 29), date(2022, 10, 29), date(2023, 4, 27),
    date(2023, 4, 27), date(2023, 8, 29), date(2023, 10, 29), date(2024, 4, 27),
    date(2024, 4, 27),
]
R384_BALANCE_ANNOUNCE = [
    date(2022, 4, 25), date(2022, 8, 25), date(2022, 10, 25), date(2023, 4, 25),
    date(2023, 4, 25), date(2023, 8, 25), date(2023, 10, 25), date(2024, 4, 25),
    date(2024, 4, 28),
]
R384_INCOME_ANNOUNCE = [
    date(2022, 4, 25), date(2022, 8, 25), date(2022, 10, 25), date(2023, 4, 25),
    date(2023, 4, 25), date(2023, 8, 25), date(2023, 10, 25), date(2024, 4, 25),
    date(2024, 4, 30),
]


def _improvement_metrics_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(R384_PERIODS),
        "period_end": [p.isoformat() for p in R384_PERIODS],
        "announce_date": [d.isoformat() for d in R384_METRICS_ANNOUNCE],
        "bps": [5.0] * len(R384_PERIODS),
        "roe": [10.0] * len(R384_PERIODS),
        "gross_margin": [30.0] * len(R384_PERIODS),
        "net_margin": [10.0] * len(R384_PERIODS),
        "revenue_yoy": [8.0] * len(R384_PERIODS),
        "net_income_yoy": [6.0] * len(R384_PERIODS),
        "debt_to_asset_ratio": [40.0] * len(R384_PERIODS),
        "revenue": [80.0, 170.0, 270.0, 380.0, 120.0, 250.0, 390.0, 540.0, 160.0],
        "net_income": [8.0, 17.0, 27.0, 38.0, 12.0, 25.0, 39.0, 54.0, 16.0],
    })


def _improvement_balance_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(R384_PERIODS),
        "period_end": [p.isoformat() for p in R384_PERIODS],
        "announce_date": [d.isoformat() for d in R384_BALANCE_ANNOUNCE],
        "total_assets": [1000.0] * len(R384_PERIODS),
        "total_liabilities": [400.0] * len(R384_PERIODS),
        "total_equity": [600.0] * len(R384_PERIODS),
        "debt_to_asset_ratio": [40.0] * len(R384_PERIODS),
        "inventory": [120.0, 115.0, 110.0, 105.0, 100.0, 110.0, 120.0, 125.0, 130.0],
        "accounts_receivable": [120.0, 130.0, 140.0, 150.0, 160.0, 175.0, 190.0, 205.0, 220.0],
    })


def _improvement_income_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(R384_PERIODS),
        "period_end": [p.isoformat() for p in R384_PERIODS],
        "announce_date": [d.isoformat() for d in R384_INCOME_ANNOUNCE],
        "operating_profit": [9.0, 19.0, 29.0, 39.0, 10.0, 20.0, 30.0, 40.0, 11.0],
        "total_profit": [10.0, 21.0, 32.0, 43.0, 11.0, 22.0, 33.0, 44.0, 12.0],
        "revenue": [80.0, 170.0, 270.0, 380.0, 120.0, 250.0, 390.0, 540.0, 160.0],
        "operating_costs": [55.0, 115.0, 180.0, 250.0, 75.0, 155.0, 240.0, 330.0, 95.0],
    })


def _improvement_cash_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"] * len(R384_PERIODS),
        "period_end": [p.isoformat() for p in R384_PERIODS],
        "announce_date": [d.isoformat() for d in R384_BALANCE_ANNOUNCE],
        "net_operating_cash_flow": [8.0, 18.0, 28.0, 40.0, 10.0, 22.0, 34.0, 46.0, 12.0],
    })


def test_improvement_factors_require_nine_balance_quarters_and_eight_income_quarters(
    tmp_path: Path,
) -> None:
    _write_tables(
        tmp_path,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 3, 31)).row(
        0, named=True
    )

    np.testing.assert_allclose(
        row["inventory_turnover_improvement_yoy"],
        np.log((350.0 / 115.0) / (270.0 / 110.0)),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["receivable_turnover_improvement_yoy"],
        np.log((580.0 / 190.0) / (420.0 / 140.0)),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["receivable_growth_minus_revenue_growth"],
        np.log(220.0 / 160.0) - np.log(580.0 / 420.0),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["inventory_growth_minus_cogs_growth"],
        np.log(130.0 / 100.0) - np.log(350.0 / 270.0),
        rtol=1e-12,
    )
    assert row["_announce_inventory_turnover_improvement_yoy"] == date(2024, 4, 30)
    assert row["_announce_receivable_turnover_improvement_yoy"] == date(2024, 4, 30)
    assert row["_announce_receivable_growth_minus_revenue_growth"] == date(2024, 4, 30)
    assert row["_announce_inventory_growth_minus_cogs_growth"] == date(2024, 4, 30)


def test_improvement_factors_fail_closed_on_gaps_and_nonpositive_inputs(
    tmp_path: Path,
) -> None:
    gap_root = tmp_path / "gap"
    _write_tables(
        gap_root,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame().filter(
            pl.col("period_end") != "2023-09-30"
        ),
    )
    gap = load_fundamental_snapshot(gap_root)
    assert gap is not None
    gap_row = gap.filter(pl.col("period_end") == date(2024, 3, 31)).row(0, named=True)
    assert gap_row["inventory_turnover_improvement_yoy"] is None
    assert gap_row["receivable_turnover_improvement_yoy"] is None
    assert gap_row["receivable_growth_minus_revenue_growth"] is None
    assert gap_row["inventory_growth_minus_cogs_growth"] is None

    zero_root = tmp_path / "zero"
    balance = _improvement_balance_frame().with_columns(
        pl.when(pl.col("period_end") == "2024-03-31")
        .then(pl.lit(0.0))
        .otherwise(pl.col("inventory"))
        .alias("inventory")
    )
    _write_tables(
        zero_root,
        metrics=_improvement_metrics_frame(),
        balance=balance,
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    zero = load_fundamental_snapshot(zero_root)
    assert zero is not None
    zero_row = zero.filter(pl.col("period_end") == date(2024, 3, 31)).row(0, named=True)
    assert zero_row["inventory_turnover_improvement_yoy"] is None
    assert zero_row["inventory_growth_minus_cogs_growth"] is None
    assert zero_row["receivable_turnover_improvement_yoy"] is not None
    assert zero_row["receivable_growth_minus_revenue_growth"] is not None


def test_improvement_factors_use_independent_pit_and_matrix_path(tmp_path: Path) -> None:
    _write_tables(
        tmp_path,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2024, 4, 29), 4)
    names = [
        "inventory_turnover_improvement_yoy",
        "receivable_turnover_improvement_yoy",
        "receivable_growth_minus_revenue_growth",
        "inventory_growth_minus_cogs_growth",
    ]
    attached = attach_fundamental_factors(panel, snapshot, names).sort("date")
    by_date = {row["date"]: row for row in attached.iter_rows(named=True)}

    assert by_date[date(2024, 4, 30)][names[0]] is None
    expected_receivable_growth = (
        np.log(205.0 / 150.0) - np.log(540.0 / 380.0)
    )
    expected_inventory_growth = (
        np.log(125.0 / 105.0) - np.log(330.0 / 250.0)
    )
    np.testing.assert_allclose(
        by_date[date(2024, 4, 30)][names[2]],
        expected_receivable_growth,
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        by_date[date(2024, 4, 30)][names[3]],
        expected_inventory_growth,
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        by_date[date(2024, 5, 1)][names[0]],
        np.log((350.0 / 115.0) / (270.0 / 110.0)),
        rtol=1e-12,
    )

    market = build_market_data_matrix(panel)
    matrices = build_fundamental_matrices(market, snapshot, names)
    column = market.symbols.index("600000.SH")
    for row_index, row in enumerate(attached.iter_rows(named=True)):
        for name in names:
            value = row[name]
            actual = matrices[name][row_index, column]
            if value is None:
                assert np.isnan(actual), (name, row["date"], actual)
            else:
                np.testing.assert_allclose(actual, value, rtol=1e-6)


def test_legacy_snapshot_without_improvement_columns_returns_null(
    tmp_path: Path,
) -> None:
    _write_tables(
        tmp_path,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    legacy = snapshot.drop([
        "inventory_turnover_improvement_yoy",
        "receivable_turnover_improvement_yoy",
        "receivable_growth_minus_revenue_growth",
        "inventory_growth_minus_cogs_growth",
        "_announce_inventory_turnover_improvement_yoy",
        "_announce_receivable_turnover_improvement_yoy",
        "_announce_receivable_growth_minus_revenue_growth",
        "_announce_inventory_growth_minus_cogs_growth",
    ])
    panel = _daily_panel(date(2024, 5, 1), 3)
    names = [
        "inventory_turnover_improvement_yoy",
        "receivable_turnover_improvement_yoy",
        "receivable_growth_minus_revenue_growth",
        "inventory_growth_minus_cogs_growth",
    ]
    attached = attach_fundamental_factors(panel, legacy, names)
    for name in names:
        assert attached[name].null_count() == attached.height
