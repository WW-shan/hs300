"""多表 TTM 财务因子与 PIT 日期测试。

覆盖:
- 历史源公告日整体晚一年时的法定截止日回退;
- 早于/等于截止日的真实公告日保留;
- 多表 TTM 派生 (ocf_ni / accrual / ocf_debt) 的数值与逐因子生效日;
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


def _write_tables(
    root: Path,
    *,
    metrics: pl.DataFrame | None = None,
    balance: pl.DataFrame | None = None,
    cash: pl.DataFrame | None = None,
) -> None:
    tables = {"metrics": metrics, "balance_sheet": balance, "cash_flow": cash}
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
    _write_tables(tmp_path, metrics=_metrics_frame(), balance=_balance_frame(), cash=_cash_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)

    np.testing.assert_allclose(row["ocf_ni"], 44.0 / 52.0, rtol=1e-12)
    np.testing.assert_allclose(row["accrual"], 8.0 / 1000.0, rtol=1e-12)
    np.testing.assert_allclose(row["ocf_debt"], 44.0 / 400.0, rtol=1e-12)

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


def test_attach_uses_each_factor_own_pit_date(tmp_path: Path):
    _write_tables(tmp_path, metrics=_metrics_frame(), balance=_balance_frame(), cash=_cash_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 10), 30)
    attached = attach_fundamental_factors(panel, snapshot, ["ocf_ni", "accrual", "ocf_debt"]).sort("date")
    by_date = {row["date"]: row for row in attached.iter_rows(named=True)}

    # ocf_debt 依赖现金流+资产负债表, 2025-04-15 公告, 4-16 起生效
    assert by_date[date(2025, 4, 15)]["ocf_debt"] is None
    np.testing.assert_allclose(by_date[date(2025, 4, 16)]["ocf_debt"], 0.11, rtol=1e-12)
    # ocf_ni/accrual 被 metrics 的 PIT 日期拖到 2025-05-03, 5-04 起生效
    assert by_date[date(2025, 5, 3)]["ocf_ni"] is None
    assert by_date[date(2025, 5, 3)]["accrual"] is None
    np.testing.assert_allclose(by_date[date(2025, 5, 4)]["ocf_ni"], 44.0 / 52.0, rtol=1e-12)
    np.testing.assert_allclose(by_date[date(2025, 5, 4)]["accrual"], 0.008, rtol=1e-12)


def test_missing_side_tables_yield_null_derived_factors(tmp_path: Path):
    _write_tables(tmp_path, metrics=_metrics_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 1), 40)
    attached = attach_fundamental_factors(panel, snapshot, ["ocf_ni", "accrual", "ocf_debt", "roe_latest"])
    for name in ("ocf_ni", "accrual", "ocf_debt"):
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
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)
    assert row["ocf_ni"] is None
    assert row["accrual"] is None
    assert row["ocf_debt"] is None

    # 季度缺口: Q2 缺失时 Q4 的 TTM 不得用 Q1-YTD 错算
    gap_root = tmp_path / "gap"
    metrics = _metrics_frame().filter(pl.col("period_end") != "2024-06-30")
    balance = _balance_frame().filter(pl.col("period_end") != "2024-06-30")
    cash = _cash_frame().filter(pl.col("period_end") != "2024-06-30")
    _write_tables(gap_root, metrics=metrics, balance=balance, cash=cash)
    gap = load_fundamental_snapshot(gap_root)
    assert gap is not None
    q4 = gap.filter(pl.col("period_end") == date(2024, 12, 31)).row(0, named=True)
    assert q4["ocf_ni"] is None
    assert q4["accrual"] is None
    assert q4["ocf_debt"] is None
    assert q4["_ttm_complete"] is False


def test_matrix_matches_panel_for_derived_factors(tmp_path: Path):
    _write_tables(tmp_path, metrics=_metrics_frame(), balance=_balance_frame(), cash=_cash_frame())
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    panel = _daily_panel(date(2025, 4, 10), 30)
    names = ["ocf_ni", "accrual", "ocf_debt"]
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


def test_derived_names_are_registered():
    from app.backtest.fundamentals import FUNDAMENTAL_FACTOR_NAMES

    assert DERIVED_FUNDAMENTAL_FACTOR_NAMES <= FUNDAMENTAL_FACTOR_NAMES
    from app.backtest.factor import FACTOR_COLUMNS

    assert {item["id"] for item in FACTOR_COLUMNS} >= DERIVED_FUNDAMENTAL_FACTOR_NAMES
