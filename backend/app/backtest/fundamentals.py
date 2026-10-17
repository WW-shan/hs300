"""财务因子: 基于本地财务快照的点时 (point-in-time) 无未来函数接入。

数据契约:
- 输入为 data/financials/{metrics,income,balance_sheet,cash_flow}/part.parquet;
- ``metrics`` 是基础快照; ``balance_sheet`` / ``cash_flow`` 用于 TTM 派生
  (``ocf_ni`` / ``accrual`` / ``ocf_debt``), 缺失时派生因子保持 null;
- 原始 ``announce_date`` 保留用于审计, 但历史源字段常是"最后更新日"而非首次
  披露日 (实测 2021~2025H1 中位滞后约 412 天)。引擎不直接使用原始值, 而是
  按 ``financial_pit_date`` 归一:
  * 原始公告日在 (报告期, 法定披露截止日] 内 → 保留实际日;
  * 缺失/早于报告期/晚于截止日 → 使用"截止日 + 3 个自然日"的保守回退。
  法定截止日: Q1=4/30, H1=8/31, Q3=10/31, FY=次年 4/30。
- 派生因子按各自依赖表的最晚 PIT 日生效:
  * ocf_ni = max(metrics, cash_flow);
  * accrual = max(metrics, cash_flow, balance_sheet);
  * ocf_debt = max(cash_flow, balance_sheet);
  * debt_ratio_latest = balance_sheet (缺失时退回 metrics)。
- 因子只在 **严格晚于生效日的交易日** 才有值 (公告多在盘后发布, 保守取 T+1),
  此前保持 null; 无财务数据一律 null, 绝不填 0。
- TTM: 累计值(YTD)按同财年连续季度差分还原单季, 再滚动 4 季求和;
  季度缺口时不使用累计值硬凑, 该窗口保持 null。

性能:
- 财务表约 12 万行/表, 读取与窗口计算为毫秒级; 矩阵路径仅在请求该因子时
  物化一张 float32 TxN 矩阵。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

PIT_FALLBACK_BUFFER_DAYS = 3

# 单表基础财务因子: 因子名 -> (快照列名, 是否除以收盘价, 生效日列)
BASE_FUNDAMENTAL_FACTORS: dict[str, dict[str, Any]] = {
    "pb_latest": {"column": "bps", "price_ratio": True, "announce_column": "_announce_metrics"},
    "roe_latest": {"column": "roe", "price_ratio": False, "announce_column": "_announce_metrics"},
    "gross_margin_latest": {"column": "gross_margin", "price_ratio": False, "announce_column": "_announce_metrics"},
    "net_margin_latest": {"column": "net_margin", "price_ratio": False, "announce_column": "_announce_metrics"},
    "revenue_yoy_latest": {"column": "revenue_yoy", "price_ratio": False, "announce_column": "_announce_metrics"},
    "net_income_yoy_latest": {"column": "net_income_yoy", "price_ratio": False, "announce_column": "_announce_metrics"},
    "debt_ratio_latest": {"column": "debt_to_asset_ratio", "price_ratio": False, "announce_column": "_announce_debt_ratio"},
}

# 多表 TTM 派生因子
DERIVED_FUNDAMENTAL_FACTORS: dict[str, dict[str, Any]] = {
    "ocf_ni": {"column": "ocf_ni", "price_ratio": False, "announce_column": "_announce_ocf_ni"},
    "accrual": {"column": "accrual", "price_ratio": False, "announce_column": "_announce_accrual"},
    "ocf_debt": {"column": "ocf_debt", "price_ratio": False, "announce_column": "_announce_ocf_debt"},
}

FUNDAMENTAL_FACTORS: dict[str, dict[str, Any]] = {
    **BASE_FUNDAMENTAL_FACTORS,
    **DERIVED_FUNDAMENTAL_FACTORS,
}
FUNDAMENTAL_FACTOR_NAMES = frozenset(FUNDAMENTAL_FACTORS)
DERIVED_FUNDAMENTAL_FACTOR_NAMES = frozenset(DERIVED_FUNDAMENTAL_FACTORS)


def _statutory_deadline(period_end: date) -> date:
    """A 股定期报告法定披露截止日 (保守口径)。"""
    if period_end.month == 3:
        return date(period_end.year, 4, 30)
    if period_end.month == 6:
        return date(period_end.year, 8, 31)
    if period_end.month == 9:
        return date(period_end.year, 10, 31)
    if period_end.month == 12:
        return date(period_end.year + 1, 4, 30)
    # 非标准报告期: 给 120 天缓冲, 仍保持确定性
    return period_end + timedelta(days=120)


def financial_pit_date(period_end: date, announce_date: date | None) -> date:
    """把原始公告日归一为可用于回测的 PIT 生效日。

    原始源在历史期会把 "最新公告日期" 写成后续更新的日期 (实测晚约一年)。
    只要原始日落在报告期之后、法定截止日之前就保留; 否则回退到截止日 + 3 天。
    """
    deadline = _statutory_deadline(period_end)
    fallback = deadline + timedelta(days=PIT_FALLBACK_BUFFER_DAYS)
    if announce_date is None or announce_date <= period_end or announce_date > deadline:
        return fallback
    return announce_date


def _deadline_expr() -> pl.Expr:
    year = pl.col("period_end").dt.year()
    month = pl.col("period_end").dt.month()
    return (
        pl.when(month == 3).then(pl.date(year, 4, 30))
        .when(month == 6).then(pl.date(year, 8, 31))
        .when(month == 9).then(pl.date(year, 10, 31))
        .when(month == 12).then(pl.date(year + 1, 4, 30))
        .otherwise(pl.col("period_end") + pl.duration(days=120))
    )


def _pit_expr(announce_column: str) -> pl.Expr:
    deadline = _deadline_expr()
    raw = pl.col(announce_column)
    fallback = deadline + pl.duration(days=PIT_FALLBACK_BUFFER_DAYS)
    return (
        pl.when((raw > pl.col("period_end")) & (raw <= deadline))
        .then(raw)
        .otherwise(fallback)
    )


def _rowwise_max_dates(*columns: str) -> pl.Expr:
    """按行取最大日期; 用 symbol 条件保证全 null 字面量也返回列而不是标量。"""
    return (
        pl.when(pl.col("symbol").is_not_null())
        .then(pl.max_horizontal(*(pl.col(column) for column in columns)))
        .otherwise(None)
    )


def _rowwise_coalesce_dates(*columns: str) -> pl.Expr:
    """按行取首个非空日期; 同样用 symbol 条件避免全 null 退化为标量。"""
    return (
        pl.when(pl.col("symbol").is_not_null())
        .then(pl.coalesce([pl.col(column) for column in columns]))
        .otherwise(None)
    )


def _coalesce_full_join_keys(frame: pl.DataFrame, suffix: str) -> pl.DataFrame:
    """合并 full join 两侧的 key 列 (Polars 对未匹配右行会放 *_suffix)。"""
    right_symbol = f"symbol{suffix}"
    right_period = f"period_end{suffix}"
    expressions = []
    if right_symbol in frame.columns:
        expressions.append(pl.coalesce([pl.col("symbol"), pl.col(right_symbol)]).alias("symbol"))
    if right_period in frame.columns:
        expressions.append(pl.coalesce([pl.col("period_end"), pl.col(right_period)]).alias("period_end"))
    drop_columns = [column for column in (right_symbol, right_period) if column in frame.columns]
    return frame.with_columns(expressions).drop(drop_columns) if expressions else frame


def _read_parquet(path: Path, table: str) -> pl.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - 数据源文件损坏时按缺失处理
        logger.warning("读取财务表 %s 失败: %s", table, exc)
        return None


def _load_optional_table(
    data_dir: Path,
    table: str,
    short_name: str,
    value_columns: list[str],
) -> pl.DataFrame | None:
    """读取可选侧表并计算该表自己的 PIT 生效日。"""
    frame = _read_parquet(data_dir / "financials" / table / "part.parquet", table)
    if frame is None:
        return None
    needed = {"symbol", "period_end", "announce_date", *value_columns}
    if not needed.issubset(frame.columns):
        logger.debug("财务侧表 %s 缺少列: %s", table, sorted(needed - set(frame.columns)))
        return None
    return (
        frame.select(sorted(needed))
        .filter(
            pl.col("symbol").is_not_null()
            & pl.col("period_end").is_not_null()
            & pl.col("announce_date").is_not_null()
        )
        .with_columns([
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("_raw_announce"),
        ])
        .with_columns(_pit_expr("_raw_announce").alias(f"_announce_{short_name}"))
        .drop("_raw_announce", "announce_date")
        .sort(["symbol", "period_end"])
    )


def _load_metrics_snapshot(data_dir: Path) -> pl.DataFrame | None:
    frame = _read_parquet(data_dir / "financials" / "metrics" / "part.parquet", "metrics")
    if frame is None:
        return None
    base_columns = {spec["column"] for spec in BASE_FUNDAMENTAL_FACTORS.values()}
    required = {"symbol", "announce_date", *base_columns}
    if not required.issubset(frame.columns):
        logger.warning("财务指标快照缺少列: %s", sorted(required - set(frame.columns)))
        return None
    has_period_end = "period_end" in frame.columns
    derived_inputs = {"revenue", "net_income"}
    select_columns = sorted(
        required
        | ({"period_end"} if has_period_end else set())
        | (derived_inputs & set(frame.columns))
    )
    metrics = (
        frame.select(select_columns)
        .filter(pl.col("symbol").is_not_null() & pl.col("announce_date").is_not_null())
        .with_columns(
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).alias("announce_date"),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("_raw_announce"),
        )
    )
    if has_period_end:
        metrics = metrics.with_columns(
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date()
        )
        metrics = metrics.with_columns(_pit_expr("_raw_announce").alias("_announce_metrics"))
    else:
        # 兼容旧快照: 没有报告期时无法做截止日回退, 沿用原始公告日。
        metrics = metrics.with_columns(
            pl.lit(None, dtype=pl.Date).alias("period_end"),
            pl.col("_raw_announce").alias("_announce_metrics"),
        )
    if "revenue" not in metrics.columns:
        metrics = metrics.with_columns(pl.lit(None, dtype=pl.Float64).alias("revenue"))
    if "net_income" not in metrics.columns:
        metrics = metrics.with_columns(pl.lit(None, dtype=pl.Float64).alias("net_income"))
    return (
        metrics.drop("_raw_announce")
        .sort(["symbol", "period_end", "announce_date"])
        .unique(subset=["symbol", "period_end"], keep="last", maintain_order=True)
        .sort(["symbol", "period_end"])
    )


def _quarterly_flow(column: str) -> pl.Expr:
    """累计值(YTD)差分为单季值; 只用连续季度, 缺口保持 null。"""
    period_index = pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()
    previous_index = period_index.shift(1).over("symbol")
    consecutive = (period_index - previous_index) == 1
    previous = pl.col(column).shift(1).over("symbol")
    return (
        pl.when(pl.col("period_end").dt.quarter() == 1)
        .then(pl.col(column))
        .when(consecutive)
        .then(pl.col(column) - previous)
        .otherwise(None)
    )


def _derive_ttm_factors(snapshot: pl.DataFrame) -> pl.DataFrame:
    """在四表并集上计算 TTM 现金流/应计因子。"""
    q_net_income = _quarterly_flow("net_income")
    q_ocf = _quarterly_flow("net_operating_cash_flow")
    enriched = snapshot.with_columns([
        q_net_income.alias("_q_net_income"),
        q_ocf.alias("_q_ocf"),
    ]).with_columns([
        pl.col("_q_net_income").rolling_sum(window_size=4, min_samples=4).over("symbol").alias("_ttm_net_income"),
        pl.col("_q_ocf").rolling_sum(window_size=4, min_samples=4).over("symbol").alias("_ttm_ocf"),
    ])
    return enriched.with_columns([
        (pl.col("_ttm_net_income").is_not_null() & pl.col("_ttm_ocf").is_not_null()).alias("_ttm_complete"),
        pl.when(pl.col("_ttm_net_income") > 0)
        .then(pl.col("_ttm_ocf") / pl.col("_ttm_net_income"))
        .otherwise(None)
        .alias("ocf_ni"),
        pl.when(pl.col("total_assets") > 0)
        .then((pl.col("_ttm_net_income") - pl.col("_ttm_ocf")) / pl.col("total_assets"))
        .otherwise(None)
        .alias("accrual"),
        pl.when(pl.col("total_liabilities") > 0)
        .then(pl.col("_ttm_ocf") / pl.col("total_liabilities"))
        .otherwise(None)
        .alias("ocf_debt"),
        _rowwise_max_dates("_announce_metrics", "_announce_cash").alias("_announce_ocf_ni"),
        _rowwise_max_dates("_announce_metrics", "_announce_cash", "_announce_balance").alias("_announce_accrual"),
        _rowwise_max_dates("_announce_cash", "_announce_balance").alias("_announce_ocf_debt"),
    ]).drop(["_q_net_income", "_q_ocf", "_ttm_net_income", "_ttm_ocf"])


def load_fundamental_snapshot(data_dir: Path | None) -> pl.DataFrame | None:
    """读取财务指标快照, 并附加多表 TTM 派生列。

    返回列: symbol, period_end, announce_date (原始), 各 PIT 生效日列, 基础
    metrics 列, 以及 ocf_ni / accrual / ocf_debt (无法计算时为 null)。
    """
    if data_dir is None:
        return None
    metrics = _load_metrics_snapshot(data_dir)
    if metrics is None or metrics.is_empty():
        return None

    balance = _load_optional_table(
        data_dir, "balance_sheet", "balance", ["total_assets", "total_liabilities"]
    )
    cash = _load_optional_table(
        data_dir, "cash_flow", "cash", ["net_operating_cash_flow"]
    )

    snapshot = metrics
    if balance is None:
        snapshot = snapshot.with_columns([
            pl.lit(None, dtype=pl.Date).alias("_announce_balance"),
            pl.lit(None, dtype=pl.Float64).alias("total_assets"),
            pl.lit(None, dtype=pl.Float64).alias("total_liabilities"),
        ])
    else:
        snapshot = _coalesce_full_join_keys(
            snapshot.join(balance, on=["symbol", "period_end"], how="full", suffix="_bs"),
            "_bs",
        )
    if cash is None:
        snapshot = snapshot.with_columns([
            pl.lit(None, dtype=pl.Date).alias("_announce_cash"),
            pl.lit(None, dtype=pl.Float64).alias("net_operating_cash_flow"),
        ])
    else:
        snapshot = _coalesce_full_join_keys(
            snapshot.join(cash, on=["symbol", "period_end"], how="full", suffix="_cf"),
            "_cf",
        )

    snapshot = (
        snapshot.with_columns([
            _rowwise_coalesce_dates("_announce_balance", "_announce_metrics").alias("_announce_debt_ratio"),
            pl.col("_announce_metrics").alias("_announce"),
        ])
        .sort(["symbol", "period_end"])
    )
    snapshot = _derive_ttm_factors(snapshot)
    keep = [
        "symbol", "period_end", "announce_date",
        "_announce", "_announce_metrics", "_announce_balance", "_announce_cash",
        "_announce_debt_ratio", "_announce_ocf_ni", "_announce_accrual", "_announce_ocf_debt",
        *sorted({spec["column"] for spec in BASE_FUNDAMENTAL_FACTORS.values()}),
        "ocf_ni", "accrual", "ocf_debt", "_ttm_complete",
    ]
    return snapshot.select(keep).sort(["symbol", "period_end"])


def attach_fundamental_factors(
    panel: pl.DataFrame,
    snapshot: pl.DataFrame | None,
    names: Any,
) -> pl.DataFrame:
    """把财务因子列按公告日门控地并入日频面板。

    - snapshot 为 None (本地无财务数据): 产出全 null 列, 保持面板形状;
    - 同一因子的源列缺失/依赖表缺失: 该因子保持 null;
    - 不同因子可使用不同的 PIT 生效日列 (基础/派生因子依赖不同表)。
    """
    requested = [str(name) for name in names if str(name) in FUNDAMENTAL_FACTOR_NAMES]
    missing_columns = [name for name in requested if name not in panel.columns]
    if not missing_columns:
        return panel

    if snapshot is None:
        return panel.with_columns([
            pl.lit(None, dtype=pl.Float64).alias(name)
            for name in missing_columns
        ])

    unavailable: list[str] = []
    groups: dict[str, list[str]] = {}
    for name in missing_columns:
        spec = FUNDAMENTAL_FACTORS[name]
        announce_column = str(spec.get("announce_column", "_announce"))
        if announce_column not in snapshot.columns:
            announce_column = "_announce" if "_announce" in snapshot.columns else ""
        if not announce_column or spec["column"] not in snapshot.columns:
            unavailable.append(name)
            continue
        groups.setdefault(announce_column, []).append(name)

    for announce_column, group_names in groups.items():
        columns = sorted({FUNDAMENTAL_FACTORS[name]["column"] for name in group_names})
        right = (
            snapshot.select(["symbol", announce_column, *columns])
            .with_columns(pl.col(announce_column).dt.offset_by("1d").alias("_effective"))
            .sort(["symbol", "_effective"])
        )
        joined = panel.join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        announced = pl.col(announce_column).is_not_null() & (pl.col("date") > pl.col(announce_column))
        expressions = []
        for name in group_names:
            spec = FUNDAMENTAL_FACTORS[name]
            source = pl.col(spec["column"])
            if spec["price_ratio"]:
                value = (
                    pl.when(source > 0)
                    .then(pl.col("close") / source)
                    .otherwise(None)
                )
            else:
                value = source
            expressions.append(pl.when(announced).then(value).otherwise(None).alias(name))
        panel = joined.with_columns(expressions).drop("_effective", announce_column)

    if unavailable:
        panel = panel.with_columns([
            pl.lit(None, dtype=pl.Float64).alias(name) for name in unavailable
        ])
    return panel


def build_fundamental_matrices(
    market: Any,
    snapshot: pl.DataFrame | None,
    names: Any,
) -> dict[str, np.ndarray]:
    """为 MarketDataMatrix 构建财务因子 TxN float32 字段。

    与 attach_fundamental_factors 同一口径: 按各因子自己的 PIT 生效日,
    公告日次一交易日起前向填充, 无数据为 NaN。
    """
    requested = [str(name) for name in names if str(name) in FUNDAMENTAL_FACTOR_NAMES]
    if not requested:
        return {}

    shape = market.shape
    result: dict[str, np.ndarray] = {}
    if snapshot is None:
        for name in requested:
            result[name] = np.full(shape, np.nan, dtype=np.float32)
        return result

    asset_index = {symbol: index for index, symbol in enumerate(market.symbols)}
    labels = market.timestamp_labels
    label_dates = np.array([label[:10] for label in labels], dtype="datetime64[D]")
    symbols = snapshot["symbol"].to_list()

    for name in requested:
        spec = FUNDAMENTAL_FACTORS[name]
        announce_column = str(spec.get("announce_column", "_announce"))
        if announce_column not in snapshot.columns:
            announce_column = "_announce" if "_announce" in snapshot.columns else ""
        if not announce_column or spec["column"] not in snapshot.columns:
            result[name] = np.full(shape, np.nan, dtype=np.float32)
            continue

        raw = np.full(shape, np.nan, dtype=np.float32)
        announce_values = snapshot[announce_column].to_list()
        values = snapshot[spec["column"]].to_list()
        for row_index, symbol in enumerate(symbols):
            column_index = asset_index.get(symbol)
            if column_index is None:
                continue
            announce = announce_values[row_index]
            if announce is None:
                continue
            # 公告日之后 (严格大于) 的首个时间行索引
            start = int(np.searchsorted(label_dates, np.datetime64(announce, "D"), side="right"))
            if start >= shape[0]:
                continue
            value = values[row_index]
            numeric = float("nan") if value is None else float(value)
            # 新一期该指标为空时必须覆盖旧值为 NaN, 避免同一行混用两期报告。
            raw[start:, column_index] = numeric if np.isfinite(numeric) else np.nan

        if spec["price_ratio"]:
            with np.errstate(divide="ignore", invalid="ignore"):
                matrix = (market.close / raw).astype(np.float32)
            matrix[~(raw > 0)] = np.nan
            matrix[np.isinf(matrix)] = np.nan
        else:
            matrix = raw
        result[name] = matrix
    return result


def attach_matrix_fundamental_fields(market: Any, data_dir: Path | None, names: Any) -> Any:
    """把财务因子作为 matrix fields 附加到 (frozen) MarketDataMatrix 副本。"""
    import dataclasses

    requested = [str(name) for name in names if str(name) in FUNDAMENTAL_FACTOR_NAMES]
    if not requested:
        return market
    snapshot = load_fundamental_snapshot(data_dir)
    extra = build_fundamental_matrices(market, snapshot, requested)
    if not extra:
        return market
    merged = {**dict(market.fields), **extra}
    for array in extra.values():
        array.flags.writeable = False
    return dataclasses.replace(market, fields=MappingProxyType(merged))
