#!/usr/bin/env python3
"""Build the experimental HS300 alternative-factor extension tables.

The research caches under ``data/research`` are deliberately not committed.
This script turns those caches into the normal ``ext_data`` contract so the
factor library, screener and strategy backtests consume the same PIT columns.

Usage (from the repository root):
    cd backend && uv run python ../scripts/build_alt_factor_data.py

Factors written include insider buy/sell events, holder concentration and
changes, major-shareholder net buying, margin changes, goodwill/R&D/capex,
equity supply, dividend growth/yield/initiation, ERP and macro spreads, and
the market emotion score.  See ``FIELDS`` for the authoritative list.

All values are keyed by (symbol, date) and only use information whose
announcement date is strictly before the signal date.  The output is local
research data; it is intentionally ignored by Git.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.factors import ext_factors
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300"
FIELDS = [
    ExtField("insider_buy_20d", "float", "董监高竞价增持事件强度(20日)"),
    ExtField("insider_net_buy_20d", "float", "董监高竞价净增持事件强度(20日)"),
    ExtField("insider_sell_20d_low", "float", "董监高竞价减持事件强度反向(20日)"),
    ExtField("holder_top10_free_float_share", "float", "前十大流通股东持股比例合计"),
    ExtField("holder_top10_share_chg_qoq", "float", "前十大流通股东持股比例环比变化"),
    ExtField("holder_top10_share_chg_yoy", "float", "前十大流通股东持股比例同比变化"),
    ExtField("holder_net_buy_ratio_90d", "float", "重要股东90日净增持比例"),
    ExtField("holder_net_buy_count_90d", "float", "重要股东90日净增持事件数"),
    ExtField("margin_chg_1m_low", "float", "两融余额月度变化反向"),
    ExtField("goodwill_ratio_low", "float", "商誉/总资产反向"),
    ExtField("intangible_ratio_low", "float", "无形资产/总资产反向"),
    ExtField("rd_intensity", "float", "研发费用/营业收入"),
    ExtField("capex_intensity_low", "float", "资本开支/营业收入反向"),
    ExtField("equity_supply_ratio_180d_low", "float", "股权融资供给180日反向"),
    ExtField("dividend_growth_ttm", "float", "现金分红TTM增长率"),
    ExtField("dividend_ttm_cash", "float", "每股现金分红TTM合计"),
    ExtField("dividend_yield", "float", "现金分红TTM收益率"),
    ExtField("dividend_initiation_flag", "float", "首次分红标记"),
    ExtField("equity_risk_premium", "float", "沪深300 ERP(盈利收益率-10年国债)"),
    ExtField("equity_risk_premium_chg3m", "float", "沪深300 ERP近3月变化"),
    ExtField("credit_spread_5y_low", "float", "AAA-国债5年信用利差反向"),
    ExtField("term_spread_10y_2y", "float", "10Y-2Y期限利差"),
    ExtField("market_emotion_score", "float", "市场情绪分数"),
]


def _calendar() -> tuple[list[date], list[str]]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("kline_daily_enriched 为空，无法建立交易日历")
    dates = (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    holder_files = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    # The holder cache is the deterministic historical-HS300 sample.  The
    # insider cache covers the whole market, so keeping its extra symbols would
    # multiply the extension table by ~8x without helping an HS300 strategy.
    symbols = {path.stem for path in holder_files}
    if not symbols:
        raise RuntimeError("研究缓存中没有可用股票代码")
    return dates, sorted(symbols)


def _calendar_frame(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    return (
        pl.DataFrame({"date": dates})
        .join(pl.DataFrame({"symbol": symbols}), how="cross")
        .sort(["symbol", "date"])
    )


def _holder_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=[
            "symbol", "period_end", "announce_date", "holder_rank", "free_float_ratio",
        ]).filter(pl.col("holder_rank") <= 10)
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    raw = raw.filter(
        pl.col("announce_date").is_not_null()
        & pl.col("period_end").is_not_null()
        & pl.col("free_float_ratio").is_not_null()
    )
    if raw.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "date": pl.Date,
            "holder_top10_free_float_share": pl.Float64,
            "holder_top10_share_chg_qoq": pl.Float64,
            "holder_top10_share_chg_yoy": pl.Float64,
        })
    grouped = (
        raw.group_by(["symbol", "period_end", "announce_date"])
        .agg(pl.col("free_float_ratio").sum().alias("holder_top10_free_float_share"))
        .sort(["symbol", "period_end"])
    )
    period_rows: list[dict] = []
    for (symbol,), part in grouped.group_by("symbol", maintain_order=True):
        rows = part.sort("period_end").to_dicts()
        for index, row in enumerate(rows):
            current = float(row["holder_top10_free_float_share"])
            qoq = None
            yoy = None
            if index > 0:
                prev = rows[index - 1]
                gap = (row["period_end"] - prev["period_end"]).days
                if 75 <= gap <= 110:
                    qoq = current - float(prev["holder_top10_free_float_share"])
            for earlier in reversed(rows[:index]):
                gap = (row["period_end"] - earlier["period_end"]).days
                if 300 <= gap <= 430:
                    yoy = current - float(earlier["holder_top10_free_float_share"])
                    break
            period_rows.append({
                "symbol": symbol,
                "effective_date": row["announce_date"] + timedelta(days=1),
                "holder_top10_free_float_share": current,
                "holder_top10_share_chg_qoq": qoq,
                "holder_top10_share_chg_yoy": yoy,
            })
    periods = (
        pl.DataFrame(period_rows, schema={
            "symbol": pl.String,
            "effective_date": pl.Date,
            "holder_top10_free_float_share": pl.Float64,
            "holder_top10_share_chg_qoq": pl.Float64,
            "holder_top10_share_chg_yoy": pl.Float64,
        })
        .sort(["symbol", "effective_date"])
        .unique(subset=["symbol", "effective_date"], keep="last")
    )
    return _calendar_frame(dates, symbols).join_asof(
        periods,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
    )


def _dividend_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r89_dividend_growth").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=["symbol", "announce_date", "cash_per10", "split_multiplier"])
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if raw.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "date": pl.Date,
            "dividend_growth_ttm": pl.Float64,
            "dividend_ttm_cash": pl.Float64,
            "dividend_initiation_flag": pl.Float64,
        })
    raw = raw.filter(pl.col("announce_date").is_not_null()).sort(["symbol", "announce_date"])
    rows: list[dict] = []
    for symbol in symbols:
        events = raw.filter(pl.col("symbol") == symbol).to_dicts()
        if not events:
            continue
        for day in dates:
            window = [
                row for row in events
                if day - timedelta(days=730) < row["announce_date"] < day
            ]
            if not window:
                continue
            current = 0.0
            previous = 0.0
            cutoff = day - timedelta(days=365)
            for index, row in enumerate(window):
                normalizer = math.prod(
                    float(later["split_multiplier"] or 1.0)
                    for later in window[index:]
                    if later["announce_date"] <= day
                )
                cash = float(row["cash_per10"] or 0.0) / 10.0 / max(normalizer, 1e-12)
                if row["announce_date"] > cutoff:
                    current += cash
                else:
                    previous += cash
            rows.append({
                "symbol": symbol,
                "date": day,
                "dividend_growth_ttm": current / previous - 1.0 if previous > 0 else None,
                "dividend_ttm_cash": current,
                "dividend_initiation_flag": 1.0 if current > 0 and previous <= 0 else 0.0,
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String,
        "date": pl.Date,
        "dividend_growth_ttm": pl.Float64,
        "dividend_ttm_cash": pl.Float64,
        "dividend_initiation_flag": pl.Float64,
    })


def _event_score(
    event_dates: list[date],
    date_values: list[int],
    *,
    window: int = 20,
) -> list[float]:
    score = [0.0] * len(date_values)
    for announce in event_dates:
        start = bisect.bisect_right(date_values, announce.toordinal())
        for offset in range(window):
            index = start + offset
            if index >= len(date_values):
                break
            score[index] += 1.0 - offset / window
    return score


def _insider_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r90_insider_trade").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=["symbol", "announce_date", "direction"])
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    raw = raw.filter(pl.col("announce_date").is_not_null())
    date_values = [day.toordinal() for day in dates]
    rows: list[dict] = []
    for symbol in symbols:
        part = raw.filter(pl.col("symbol") == symbol)
        buy_dates = sorted(part.filter(pl.col("direction") == "buy")["announce_date"].unique().to_list())
        sell_dates = sorted(part.filter(pl.col("direction") == "sell")["announce_date"].unique().to_list())
        buy_score = _event_score(buy_dates, date_values)
        sell_score = _event_score(sell_dates, date_values)
        for index, (buy, sell) in enumerate(zip(buy_score, sell_score, strict=True)):
            if buy == 0 and sell == 0:
                continue
            rows.append({
                "symbol": symbol,
                "date": dates[index],
                "insider_buy_20d": buy,
                "insider_net_buy_20d": buy - sell,
                "insider_sell_20d_low": -sell,
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String,
        "date": pl.Date,
        "insider_buy_20d": pl.Float64,
        "insider_net_buy_20d": pl.Float64,
        "insider_sell_20d_low": pl.Float64,
    })


def _holder_net_buy_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    path = DATA / "research" / "r62_holder_trade" / "raw_all.parquet"
    if not path.exists():
        raise RuntimeError("重要股东增减持缓存缺失")
    raw = pl.read_parquet(path)
    inst = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    frame = (
        raw.select([
            pl.col("代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
            pl.col("公告日").cast(pl.Date, strict=False).alias("announce_date"),
            pl.col("股东名称").cast(pl.Utf8).fill_null("UNKNOWN").alias("shareholder"),
            pl.when(pl.col("持股变动信息-增减") == "增持").then(1)
            .when(pl.col("持股变动信息-增减") == "减持").then(-1)
            .otherwise(None).alias("direction"),
            pl.col("持股变动信息-占流通股比例").cast(pl.Float64, strict=False).abs().alias("ratio_pct"),
            pl.col("持股变动信息-变动数量").cast(pl.Float64, strict=False).alias("quantity"),
        ])
        .join(inst, on="code", how="inner")
        .filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("announce_date").is_not_null()
            & pl.col("direction").is_not_null()
        )
        .unique(subset=["symbol", "announce_date", "shareholder", "direction", "ratio_pct", "quantity"], keep="last")
    )
    daily = (
        frame.group_by(["symbol", "announce_date"]).agg([
            (pl.col("ratio_pct") * pl.col("direction")).sum().alias("ratio_signed"),
            pl.col("direction").sum().alias("count_signed"),
        ]).sort(["symbol", "announce_date"])
    )
    rows: list[dict] = []
    for symbol in symbols:
        part = daily.filter(pl.col("symbol") == symbol).sort("announce_date")
        event_dates = part["announce_date"].to_list()
        ratios = part["ratio_signed"].fill_null(0.0).to_list()
        counts = part["count_signed"].fill_null(0.0).to_list()
        ratio_cum = [0.0]
        count_cum = [0.0]
        for ratio, count in zip(ratios, counts, strict=True):
            ratio_cum.append(ratio_cum[-1] + float(ratio))
            count_cum.append(count_cum[-1] + float(count))
        for day in dates:
            right = bisect.bisect_left(event_dates, day)
            left = bisect.bisect_right(event_dates, day - timedelta(days=90))
            rows.append({
                "symbol": symbol,
                "date": day,
                "holder_net_buy_ratio_90d": ratio_cum[right] - ratio_cum[left],
                "holder_net_buy_count_90d": count_cum[right] - count_cum[left],
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String,
        "date": pl.Date,
        "holder_net_buy_ratio_90d": pl.Float64,
        "holder_net_buy_count_90d": pl.Float64,
    })


def _margin_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r50_margin").glob("*.parquet"))
    frames = [pl.read_parquet(path) for path in paths]
    if not frames:
        raise RuntimeError("两融缓存缺失")
    margins = (
        pl.concat(frames, how="diagonal_relaxed")
        .group_by(["symbol", "snapshot_date"])
        .agg(pl.col("margin_balance").max().alias("margin_balance"))
        .sort(["symbol", "snapshot_date"])
        .with_columns([
            pl.col("margin_balance").shift(1).over("symbol").alias("_prev_balance"),
            pl.col("snapshot_date").shift(1).over("symbol").alias("_prev_date"),
        ])
        .with_columns((pl.col("snapshot_date") - pl.col("_prev_date")).dt.total_days().alias("_gap_days"))
        .with_columns([
            pl.when(
                pl.col("_gap_days").is_between(20, 45, closed="both")
                & (pl.col("_prev_balance") > 0)
                & (pl.col("margin_balance") > 0)
            ).then(pl.col("margin_balance") / pl.col("_prev_balance") - 1.0).otherwise(None).alias("_chg"),
            (pl.col("snapshot_date") + pl.duration(days=1)).alias("effective_date"),
        ])
        .filter(pl.col("symbol").is_in(symbols))
    )
    joined = _calendar_frame(dates, symbols).join_asof(
        margins.select(["symbol", "effective_date", "_chg"]).sort(["symbol", "effective_date"]),
        left_on="date", right_on="effective_date", by="symbol", strategy="backward",
    )
    return joined.with_columns(
        (-pl.col("_chg")).alias("margin_chg_1m_low")
    ).select(["symbol", "date", "margin_chg_1m_low"])


def _goodwill_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r86_intangible").glob("*.parquet"))
    frames = [pl.read_parquet(path) for path in paths]
    if not frames:
        raise RuntimeError("研发/商誉缓存缺失")
    raw = pl.concat(frames, how="diagonal_relaxed").filter(
        pl.col("symbol").is_in(symbols) & pl.col("notice_date").is_not_null()
    )
    values = raw.with_columns([
        (pl.col("notice_date") + pl.duration(days=1)).alias("effective_date"),
        pl.when((pl.col("total_assets") > 0) & pl.col("goodwill").is_not_null())
        .then(-pl.col("goodwill") / pl.col("total_assets")).otherwise(None).alias("goodwill_ratio_low"),
        pl.when((pl.col("total_assets") > 0) & pl.col("intangible_asset").is_not_null())
        .then(-pl.col("intangible_asset") / pl.col("total_assets")).otherwise(None).alias("intangible_ratio_low"),
        pl.when((pl.col("revenue") > 0) & pl.col("rd_expense").is_not_null())
        .then(pl.col("rd_expense") / pl.col("revenue")).otherwise(None).alias("rd_intensity"),
        pl.when((pl.col("revenue") > 0) & pl.col("capex").is_not_null())
        .then(-pl.col("capex") / pl.col("revenue")).otherwise(None).alias("capex_intensity_low"),
    ]).sort(["symbol", "effective_date"])
    return _calendar_frame(dates, symbols).join_asof(
        values.select([
            "symbol", "effective_date", "goodwill_ratio_low", "intangible_ratio_low",
            "rd_intensity", "capex_intensity_low",
        ]),
        left_on="date", right_on="effective_date", by="symbol", strategy="backward",
    )


def _equity_supply_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    keywords = ("增发新股上市", "配股上市", "可转债转股")
    paths = sorted((DATA / "research" / "r68_equity_supply").glob("*.parquet"))
    frames = []
    for path in paths:
        raw = pl.read_parquet(path)
        if "公告日期" not in raw.columns:
            continue
        frames.append(raw.with_columns(pl.lit(path.stem).alias("symbol")))
    if not frames:
        raise RuntimeError("股权融资供给缓存缺失")
    raw = pl.concat(frames, how="diagonal_relaxed")
    required = {"symbol", "公告日期", "变动日期", "变动原因", "总股本"}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"股权融资供给字段缺失: {sorted(required - set(raw.columns))}")
    frame = (
        raw.select([
            "symbol",
            pl.col("公告日期").cast(pl.Date, strict=False).alias("announce_date"),
            pl.col("变动日期").cast(pl.Date, strict=False).alias("change_date"),
            pl.col("变动原因").cast(pl.Utf8).alias("reason"),
            pl.col("总股本").cast(pl.Float64, strict=False).alias("total_shares"),
        ])
        .filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("announce_date").is_not_null()
            & pl.col("reason").is_not_null()
            & pl.col("total_shares").is_not_null()
            & (pl.col("total_shares") > 0)
        )
        .unique(subset=["symbol", "announce_date", "change_date", "reason", "total_shares"], keep="last")
        .sort(["symbol", "announce_date", "change_date", "reason"])
    )
    frame = frame.with_columns(pl.col("total_shares").shift(1).over("symbol").alias("_prev_total"))
    frame = frame.with_columns((pl.col("total_shares") / pl.col("_prev_total") - 1.0).alias("_dilution"))
    issuance = frame.filter(
        pl.col("_dilution").is_not_null()
        & (pl.col("_dilution") > 0)
        & pl.col("reason").str.contains("|".join(keywords))
    )
    daily = issuance.group_by(["symbol", "announce_date"]).agg([
        ((pl.col("_dilution") + 1.0).product() - 1.0).alias("dilution"),
        pl.len().alias("events"),
    ]).sort(["symbol", "announce_date"])
    rows: list[dict] = []
    for symbol in symbols:
        part = daily.filter(pl.col("symbol") == symbol).sort("announce_date")
        event_dates = part["announce_date"].to_list()
        ratios = part["dilution"].fill_null(0.0).to_list()
        counts = part["events"].fill_null(0).to_list()
        log_cum = [0.0]
        count_cum = [0.0]
        for ratio, count in zip(ratios, counts, strict=True):
            log_cum.append(log_cum[-1] + math.log1p(max(float(ratio), -0.999999)))
            count_cum.append(count_cum[-1] + float(count))
        for day in dates:
            right = bisect.bisect_left(event_dates, day)
            left = bisect.bisect_right(event_dates, day - timedelta(days=180))
            ratio = math.expm1(log_cum[right] - log_cum[left])
            rows.append({
                "symbol": symbol,
                "date": day,
                "equity_supply_ratio_180d_low": -ratio,
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date, "equity_supply_ratio_180d_low": pl.Float64,
    })


def _macro_daily(dates: list[date]) -> pl.DataFrame:
    files = sorted((DATA / "research" / "r92_credit_spread").glob("*.parquet"))
    credit_frames = [pl.read_parquet(path) for path in files]
    if not credit_frames:
        raise RuntimeError("信用利差缓存缺失")
    credit = pl.concat(credit_frames, how="diagonal_relaxed")
    aaa = (
        credit.filter(pl.col("曲线名称").str.contains("中短期票据"))
        .select([pl.col("日期").alias("date"), pl.col("5年").cast(pl.Float64).alias("aaa_5y")])
        .sort("date")
    )
    treasury = (
        credit.filter(pl.col("曲线名称").str.contains("国债"))
        .select([pl.col("日期").alias("date"), pl.col("5年").cast(pl.Float64).alias("treasury_5y")])
        .sort("date")
    )
    credit_daily = (
        aaa.join(treasury, on="date", how="inner")
        .with_columns((-(pl.col("aaa_5y") - pl.col("treasury_5y"))).alias("credit_spread_5y_low"))
        .select(["date", "credit_spread_5y_low"])
    )
    bond = (
        pl.read_parquet(DATA / "research" / "macro" / "bond_10y.parquet")
        .select([
            pl.col("日期").alias("date"),
            pl.col("中国国债收益率10年").cast(pl.Float64).alias("y10"),
            pl.col("中国国债收益率2年").cast(pl.Float64).alias("y2"),
        ])
        .filter(pl.col("y10").is_not_null() & pl.col("y2").is_not_null())
        .with_columns((pl.col("y10") - pl.col("y2")).alias("term_spread_10y_2y"))
        .select(["date", "term_spread_10y_2y"])
        .sort("date")
    )
    base = pl.DataFrame({"date": dates}).sort("date")
    return (
        base.join_asof(credit_daily, on="date", strategy="backward")
        .join_asof(bond, on="date", strategy="backward")
    )


def _emotion_daily(dates: list[date]) -> pl.DataFrame:
    path = DATA / "regime_history" / "part.parquet"
    if not path.exists():
        raise RuntimeError("regime_history 缺失")
    raw = pl.read_parquet(path).select([
        pl.col("date"),
        pl.col("score").cast(pl.Float64).alias("market_emotion_score"),
    ]).unique(subset=["date"], keep="last").sort("date")
    return pl.DataFrame({"date": dates}).sort("date").join_asof(raw, on="date", strategy="backward")


def _erp_daily(dates: list[date]) -> pl.DataFrame:
    pe_path = DATA / "research" / "r93_erp" / "hs300_pe.parquet"
    bond_path = DATA / "research" / "macro" / "bond_10y.parquet"
    if not pe_path.exists() or not bond_path.exists():
        raise RuntimeError("ERP 研究缓存缺失: r93_erp/hs300_pe.parquet 或 macro/bond_10y.parquet")
    pe = (
        pl.read_parquet(pe_path)
        .select([
            pl.col("日期").alias("date"),
            pl.col("滚动市盈率").cast(pl.Float64).alias("pe_ttm"),
        ])
        .filter(pl.col("pe_ttm") > 0)
        .sort("date")
    )
    bond = (
        pl.read_parquet(bond_path)
        .select([
            pl.col("日期").alias("date"),
            pl.col("中国国债收益率10年").cast(pl.Float64).alias("bond_10y"),
        ])
        .filter(pl.col("bond_10y").is_not_null())
        .sort("date")
    )
    base = pl.DataFrame({"date": dates}).sort("date")
    daily = (
        base.join_asof(pe, on="date", strategy="backward")
        .join_asof(bond, on="date", strategy="backward")
        .with_columns((100.0 / pl.col("pe_ttm") - pl.col("bond_10y")).alias("equity_risk_premium"))
        .with_columns(
            (pl.col("equity_risk_premium") - pl.col("equity_risk_premium").shift(63)).alias(
                "equity_risk_premium_chg3m"
            )
        )
        .select(["date", "equity_risk_premium", "equity_risk_premium_chg3m"])
    )
    return daily


def _prices(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_in(dates))
        .select(["symbol", "date", "raw_close"])
        .collect()
    )


def _write_partitions(frame: pl.DataFrame) -> int:
    out_root = DATA / "ext_data" / CONFIG_ID / "timeseries"
    if out_root.exists():
        import shutil
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for part in frame.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
        day = part["date"][0]
        target = out_root / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(part.drop("date"), target)
        count += 1
    return count


def main() -> int:
    dates, symbols = _calendar()
    print(f"calendar={len(dates)} symbols={len(symbols)}", flush=True)

    holder = _holder_daily(dates, symbols)
    dividend = _dividend_daily(dates, symbols)
    insider = _insider_daily(dates, symbols)
    holder_trade = _holder_net_buy_daily(dates, symbols)
    margin = _margin_daily(dates, symbols)
    goodwill = _goodwill_daily(dates, symbols)
    equity_supply = _equity_supply_daily(dates, symbols)
    erp = _erp_daily(dates)
    macro = _macro_daily(dates)
    emotion = _emotion_daily(dates)
    prices = _prices(dates, symbols)

    base = _calendar_frame(dates, symbols)
    for frame in (holder, dividend, insider, holder_trade, margin, goodwill, equity_supply, prices):
        base = base.join(frame, on=["symbol", "date"], how="left")
    for frame in (erp, macro, emotion):
        base = base.join(frame, on="date", how="left")
    base = base.with_columns(
        pl.when((pl.col("raw_close") > 0) & pl.col("dividend_ttm_cash").is_not_null())
        .then(pl.col("dividend_ttm_cash") / pl.col("raw_close"))
        .otherwise(None)
        .alias("dividend_yield")
    ).drop("raw_close")
    for field in FIELDS:
        if field.name not in base.columns:
            base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias(field.name))
    base = base.select(["symbol", "date", *[field.name for field in FIELDS]])

    cfg = ExtConfig(
        id=CONFIG_ID,
        label="HS300另类因子",
        mode="timeseries",
        fields=FIELDS,
        description=(
            "研究实验数据: 增持/减持事件、股东结构变化、两融、商誉与研发、"
            "股权融资供给、分红、ERP、信用/期限利差和市场情绪。"
            "仅用于实验策略，不构成可部署结论。"
        ),
    )
    ExtConfigStore(DATA).upsert(cfg)
    partitions = _write_partitions(base)
    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    print(
        f"wrote rows={base.height} partitions={partitions} "
        f"factors={[spec.id for spec in ext_factors.ext_factor_specs(DATA)]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
