#!/usr/bin/env python3
"""Build the experimental HS300 alternative-factor extension tables.

The research caches under ``data/research`` are deliberately not committed.
This script turns those caches into the normal ``ext_data`` contract so the
factor library, screener and strategy backtests consume the same PIT columns.

Usage (from the repository root):
    cd backend && uv run python ../scripts/build_alt_factor_data.py

Factors written include insider buy/sell events, holder concentration and
changes, major-shareholder net buying, fund-holding crowding, margin changes,
goodwill/R&D/capex, equity supply, dividend growth/yield/initiation, ERP and
macro spreads, and the market emotion score.  See ``FIELDS`` for the
authoritative list.

All values are keyed by (symbol, date) and only use information whose
announcement date is strictly before the signal date.  The output is local
research data; it is intentionally ignored by Git.
"""
from __future__ import annotations

import bisect
import json
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
    ExtField("fund_count_low", "float", "基金持仓家数反向(低拥挤)"),
    ExtField("fund_count_chg_qoq_low", "float", "基金持仓家数季度变化反向"),
    ExtField("fund_holding_ratio_low", "float", "基金持股占流通股比例反向"),
    ExtField("fund_holding_ratio_chg_qoq_low", "float", "基金持股比例季度变化反向"),
    ExtField("fund_holding_value_chg_qoq_low", "float", "基金持股市值季度变化反向"),
    ExtField("lhb_net_buy_ratio_20d", "float", "龙虎榜净买额占市场成交额20日衰减"),
    ExtField("lhb_inst_net_buy_ratio_20d", "float", "龙虎榜机构净买额占市场成交额20日衰减"),
    ExtField("lhb_event_count_20d_low", "float", "龙虎榜上榜次数20日衰减反向"),
    ExtField("pledge_ratio_low", "float", "股权质押比例反向"),
    ExtField("pledge_ratio_chg_1m_low", "float", "股权质押比例月度变化反向"),
    ExtField("repurchase_event_60d", "float", "回购计划公告60日衰减"),
    ExtField("block_trade_ratio_20d_low", "float", "大宗交易成交额占流通市值20日衰减反向"),
    ExtField("block_premium_20d", "float", "大宗交易成交额加权折溢率20日衰减"),
    ExtField("analyst_rating_score_20d", "float", "分析师评级20日滚动水平"),
    ExtField("analyst_rating_change_20d", "float", "分析师评级调整20日衰减"),
    ExtField("forecast_surprise_60d", "float", "业绩预告预期差60日衰减"),
    ExtField("express_surprise_60d", "float", "业绩快报净利润同比预期差60日衰减"),
    ExtField("industry_mom_20d", "float", "一级行业20日等权动量"),
    ExtField("industry_mom_60d", "float", "一级行业60日等权动量"),
    ExtField("industry_rps_20d", "float", "一级行业20日动量截面分位"),
    ExtField("industry_breadth_20d", "float", "一级行业站上MA20比例"),
    ExtField("stock_rel_industry_20d", "float", "个股相对行业20日强弱"),
    ExtField("hs300_stock_rel_industry_top3_flag", "float", "HS300 top-3 relative industry strength flag"),
    ExtField("mainline_top1_flag", "float", "所属行业是否为当日主线第一"),
    ExtField("mainline_strength", "float", "主线行业涨停强度"),
    ExtField("mainline_leader_flag", "float", "是否为当日主线龙头"),
    ExtField("etf_share_chg_1m", "float", "510300份额月度变化"),
    ExtField("etf_share_chg_3m", "float", "510300份额三月变化"),
    ExtField("qvix_z_252", "float", "300ETF QVIX 252日z-score"),
    ExtField("m1_m2_scissors", "float", "M1-M2同比剪刀差"),
    ExtField("m1_m2_scissors_chg_1m", "float", "M1-M2剪刀差月度变化"),
    ExtField("m2_yoy", "float", "M2同比"),
    ExtField("shibor_3m", "float", "SHIBOR 3M"),
    ExtField("shibor_3m_chg_20d", "float", "SHIBOR 3M 20日变化"),
    ExtField("pmi", "float", "制造业PMI"),
    ExtField("cpi_yoy", "float", "CPI同比"),
    ExtField("ppi_yoy", "float", "PPI同比"),
    ExtField("market_breadth_above_ma20", "float", "市场站上MA20比例"),
    ExtField("market_profit_score", "float", "市场赚钱效应分数"),
    ExtField("market_trend_score", "float", "市场趋势分数"),
    ExtField("market_speculation_score", "float", "市场投机分数"),
    ExtField("northbound_net_buy_20d", "float", "北向20日净买额"),
    ExtField("margin_chg_1m_low", "float", "两融余额月度变化反向"),
    ExtField("short_ratio_low", "float", "融券余量占流通股本比例反向(低空头拥挤)"),
    ExtField("if_net_short_chg_1m", "float", "IF前20席位净空头占比月度变化(方向负)"),
    ExtField("bond_mom_20d", "float", "T0(10Y国债期货)20日动量(方向负)"),
    ExtField("bond_tf_mom_20d", "float", "TF0(5Y国债期货)20日动量(方向负)"),
    ExtField("ah_premium_z", "float", "A-H溢价20日中位数扩张z-score(方向负)"),
    ExtField("ah_premium_chg_1m", "float", "A-H溢价20日中位数月度变化(方向负)"),
    ExtField("hsi_mom_20d", "float", "恒生指数20日动量(方向正)"),
    ExtField("hsciei_mom_20d", "float", "恒生国企指数20日动量(方向正)"),
    ExtField("hsi_rel_hs300_20d", "float", "恒指20日动量减沪深30020日动量(方向正)"),
    ExtField("margin_safety_z", "float", "两融维持担保比例扩张z-score(方向正)"),
    ExtField("margin_safety_chg_20d", "float", "两融维持担保比例20日变化(方向正)"),
    ExtField("nh_nl_diff_z", "float", "HS300新高减新低比例扩张z-score(方向正)"),
    ExtField("nh_nl_diff_20d", "float", "HS300新高减新低比例20日滚动均值(方向正)"),
    ExtField("shibor_tightness_z", "float", "SHIBOR 3M扩张z-score(方向负)"),
    ExtField("turnover_hhi_z", "float", "HS300成交额HHI扩张z-score(方向负)"),
    ExtField("top30_amount_share_z", "float", "HS300前30成交额占比扩张z-score(方向负)"),
    ExtField("hs300_turnover_z", "float", "HS300换手率20日均值扩张z-score(方向负)"),
    ExtField("hs300_turnover_chg_20d", "float", "HS300换手率20日均值20日变化(方向负)"),
    ExtField("hs300_turnover_z_ext2017", "float", "HS300 turnover 20d expanding z-score with 2017 history (direction negative)"),
    ExtField("hs300_turnover_chg_20d_ext2017", "float", "HS300 turnover 20d mean 20d change with 2017 history (direction negative)"),
    ExtField("hs300_turnover_gate_flag_ext2017", "float", "HS300 turnover 20d expanding z-score < 0 gate flag with 2017 history"),
    ExtField("idio_vol_60d_low", "float", "HS300市场模型60日残差波动率反向"),
    ExtField("price_delay_60d", "float", "HS300市场信息60日价格延迟"),
    ExtField("cs_spread_60d", "float", "Corwin-Schultz高低价估计买卖价差60日均值"),
    ExtField("vol_of_vol_60d_low", "float", "20日波动率的60日稳定性反向"),
    ExtField("market_down_beta_60d_low", "float", "HS300市场下跌日条件beta反向"),
    ExtField("beta_asymmetry_120d", "float", "HS300上行beta减下行beta（高好）"),
    ExtField("trend_efficiency_60d", "float", "60日趋势效率（净对数收益/绝对路径和）"),
    ExtField("risk_adjusted_momentum_60d", "float", "60日风险调整动量（收益/年化波动率）"),
    ExtField("market_skew_60d_low", "float", "HS300 60日已实现偏度反向"),
    ExtField("cash_flow_stability_4q_low", "float", "经营现金流/总资产4季稳定性反向"),
    ExtField("net_financing_4q_low", "float", "净筹资现金流/总资产4季强度反向"),
    ExtField("sga_ratio_4q_low", "float", "销售+管理费用/营收4季费用率反向"),
    ExtField("sga_ratio_4q_qoq_improvement", "float", "销售+管理费用率TTM环比改善(高好)"),
    ExtField("management_expense_ratio_4q_low", "float", "管理费用/营收4季费用率反向"),
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


def _holder_paths() -> list[Path]:
    r87 = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    r163 = sorted((DATA / "research" / "r163_holder_batch").glob("*.parquet"))
    return [*r87, *r163]


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
    r87 = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    r163 = sorted((DATA / "research" / "r163_holder_batch").glob("*.parquet"))
    # The holder caches define the deterministic historical-HS300 sample. The
    # insider cache covers the whole market, so keeping its extra symbols would
    # multiply the extension table by ~8x without helping an HS300 strategy.
    symbols = {path.stem for path in r87}
    for path in r163:
        symbols.update(pl.read_parquet(path, columns=["symbol"])["symbol"].unique().to_list())
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
    paths = _holder_paths()
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
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )
    return _calendar_frame(dates, symbols).join_asof(
        periods,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
    )


def _fund_holding_frame(
    raw: pl.DataFrame,
    dates: list[date],
    symbols: list[str],
    instruments: pl.DataFrame,
) -> pl.DataFrame:
    """基金季报持仓 → 保守滞后 45 天可得的高方向因子。

    季末数据在 ``quarter_date + 45d`` 前一律不可见。基金家数、持股比例和
    持股市值变化都转成“低拥挤/减仓更好”的高方向列，供评分统一按降序使用。
    历史流通股本仍使用当前维表，这是本实验因子的已知口径限制。
    """
    factor_schema = {
        "symbol": pl.String,
        "available_date": pl.Date,
        "fund_count_low": pl.Float64,
        "fund_count_chg_qoq_low": pl.Float64,
        "fund_holding_ratio_low": pl.Float64,
        "fund_holding_ratio_chg_qoq_low": pl.Float64,
        "fund_holding_value_chg_qoq_low": pl.Float64,
    }
    empty_periods = pl.DataFrame(schema=factor_schema)
    if raw.is_empty():
        periods = empty_periods
    else:
        required = {
            "symbol", "quarter_date", "fund_count", "holding_shares", "holding_value",
        }
        missing = required - set(raw.columns)
        if missing:
            raise ValueError(f"基金持仓缓存缺少字段: {sorted(missing)}")
        shares = instruments.select(["symbol", "float_shares"]).unique(subset=["symbol"])
        periods = (
            raw.select([
                pl.col("symbol").cast(pl.String),
                pl.col("quarter_date").cast(pl.Date),
                pl.col("fund_count").cast(pl.Float64, strict=False),
                pl.col("holding_shares").cast(pl.Float64, strict=False),
                pl.col("holding_value").cast(pl.Float64, strict=False),
            ])
            .filter(
                pl.col("symbol").is_in(symbols)
                & pl.col("quarter_date").is_not_null()
                & (pl.col("holding_shares") > 0)
            )
            .join(shares, on="symbol", how="left")
            .sort(["symbol", "quarter_date"])
            .with_columns([
                pl.col("fund_count").shift(1).over("symbol").alias("_prev_count"),
                pl.col("holding_shares").shift(1).over("symbol").alias("_prev_shares"),
                pl.col("holding_value").shift(1).over("symbol").alias("_prev_value"),
                (pl.col("quarter_date") + pl.duration(days=45)).alias("available_date"),
            ])
            .with_columns(
                pl.when((pl.col("float_shares") > 0) & pl.col("holding_shares").is_not_null())
                .then(pl.col("holding_shares") / pl.col("float_shares"))
                .otherwise(None)
                .alias("_holding_ratio")
            )
            .with_columns(
                pl.col("_holding_ratio").shift(1).over("symbol").alias("_prev_ratio")
            )
            .with_columns([
                (-(pl.col("fund_count") + 1).log()).alias("fund_count_low"),
                pl.when((pl.col("_prev_count") > 0) & (pl.col("fund_count") > 0))
                .then(-(pl.col("fund_count") / pl.col("_prev_count") - 1.0))
                .otherwise(None)
                .alias("fund_count_chg_qoq_low"),
                (-pl.col("_holding_ratio")).alias("fund_holding_ratio_low"),
                pl.when((pl.col("_prev_ratio") > 0) & (pl.col("_holding_ratio") >= 0))
                .then(-(pl.col("_holding_ratio") / pl.col("_prev_ratio") - 1.0))
                .otherwise(None)
                .alias("fund_holding_ratio_chg_qoq_low"),
                pl.when((pl.col("_prev_value") > 0) & (pl.col("holding_value") >= 0))
                .then(-(pl.col("holding_value") / pl.col("_prev_value") - 1.0))
                .otherwise(None)
                .alias("fund_holding_value_chg_qoq_low"),
            ])
            .select(list(factor_schema))
            .unique(subset=["symbol", "available_date"], keep="last")
            .sort(["symbol", "available_date"])
        )
    return _calendar_frame(dates, symbols).join_asof(
        periods,
        left_on="date",
        right_on="available_date",
        by="symbol",
        strategy="backward",
    ).drop("available_date")


def _fund_holding_daily(
    dates: list[date], symbols: list[str], instruments: pl.DataFrame
) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r60_fund_hold").glob("*.parquet"))
    if not paths:
        return _fund_holding_frame(pl.DataFrame(), dates, symbols, instruments)
    raw = pl.concat(
        [
            pl.read_parquet(
                path,
                columns=[
                    "symbol", "quarter_date", "fund_count",
                    "holding_shares", "holding_value",
                ],
            )
            for path in paths
        ],
        how="diagonal_relaxed",
    )
    return _fund_holding_frame(raw, dates, symbols, instruments)


def _stock_code_map(instruments: pl.DataFrame) -> pl.DataFrame:
    return instruments.select([
        pl.col("symbol").cast(pl.String),
        pl.col("code").cast(pl.String).str.zfill(6).alias("_code6"),
    ]).unique(subset=["_code6"], keep="last")


def _lhb_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    detail_paths = sorted((DATA / "research" / "r43_lhb").glob("detail_*.parquet"))
    inst_paths = sorted((DATA / "research" / "r43_lhb").glob("jgmm_*.parquet"))
    if not detail_paths and not inst_paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "lhb_net_buy_ratio_20d": pl.Float64,
            "lhb_inst_net_buy_ratio_20d": pl.Float64,
            "lhb_event_count_20d_low": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    details = pl.concat(
        [pl.read_parquet(path) for path in detail_paths], how="diagonal_relaxed"
    ) if detail_paths else pl.DataFrame()
    if not details.is_empty():
        details = (
            details.select([
                pl.col("代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
                pl.col("上榜日").cast(pl.Date, strict=False).alias("date"),
                pl.col("龙虎榜净买额").cast(pl.Float64, strict=False).fill_null(0.0),
                pl.col("市场总成交额").cast(pl.Float64, strict=False).fill_null(0.0),
            ])
            .join(code_map, on="_code6", how="inner")
            .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
            .group_by(["symbol", "date"]).agg([
                pl.col("龙虎榜净买额").sum().alias("_net_buy"),
                pl.col("市场总成交额").max().alias("_market_turnover"),
                pl.len().alias("_event_count"),
            ])
            .with_columns(
                pl.when(pl.col("_market_turnover") > 0)
                .then(pl.col("_net_buy") / pl.col("_market_turnover") * 100.0)
                .otherwise(0.0)
                .alias("_net_buy_ratio")
            )
        )
    else:
        details = pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "_net_buy_ratio": pl.Float64, "_event_count": pl.UInt32,
        })
    inst = pl.concat(
        [pl.read_parquet(path) for path in inst_paths], how="diagonal_relaxed"
    ) if inst_paths else pl.DataFrame()
    if not inst.is_empty():
        inst = (
            inst.select([
                pl.col("代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
                pl.col("上榜日期").cast(pl.Date, strict=False).alias("date"),
                pl.col("机构买入净额").cast(pl.Float64, strict=False).fill_null(0.0),
                pl.col("市场总成交额").cast(pl.Float64, strict=False).fill_null(0.0),
            ])
            .join(code_map, on="_code6", how="inner")
            .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
            .group_by(["symbol", "date"]).agg([
                pl.col("机构买入净额").sum().alias("_inst_net_buy"),
                pl.col("市场总成交额").max().alias("_market_turnover"),
            ])
            .with_columns(
                pl.when(pl.col("_market_turnover") > 0)
                .then(pl.col("_inst_net_buy") / pl.col("_market_turnover") * 100.0)
                .otherwise(0.0)
                .alias("_inst_net_buy_ratio")
            )
            .select(["symbol", "date", "_inst_net_buy_ratio"])
        )
    else:
        inst = pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date, "_inst_net_buy_ratio": pl.Float64,
        })
    merged = details.join(inst, on=["symbol", "date"], how="full", coalesce=True).sort(
        ["symbol", "date"]
    )
    date_values = [day.toordinal() for day in dates]
    rows: list[dict] = []
    for symbol in symbols:
        part = merged.filter(pl.col("symbol") == symbol)
        if part.is_empty():
            continue
        event_dates = part["date"].to_list()
        net_score = _decayed_event_score(
            event_dates,
            part["_net_buy_ratio"].fill_null(0.0).to_list(),
            date_values,
            window=20,
        )
        inst_score = _decayed_event_score(
            event_dates,
            part["_inst_net_buy_ratio"].fill_null(0.0).to_list(),
            date_values,
            window=20,
        )
        count_score = _decayed_event_score(
            event_dates,
            part["_event_count"].fill_null(0).cast(pl.Float64).to_list(),
            date_values,
            window=20,
        )
        for index, day in enumerate(dates):
            if net_score[index] == 0 and inst_score[index] == 0 and count_score[index] == 0:
                continue
            rows.append({
                "symbol": symbol,
                "date": day,
                "lhb_net_buy_ratio_20d": net_score[index],
                "lhb_inst_net_buy_ratio_20d": inst_score[index],
                "lhb_event_count_20d_low": -count_score[index],
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date,
        "lhb_net_buy_ratio_20d": pl.Float64,
        "lhb_inst_net_buy_ratio_20d": pl.Float64,
        "lhb_event_count_20d_low": pl.Float64,
    })


def _pledge_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r65_pledge").glob("20*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "pledge_ratio_low": pl.Float64,
            "pledge_ratio_chg_1m_low": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    raw = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
    snapshots = (
        raw.select([
            pl.col("股票代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
            pl.col("交易日期").cast(pl.Date, strict=False).alias("snapshot_date"),
            pl.col("质押比例").cast(pl.Float64, strict=False).alias("_pledge_ratio"),
        ])
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("snapshot_date").is_not_null())
        .sort(["symbol", "snapshot_date"])
        .with_columns([
            pl.col("_pledge_ratio").shift(1).over("symbol").alias("_prev_ratio"),
            (pl.col("snapshot_date") + pl.duration(days=1)).alias("effective_date"),
        ])
        .with_columns(
            pl.when(pl.col("_prev_ratio").is_not_null())
            .then(pl.col("_pledge_ratio") - pl.col("_prev_ratio"))
            .otherwise(None)
            .alias("_pledge_chg")
        )
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )
    joined = _calendar_frame(dates, symbols).join_asof(
        snapshots.select([
            "symbol", "effective_date", "snapshot_date", "_pledge_ratio", "_pledge_chg",
        ]),
        left_on="date", right_on="effective_date", by="symbol", strategy="backward",
        check_sortedness=False,
    )
    age = (pl.col("date") - pl.col("snapshot_date")).dt.total_days()
    return joined.with_columns([
        pl.when(age.is_between(1, 14, closed="both"))
        .then(-pl.col("_pledge_ratio"))
        .otherwise(None)
        .alias("pledge_ratio_low"),
        pl.when(age.is_between(1, 14, closed="both"))
        .then(-pl.col("_pledge_chg"))
        .otherwise(None)
        .alias("pledge_ratio_chg_1m_low"),
    ]).select(["symbol", "date", "pledge_ratio_low", "pledge_ratio_chg_1m_low"])


def _event_only_daily(
    dates: list[date],
    symbols: list[str],
    events: pl.DataFrame,
    *,
    value_column: str,
    output_column: str,
    window: int,
) -> pl.DataFrame:
    date_values = [day.toordinal() for day in dates]
    rows: list[dict] = []
    for symbol in symbols:
        part = events.filter(pl.col("symbol") == symbol).sort("date")
        if part.is_empty():
            continue
        score = _decayed_event_score(
            part["date"].to_list(),
            part[value_column].fill_null(0.0).to_list(),
            date_values,
            window=window,
        )
        for index, day in enumerate(dates):
            if score[index] == 0:
                continue
            rows.append({"symbol": symbol, "date": day, output_column: score[index]})
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date, output_column: pl.Float64,
    })


def _repurchase_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    path = DATA / "research" / "external_r39" / "repurchase.parquet"
    if not path.exists():
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date, "repurchase_event_60d": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    raw = (
        pl.read_parquet(path)
        .select([
            pl.col("股票代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
            pl.col("最新公告日期").cast(pl.Date, strict=False).alias("date"),
        ])
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"]).agg(pl.lit(1.0).alias("_event"))
        .sort(["symbol", "date"])
    )
    return _event_only_daily(
        dates, symbols, raw, value_column="_event",
        output_column="repurchase_event_60d", window=60,
    )


def _block_trade_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "external_r39").glob("dzjy_*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "block_trade_ratio_20d_low": pl.Float64,
            "block_premium_20d": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    raw = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
    events = (
        raw.select([
            pl.col("证券代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
            pl.col("交易日期").cast(pl.Date, strict=False).alias("date"),
            pl.col("成交额").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("折溢率").cast(pl.Float64, strict=False),
            pl.col("成交额/流通市值").cast(pl.Float64, strict=False),
        ])
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"]).agg([
            pl.col("成交额/流通市值").sum().alias("_block_ratio"),
            (
                pl.when(pl.col("折溢率").is_not_null())
                .then(pl.col("成交额") * pl.col("折溢率"))
                .otherwise(0.0).sum()
                / pl.when(pl.col("折溢率").is_not_null())
                .then(pl.col("成交额"))
                .otherwise(0.0).sum()
            ).alias("_premium"),
        ])
        .sort(["symbol", "date"])
    )
    date_values = [day.toordinal() for day in dates]
    rows: list[dict] = []
    for symbol in symbols:
        part = events.filter(pl.col("symbol") == symbol)
        if part.is_empty():
            continue
        event_dates = part["date"].to_list()
        ratio_score = _decayed_event_score(
            event_dates, part["_block_ratio"].fill_null(0.0).to_list(), date_values, window=20
        )
        premium_score = _decayed_event_score(
            event_dates, part["_premium"].fill_null(0.0).to_list(), date_values, window=20
        )
        for index, day in enumerate(dates):
            if ratio_score[index] == 0 and premium_score[index] == 0:
                continue
            rows.append({
                "symbol": symbol,
                "date": day,
                "block_trade_ratio_20d_low": -ratio_score[index],
                "block_premium_20d": premium_score[index],
            })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date,
        "block_trade_ratio_20d_low": pl.Float64,
        "block_premium_20d": pl.Float64,
    })


def _analyst_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r47_analyst").glob("*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "analyst_rating_score_20d": pl.Float64,
            "analyst_rating_change_20d": pl.Float64,
        })
    raw = pl.scan_parquet([str(path) for path in paths]).select([
        pl.col("symbol").cast(pl.String),
        pl.col("report_date").cast(pl.Date, strict=False).alias("report_date"),
        pl.col("rating_score").cast(pl.Float64, strict=False),
        pl.col("rating_change_score").cast(pl.Float64, strict=False),
    ]).collect()
    daily = (
        raw.filter(pl.col("symbol").is_in(symbols) & pl.col("report_date").is_not_null())
        .group_by(["symbol", "report_date"]).agg([
            pl.col("rating_score").mean().alias("_rating"),
            pl.col("rating_change_score").mean().alias("_rating_change"),
        ])
        .with_columns((pl.col("report_date") + pl.duration(days=1)).alias("effective_date"))
        .sort(["symbol", "effective_date"])
    )
    calendar = _calendar_frame(dates, symbols)
    level = calendar.join_asof(
        daily.select(["symbol", "effective_date", "report_date", "_rating"]),
        left_on="date", right_on="effective_date", by="symbol", strategy="backward",
        check_sortedness=False,
    ).with_columns(
        pl.when((pl.col("date") - pl.col("report_date")).dt.total_days().is_between(0, 180))
        .then(pl.col("_rating"))
        .otherwise(None)
        .alias("_rating_valid")
    ).with_columns(
        pl.col("_rating_valid").rolling_mean(window_size=20, min_samples=1).over("symbol")
        .alias("analyst_rating_score_20d")
    )
    date_values = [day.toordinal() for day in dates]
    change_rows: list[dict] = []
    for symbol in symbols:
        part = daily.filter(pl.col("symbol") == symbol)
        if part.is_empty():
            continue
        score = _decayed_event_score(
            part["effective_date"].to_list(),
            part["_rating_change"].fill_null(0.0).to_list(),
            date_values,
            window=20,
        )
        first_effective = part["effective_date"].min()
        for index, day in enumerate(dates):
            if day < first_effective:
                continue
            change_rows.append({
                "symbol": symbol,
                "date": day,
                "analyst_rating_change_20d": score[index],
            })
    changes = pl.DataFrame(change_rows, schema={
        "symbol": pl.String, "date": pl.Date, "analyst_rating_change_20d": pl.Float64,
    })
    return level.select([
        "symbol", "date", "analyst_rating_score_20d",
    ]).join(changes, on=["symbol", "date"], how="left")


def _forecast_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r63_forecast").glob("*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date, "forecast_surprise_60d": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    raw = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
    events = (
        raw.select([
            pl.col("股票代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
            pl.col("公告日期").cast(pl.Date, strict=False).alias("date"),
            pl.col("预测指标").cast(pl.String),
            pl.col("业绩变动幅度").cast(pl.Float64, strict=False),
            pl.col("预告类型").cast(pl.String),
        ])
        .join(code_map, on="_code6", how="inner")
        .filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("date").is_not_null()
            & pl.col("预测指标").str.contains("净利润|归属于")
        )
        .with_columns(
            pl.when(pl.col("业绩变动幅度").is_not_null())
            .then(pl.col("业绩变动幅度") / 100.0)
            .when(pl.col("预告类型").is_in(["预增", "略增", "扭亏"]))
            .then(0.2)
            .when(pl.col("预告类型").is_in(["预减", "略减", "首亏", "续亏"]))
            .then(-0.2)
            .otherwise(0.0)
            .alias("_surprise")
        )
        .group_by(["symbol", "date"]).agg(pl.col("_surprise").max().alias("_surprise"))
        .sort(["symbol", "date"])
    )
    return _event_only_daily(
        dates, symbols, events, value_column="_surprise",
        output_column="forecast_surprise_60d", window=60,
    )


def _express_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r64_express").glob("*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date, "express_surprise_60d": pl.Float64,
        })
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    code_map = _stock_code_map(instruments)
    raw = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
    events = (
        raw.select([
            pl.col("股票代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("_code6"),
            pl.col("公告日期").cast(pl.Date, strict=False).alias("date"),
            pl.col("净利润-同比增长").cast(pl.Float64, strict=False).alias("_surprise"),
        ])
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .with_columns((pl.col("_surprise") / 100.0).alias("_surprise"))
        .group_by(["symbol", "date"]).agg(pl.col("_surprise").max().alias("_surprise"))
        .sort(["symbol", "date"])
    )
    return _event_only_daily(
        dates, symbols, events, value_column="_surprise",
        output_column="express_surprise_60d", window=60,
    )


def _hs300_membership_flags(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    """Map each symbol-date to the latest historical HS300 snapshot."""
    monthly = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
    daily = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet")
    membership = (
        pl.concat([monthly, daily], how="diagonal_relaxed")
        .select([
            pl.col("query_date").cast(pl.Date).alias("snapshot_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["snapshot_date", "symbol"], keep="last")
        .sort(["snapshot_date", "symbol"])
    )
    snapshots = membership.select("snapshot_date").unique().sort("snapshot_date")
    date_map = (
        pl.DataFrame({"date": dates})
        .sort("date")
        .join_asof(snapshots, left_on="date", right_on="snapshot_date", strategy="backward")
    )
    members = date_map.join(membership, on="snapshot_date", how="inner").select([
        "symbol", "date", pl.lit(1.0).alias("_hs300_member"),
    ])
    return _calendar_frame(dates, symbols).join(members, on=["symbol", "date"], how="left").with_columns(
        pl.col("_hs300_member").fill_null(0.0)
    )


def _industry_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(DATA)
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    panel = (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_in(dates))
        .select(["symbol", "date", "close"])
        .collect()
        .sort(["symbol", "date"])
        .with_columns([
            (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("_ret20"),
            (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("_ret60"),
            (pl.col("close") > pl.col("close").rolling_mean(20).over("symbol"))
            .cast(pl.Float64).alias("_above_ma20"),
        ])
        .join(industry, on="symbol", how="left")
    )
    grouped = (
        panel.filter(pl.col("industry_l1").is_not_null())
        .group_by(["date", "industry_l1"]).agg([
            pl.col("_ret20").mean().alias("industry_mom_20d"),
            pl.col("_ret60").mean().alias("industry_mom_60d"),
            pl.col("_above_ma20").mean().alias("industry_breadth_20d"),
            pl.len().alias("_industry_count"),
        ])
        .with_columns(
            (pl.col("industry_mom_20d").rank("ordinal").over("date") / pl.len().over("date"))
            .alias("industry_rps_20d")
        )
    )
    flags = _hs300_membership_flags(dates, symbols)
    return (
        panel.join(grouped, on=["date", "industry_l1"], how="left")
        .join(flags, on=["symbol", "date"], how="left")
        .with_columns(
            (pl.col("_ret20") - pl.col("industry_mom_20d")).alias("stock_rel_industry_20d")
        )
        .with_columns(
            pl.when(pl.col("_hs300_member") == 1)
            .then(pl.col("stock_rel_industry_20d"))
            .otherwise(None)
            .rank("ordinal", descending=True)
            .over("date")
            .alias("_stock_rel_rank")
        )
        .with_columns(
            pl.when((pl.col("_hs300_member") == 1) & (pl.col("_stock_rel_rank") <= 3))
            .then(1.0)
            .otherwise(0.0)
            .alias("hs300_stock_rel_industry_top3_flag")
        )
        .select([
            "symbol", "date", "industry_mom_20d", "industry_mom_60d",
            "industry_rps_20d", "industry_breadth_20d", "stock_rel_industry_20d",
            "hs300_stock_rel_industry_top3_flag",
        ])
    )


def _mainline_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    from app.services.industry_map import load_industry_l1

    path = DATA / "mainline_history" / "part.parquet"
    if not path.exists():
        return pl.DataFrame(schema={
            "symbol": pl.String, "date": pl.Date,
            "mainline_top1_flag": pl.Float64,
            "mainline_strength": pl.Float64,
            "mainline_leader_flag": pl.Float64,
        })
    industry = load_industry_l1(DATA)
    mainline = (
        pl.read_parquet(path)
        .filter((pl.col("kind") == "industry") & (pl.col("rank") == 1))
        .select([
            pl.col("date").cast(pl.Date),
            pl.col("member").cast(pl.String).str.split("-").list.first().alias("industry_l1"),
            (pl.col("limit_up_count").cast(pl.Float64) + pl.col("rungs_filled").cast(pl.Float64))
            .alias("mainline_strength"),
            pl.col("leader_symbol").cast(pl.String),
            pl.lit(True).alias("_matched"),
        ])
        .unique(subset=["date"], keep="last")
    )
    base = _calendar_frame(dates, symbols).join(industry, on="symbol", how="left")
    joined = base.join(mainline, on=["date", "industry_l1"], how="left")
    return joined.select([
        "symbol", "date",
        pl.when(pl.col("_matched").is_not_null()).then(1.0).otherwise(0.0)
        .alias("mainline_top1_flag"),
        pl.col("mainline_strength").fill_null(0.0),
        pl.when(pl.col("symbol") == pl.col("leader_symbol")).then(1.0).otherwise(0.0)
        .alias("mainline_leader_flag"),
    ])


def _etf_flow_daily(dates: list[date]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r74_etf_flow").glob("*.parquet"))
    if not paths:
        return pl.DataFrame(schema={
            "date": pl.Date, "etf_share_chg_1m": pl.Float64, "etf_share_chg_3m": pl.Float64,
        })
    raw = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
    shares = (
        raw.filter(pl.col("基金代码").cast(pl.String) == "510300")
        .select([
            pl.col("统计日期").cast(pl.Date, strict=False).alias("snapshot_date"),
            pl.col("基金份额").cast(pl.Float64, strict=False).alias("_shares"),
        ])
        .filter(pl.col("snapshot_date").is_not_null() & (pl.col("_shares") > 0))
        .unique(subset=["snapshot_date"], keep="last")
        .sort("snapshot_date")
        .with_columns([
            (pl.col("_shares") / pl.col("_shares").shift(1) - 1.0).alias("etf_share_chg_1m"),
            (pl.col("_shares") / pl.col("_shares").shift(3) - 1.0).alias("etf_share_chg_3m"),
            (pl.col("snapshot_date") + pl.duration(days=1)).alias("effective_date"),
        ])
    )
    joined = pl.DataFrame({"date": dates}).sort("date").join_asof(
        shares.select(["effective_date", "etf_share_chg_1m", "etf_share_chg_3m"]),
        left_on="date", right_on="effective_date", strategy="backward",
        check_sortedness=False,
    )
    age = (pl.col("date") - pl.col("effective_date")).dt.total_days()
    return joined.with_columns([
        pl.when(age <= 45).then(pl.col("etf_share_chg_1m")).otherwise(None)
        .alias("etf_share_chg_1m"),
        pl.when(age <= 45).then(pl.col("etf_share_chg_3m")).otherwise(None)
        .alias("etf_share_chg_3m"),
    ]).drop("effective_date")


def _qvix_daily(dates: list[date]) -> pl.DataFrame:
    path = DATA / "research" / "r75_qvix" / "qvix_300etf.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"date": pl.Date, "qvix_z_252": pl.Float64})
    qvix = (
        pl.read_parquet(path)
        .select(["date", "close"])
        .filter(pl.col("close").is_not_null())
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns([
            pl.col("close").rolling_mean(252).alias("_mean"),
            pl.col("close").rolling_std(252).alias("_std"),
        ])
        .with_columns(
            pl.when(pl.col("_std") > 0)
            .then((pl.col("close") - pl.col("_mean")) / pl.col("_std"))
            .otherwise(None)
            .alias("qvix_z_252")
        )
        .select(["date", "qvix_z_252"])
    )
    return pl.DataFrame({"date": dates}).sort("date").join_asof(
        qvix, on="date", strategy="backward", check_sortedness=False,
    )


def _macro_extra_daily(dates: list[date]) -> pl.DataFrame:
    base = pl.DataFrame({"date": dates}).sort("date")
    macro_dir = DATA / "research" / "macro"

    def series(path: Path, value_col: str, output_col: str) -> pl.DataFrame:
        return (
            pl.read_parquet(path)
            .select([
                pl.col("日期").cast(pl.Date, strict=False).alias("available_date"),
                pl.col(value_col).cast(pl.Float64, strict=False).alias(output_col),
            ])
            .filter(pl.col("available_date").is_not_null() & pl.col(output_col).is_not_null())
            .unique(subset=["available_date"], keep="last")
            .sort("available_date")
        )

    out = base
    for name, output_col in (("pmi", "pmi"), ("cpi", "cpi_yoy"), ("ppi", "ppi_yoy")):
        path = macro_dir / f"{name}.parquet"
        if path.exists():
            source = series(path, "今值", output_col)
            out = out.join_asof(
                source, left_on="date", right_on="available_date", strategy="backward",
                check_sortedness=False,
            )
            age = (pl.col("date") - pl.col("available_date")).dt.total_days()
            out = out.with_columns(
                pl.when(age <= 60).then(pl.col(output_col)).otherwise(None).alias(output_col)
            ).drop("available_date")

    money_path = macro_dir / "money_supply.parquet"
    if money_path.exists():
        money = (
            pl.read_parquet(money_path)
            .select([
                pl.concat_str([
                    pl.col("月份").cast(pl.String).str.replace("年", "-").str.replace("月份", ""),
                    pl.lit("-01"),
                ]).str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("month_date"),
                pl.col("货币(M1)-同比增长").cast(pl.Float64, strict=False).alias("_m1"),
                pl.col("货币和准货币(M2)-同比增长").cast(pl.Float64, strict=False).alias("m2_yoy"),
            ])
            .filter(pl.col("month_date").is_not_null())
            .with_columns([
                (pl.col("_m1") - pl.col("m2_yoy")).alias("m1_m2_scissors"),
                (pl.col("month_date") + pl.duration(days=45)).alias("effective_date"),
            ])
            .with_columns(
                (pl.col("m1_m2_scissors") - pl.col("m1_m2_scissors").shift(1))
                .alias("m1_m2_scissors_chg_1m")
            )
            .sort("effective_date")
        )
        out = out.join_asof(
            money.select([
                "effective_date", "m1_m2_scissors", "m1_m2_scissors_chg_1m", "m2_yoy",
            ]),
            left_on="date", right_on="effective_date", strategy="backward",
            check_sortedness=False,
        )
        age = (pl.col("date") - pl.col("effective_date")).dt.total_days()
        out = out.with_columns([
            pl.when(age <= 60).then(pl.col("m1_m2_scissors")).otherwise(None)
            .alias("m1_m2_scissors"),
            pl.when(age <= 60).then(pl.col("m1_m2_scissors_chg_1m")).otherwise(None)
            .alias("m1_m2_scissors_chg_1m"),
            pl.when(age <= 60).then(pl.col("m2_yoy")).otherwise(None).alias("m2_yoy"),
        ]).drop("effective_date")

    shibor_path = macro_dir / "shibor.parquet"
    if shibor_path.exists():
        shibor = (
            pl.read_parquet(shibor_path)
            .select([
                pl.col("日期").cast(pl.Date, strict=False).alias("date"),
                pl.col("3M-定价").cast(pl.Float64, strict=False).alias("shibor_3m"),
            ])
            .filter(pl.col("date").is_not_null() & pl.col("shibor_3m").is_not_null())
            .unique(subset=["date"], keep="last")
            .sort("date")
            .with_columns(
                (pl.col("shibor_3m") - pl.col("shibor_3m").shift(20))
                .alias("shibor_3m_chg_20d")
            )
        )
        out = out.join_asof(
            shibor, on="date", strategy="backward", check_sortedness=False,
        )
    return out


def _regime_extra_daily(dates: list[date]) -> pl.DataFrame:
    path = DATA / "regime_history" / "part.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"date": pl.Date})
    raw = (
        pl.read_parquet(path)
        .select([
            pl.col("date").cast(pl.Date),
            pl.col("above_ma20_pct").cast(pl.Float64).alias("market_breadth_above_ma20"),
            pl.col("profit_score").cast(pl.Float64).alias("market_profit_score"),
            pl.col("trend_score").cast(pl.Float64).alias("market_trend_score"),
            pl.col("speculation_score").cast(pl.Float64).alias("market_speculation_score"),
        ])
        .unique(subset=["date"], keep="last")
        .sort("date")
    )
    return pl.DataFrame({"date": dates}).sort("date").join_asof(
        raw, on="date", strategy="backward", check_sortedness=False,
    )


def _northbound_daily(dates: list[date]) -> pl.DataFrame:
    path = DATA / "research" / "macro" / "northbound.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"date": pl.Date, "northbound_net_buy_20d": pl.Float64})
    raw = (
        pl.read_parquet(path)
        .select([
            pl.col("日期").cast(pl.Date, strict=False).alias("available_date"),
            pl.col("当日成交净买额").cast(pl.Float64, strict=False).alias("_net_buy"),
        ])
        .filter(pl.col("available_date").is_not_null())
        .unique(subset=["available_date"], keep="last")
        .sort("available_date")
        .with_columns(
            pl.col("_net_buy").rolling_sum(20, min_samples=20).alias("northbound_net_buy_20d")
        )
        .select(["available_date", "northbound_net_buy_20d"])
    )
    joined = pl.DataFrame({"date": dates}).sort("date").join_asof(
        raw, left_on="date", right_on="available_date", strategy="backward",
        check_sortedness=False,
    )
    age = (pl.col("date") - pl.col("available_date")).dt.total_days()
    return joined.with_columns(
        pl.when(age <= 30).then(pl.col("northbound_net_buy_20d")).otherwise(None)
        .alias("northbound_net_buy_20d")
    ).drop("available_date")


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


def _decayed_event_score(
    event_dates: list[date],
    event_values: list[float],
    date_values: list[int],
    *,
    window: int,
) -> list[float]:
    """公告日后下一交易日生效，并在 ``window`` 个交易日内线性衰减。"""
    score = [0.0] * len(date_values)
    for announce, event_value in zip(event_dates, event_values, strict=True):
        start = bisect.bisect_right(date_values, announce.toordinal())
        for offset in range(window):
            index = start + offset
            if index >= len(date_values):
                break
            score[index] += float(event_value) * (1.0 - offset / window)
    return score


def _event_score(
    event_dates: list[date],
    date_values: list[int],
    *,
    window: int = 20,
) -> list[float]:
    return _decayed_event_score(
        event_dates,
        [1.0] * len(event_dates),
        date_values,
        window=window,
    )


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


def _short_ratio_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r50_margin").glob("*.parquet"))
    frames = [pl.read_parquet(path) for path in paths]
    if not frames:
        raise RuntimeError("两融缓存缺失")
    inst = (
        pl.read_parquet(DATA / "instruments" / "instruments.parquet")
        .select(["symbol", "float_shares"])
        .unique(subset=["symbol"])
    )
    margins = (
        pl.concat(frames, how="diagonal_relaxed")
        .group_by(["symbol", "snapshot_date"])
        .agg(pl.col("short_qty").max().alias("short_qty"))
        .sort(["symbol", "snapshot_date"])
        .filter(pl.col("symbol").is_in(symbols))
        .join(inst, on="symbol", how="left")
        .with_columns([
            pl.when((pl.col("float_shares") > 0) & (pl.col("short_qty") >= 0))
            .then(pl.col("short_qty") / pl.col("float_shares")).otherwise(None).alias("_ratio"),
            (pl.col("snapshot_date") + pl.duration(days=1)).alias("effective_date"),
        ])
    )
    joined = _calendar_frame(dates, symbols).join_asof(
        margins.select(["symbol", "effective_date", "_ratio"]).sort(["symbol", "effective_date"]),
        left_on="date", right_on="effective_date", by="symbol", strategy="backward",
    )
    return joined.with_columns(
        pl.when((pl.col("date") - pl.col("effective_date")).dt.total_days().is_between(0, 45, closed="both"))
        .then(-pl.col("_ratio")).otherwise(None).alias("short_ratio_low")
    ).select(["symbol", "date", "short_ratio_low"])


def _if_position_daily(dates: list[date]) -> pl.DataFrame:
    """IF top-20 member net-short ratio monthly change, direction negative.

    Source: evidence/r187_cffex_probe.json monthly_records (net_short_ratio).
    available_date = month_end + 1 day; the signal is a market-level column
    broadcast to all symbols.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r187_cffex_probe.json"
    if not evidence.exists():
        raise RuntimeError("R187 CFFEX 探针结果缺失")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R187 探针没有月度记录")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    frame = frame.with_columns(
        pl.col("net_short_ratio").shift(1).alias("_prev")
    ).with_columns(
        (pl.col("net_short_ratio") - pl.col("_prev")).alias("_chg"),
        (pl.col("date") + pl.duration(days=1)).alias("effective_date"),
    ).filter(pl.col("_chg").is_not_null())
    signal = frame.select(["effective_date", "_chg"]).sort("effective_date")
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns(
        pl.when(pl.col("_age").is_between(0, 45, closed="both"))
        .then(-pl.col("_chg")).otherwise(None).alias("if_net_short_chg_1m")
    ).select(["date", "if_net_short_chg_1m"])


def _bond_momentum_daily(dates: list[date]) -> pl.DataFrame:
    """Treasury futures 20d momentum, market-level, direction negative.

    Source: local caches from scripts/probe_r193_treasury.py
    (sina futures_main_sina T0/TF0).  Both futures and A-shares use the
    domestic trading calendar; asof join keeps the signal PIT.
    """
    frames = []
    for symbol, col in [("T0", "bond_mom_20d"), ("TF0", "bond_tf_mom_20d")]:
        path = DATA / "research" / "r193_treasury" / f"{symbol}.parquet"
        if not path.exists():
            raise RuntimeError(f"R193 国债期货缓存缺失: {path}")
        frame = pl.read_parquet(path).sort("date")
        frame = frame.with_columns(pl.col("close").shift(20).alias("_prev")).with_columns(
            (pl.col("close") / pl.col("_prev") - 1.0).alias(col)
        ).select(["date", col]).filter(pl.col(col).is_not_null())
        frames.append(frame)
    daily = pl.DataFrame({"date": dates}).sort("date")
    for frame in frames:
        daily = daily.join_asof(frame, on="date", strategy="backward")
    return daily.select(["date", "bond_mom_20d", "bond_tf_mom_20d"])


def _ah_premium_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level AH premium factors, direction negative.

    Reads evidence/r194_ah_premium_scan.json monthly_records (built by
    scripts/backtest_r194_ah_premium_scan.py). Signal is broadcast daily with
    a 45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r194_ah_premium_scan.json"
    if not evidence.exists():
        raise RuntimeError("R194 A-H premium scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R194 A-H premium scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "ah_premium_z", "ah_premium_chg_1m"])
    signal = signal.with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "ah_premium_z", "ah_premium_chg_1m"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("ah_premium_z")).otherwise(None).alias("ah_premium_z"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("ah_premium_chg_1m")).otherwise(None).alias("ah_premium_chg_1m"),
    ]).select(["date", "ah_premium_z", "ah_premium_chg_1m"])


def _hk_risk_daily(dates: list[date]) -> pl.DataFrame:
    """Hong Kong index momentum factors, direction positive.

    HSI/HSCEI close at 16:00 HK time, after the A-share close; the asof join
    keeps the signal PIT (last HK close on or before the A-share date).
    """
    cache = DATA / "research" / "r195_hk"
    frames = []
    for symbol, col in [("HSI", "hsi_mom_20d"), ("HSCEI", "hsciei_mom_20d")]:
        path = cache / f"{symbol}.parquet"
        if not path.exists():
            raise RuntimeError(f"R195 港股指数缓存缺失: {path}")
        frame = pl.read_parquet(path).sort("date")
        frame = frame.with_columns(
            (pl.col("close") / pl.col("close").shift(20) - 1.0).alias(col)
        ).select(["date", col]).filter(pl.col(col).is_not_null())
        frames.append(frame)
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    hs300 = (
        pl.scan_parquet([str(p) for p in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
        .with_columns((pl.col("close") / pl.col("close").shift(20) - 1.0).alias("hs300_mom_20d"))
        .select(["date", "hs300_mom_20d"])
    )
    daily = pl.DataFrame({"date": dates}).sort("date")
    for frame in frames:
        daily = daily.join_asof(frame, on="date", strategy="backward")
    daily = daily.join(hs300, on="date", how="left")
    return daily.with_columns(
        (pl.col("hsi_mom_20d") - pl.col("hs300_mom_20d")).alias("hsi_rel_hs300_20d")
    ).select(["date", "hsi_mom_20d", "hsciei_mom_20d", "hsi_rel_hs300_20d"])


def _margin_safety_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level margin maintenance-ratio factors, direction positive.

    Reads evidence/r196_margin_safety_scan.json monthly_records (built by
    scripts/backtest_r196_margin_safety_scan.py). Signal is broadcast daily
    with a 45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r196_margin_safety_scan.json"
    if not evidence.exists():
        raise RuntimeError("R196 margin safety scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R196 margin safety scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "margin_safety_z", "margin_safety_chg_20d"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "margin_safety_z", "margin_safety_chg_20d"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("margin_safety_z")).otherwise(None).alias("margin_safety_z"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("margin_safety_chg_20d")).otherwise(None).alias("margin_safety_chg_20d"),
    ]).select(["date", "margin_safety_z", "margin_safety_chg_20d"])


def _breadth_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level HS300 new-high/new-low breadth, direction positive.

    Reads evidence/r197_breadth_scan.json monthly_records (built by
    scripts/backtest_r197_breadth_scan.py). Signal is broadcast daily with a
    45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r197_breadth_scan.json"
    if not evidence.exists():
        raise RuntimeError("R197 breadth scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R197 breadth scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "nh_nl_diff_z", "nh_nl_diff_20d"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "nh_nl_diff_z", "nh_nl_diff_20d"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("nh_nl_diff_z")).otherwise(None).alias("nh_nl_diff_z"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("nh_nl_diff_20d")).otherwise(None).alias("nh_nl_diff_20d"),
    ]).select(["date", "nh_nl_diff_z", "nh_nl_diff_20d"])


def _shibor_tightness_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level SHIBOR 3M tightness z-score, direction negative.

    Reads evidence/r198_shibor_scan.json monthly_records (built by
    scripts/backtest_r198_shibor_scan.py). Signal is broadcast daily with a
    45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r198_shibor_scan.json"
    if not evidence.exists():
        raise RuntimeError("R198 SHIBOR scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R198 SHIBOR scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "shibor_tightness_z"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "shibor_tightness_z"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns(
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("shibor_tightness_z")).otherwise(None).alias("shibor_tightness_z")
    ).select(["date", "shibor_tightness_z"])


def _concentration_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level HS300 turnover concentration, direction negative.

    Reads evidence/r199_concentration_scan.json monthly_records (built by
    scripts/backtest_r199_concentration_scan.py). Signal is broadcast daily
    with a 45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r199_concentration_scan.json"
    if not evidence.exists():
        raise RuntimeError("R199 concentration scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R199 concentration scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "turnover_hhi_z", "top30_amount_share_z"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "turnover_hhi_z", "top30_amount_share_z"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("turnover_hhi_z")).otherwise(None).alias("turnover_hhi_z"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("top30_amount_share_z")).otherwise(None).alias("top30_amount_share_z"),
    ]).select(["date", "turnover_hhi_z", "top30_amount_share_z"])


def _turnover_level_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level HS300 turnover-rate level, direction negative.

    Reads evidence/r201_turnover_scan.json monthly_records (built by
    scripts/backtest_r201_turnover_scan.py). Signal is broadcast daily with a
    45-day freshness cap; values are PIT month-end figures.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r201_turnover_scan.json"
    if not evidence.exists():
        raise RuntimeError("R201 turnover scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R201 turnover scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "hs300_turnover_z", "hs300_turnover_chg_20d"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "hs300_turnover_z", "hs300_turnover_chg_20d"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("hs300_turnover_z")).otherwise(None).alias("hs300_turnover_z"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("hs300_turnover_chg_20d")).otherwise(None).alias("hs300_turnover_chg_20d"),
    ]).select(["date", "hs300_turnover_z", "hs300_turnover_chg_20d"])


def _turnover_extended_daily(dates: list[date]) -> pl.DataFrame:
    """Market-level HS300 turnover with 2017 history, direction negative.

    Reads evidence/r203_turnover_scan.json, which extends the R201 monthly
    turnover history through 2017. The signal is broadcast daily with the same
    45-day freshness cap used by the R201 factor.
    """
    evidence = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r203_turnover_scan.json"
    if not evidence.exists():
        raise RuntimeError("R203 turnover scan missing")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    records = payload.get("monthly_records") or []
    if not records:
        raise RuntimeError("R203 turnover scan has no monthly records")
    frame = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).sort("date")
    signal = frame.select(["date", "hs300_turnover_z_ext2017", "hs300_turnover_chg_20d"]).with_columns(
        (pl.col("date") + pl.duration(days=1)).alias("effective_date")
    ).select(["effective_date", "hs300_turnover_z_ext2017", "hs300_turnover_chg_20d"])
    daily = pl.DataFrame({"date": dates}).sort("date").join_asof(
        signal, left_on="date", right_on="effective_date", strategy="backward",
    ).with_columns((pl.col("date") - pl.col("effective_date")).dt.total_days().alias("_age"))
    return daily.with_columns([
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("hs300_turnover_z_ext2017")).otherwise(None).alias("hs300_turnover_z_ext2017"),
        pl.when(pl.col("_age").is_between(0, 45, closed="both")).then(pl.col("hs300_turnover_chg_20d")).otherwise(None).alias("hs300_turnover_chg_20d_ext2017"),
    ]).with_columns(
        pl.when(pl.col("hs300_turnover_z_ext2017").is_not_null() & (pl.col("hs300_turnover_z_ext2017") < 0))
        .then(1.0).otherwise(0.0).alias("hs300_turnover_gate_flag_ext2017")
    ).select(["date", "hs300_turnover_z_ext2017", "hs300_turnover_chg_20d_ext2017", "hs300_turnover_gate_flag_ext2017"])


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


def _residual_volatility_frame(
    stock_panel: pl.DataFrame,
    market_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Compute negative annualized HS300 market-model residual volatility.

    The zero-intercept residual variance over a rolling window is
    ``var(y) - cov(x, y)^2 / var(x)``.  The output is negated so that a
    higher score always means lower idiosyncratic volatility.
    """
    stocks = (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
    )
    market = (
        market_panel.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_market_ret")
        )
        .select(["date", "_market_ret"])
    )
    return (
        stocks.join(market, on="date", how="left")
        .with_columns([
            pl.rolling_cov(
                "_ret", "_market_ret", window_size=window_size, min_samples=window_size
            ).over("symbol").alias("_cov"),
            pl.col("_ret").rolling_var(window_size, min_samples=window_size).over("symbol").alias("_var_y"),
            pl.col("_market_ret").rolling_var(window_size, min_samples=window_size).over("symbol").alias("_var_m"),
        ])
        .with_columns(
            pl.when((pl.col("_var_m") > 0.0) & pl.col("_cov").is_not_null())
            .then((pl.col("_var_y") - pl.col("_cov").pow(2) / pl.col("_var_m")).clip(lower_bound=0.0))
            .otherwise(None)
            .alias("_residual_var")
        )
        .with_columns(
            (-pl.col("_residual_var").sqrt() * math.sqrt(252.0)).alias("idio_vol_60d_low")
        )
        .select(["symbol", "date", "idio_vol_60d_low"])
    )


def _idio_vol_daily(prices: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("kline_index_daily 为空，无法计算 HS300 残差波动率")
    market = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
    )
    return _residual_volatility_frame(prices, market)


def _price_delay_frame(
    stock_panel: pl.DataFrame,
    market_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Compute the one-lag HS300 price-delay ratio.

    The factor compares the stock's response to lagged market returns with
    its contemporaneous and lagged market response.  A higher positive value
    means that more of the stock's market response arrives with a delay.
    """
    stocks = (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
    )
    market = (
        market_panel.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_market_ret")
        )
        .with_columns(pl.col("_market_ret").shift(1).alias("_market_ret_lag1"))
        .select(["date", "_market_ret", "_market_ret_lag1"])
    )
    return (
        stocks.join(market, on="date", how="left")
        .with_columns([
            pl.rolling_cov(
                "_ret", "_market_ret", window_size=window_size, min_samples=window_size
            ).over("symbol").alias("_cov0"),
            pl.col("_market_ret").rolling_var(window_size, min_samples=window_size).over("symbol").alias("_var0"),
            pl.rolling_cov(
                "_ret", "_market_ret_lag1", window_size=window_size, min_samples=window_size
            ).over("symbol").alias("_cov1"),
            pl.col("_market_ret_lag1").rolling_var(window_size, min_samples=window_size).over("symbol").alias("_var1"),
        ])
        .with_columns([
            pl.when(pl.col("_var0") > 0.0).then(pl.col("_cov0") / pl.col("_var0")).otherwise(None).alias("_beta0"),
            pl.when(pl.col("_var1") > 0.0).then(pl.col("_cov1") / pl.col("_var1")).otherwise(None).alias("_beta1"),
        ])
        .with_columns(
            pl.when(pl.col("_beta0").is_not_null() & pl.col("_beta1").is_not_null())
            .then(pl.col("_beta1") / (pl.col("_beta0").abs() + pl.col("_beta1").abs() + 1e-6))
            .otherwise(None)
            .alias("price_delay_60d")
        )
        .select(["symbol", "date", "price_delay_60d"])
    )


def _price_delay_daily(prices: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("kline_index_daily 为空，无法计算 HS300 价格延迟")
    market = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
    )
    return _price_delay_frame(prices, market)


def _vol_of_vol_frame(
    stock_panel: pl.DataFrame,
    *,
    vol_window: int = 20,
    stability_window: int = 60,
) -> pl.DataFrame:
    """Compute negative 60-day stability of 20-day realized volatility."""
    return (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
        .with_columns(
            (
                pl.col("_ret").rolling_std(vol_window, min_samples=vol_window).over("symbol")
                * math.sqrt(252.0)
            ).alias("_rv20")
        )
        .with_columns(
            pl.col("_rv20").rolling_std(stability_window, min_samples=stability_window).over("symbol").alias("_vol_of_vol")
        )
        .with_columns(
            (-pl.col("_vol_of_vol")).alias("vol_of_vol_60d_low")
        )
        .select(["symbol", "date", "vol_of_vol_60d_low"])
    )


def _vol_of_vol_daily(prices: pl.DataFrame) -> pl.DataFrame:
    return _vol_of_vol_frame(prices)


def _market_down_beta_frame(
    stock_panel: pl.DataFrame,
    market_panel: pl.DataFrame,
    *,
    window_size: int = 60,
    min_down_days: int = 12,
) -> pl.DataFrame:
    """Conditional HS300-down-day beta, negated so higher is safer."""
    stocks = (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
    )
    market = (
        market_panel.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_market_ret")
        )
        .select(["date", "_market_ret"])
    )
    return (
        stocks.join(market, on="date", how="left")
        .with_columns([
            (pl.col("_market_ret") < 0.0).cast(pl.Float64).alias("_down_mask"),
            pl.col("_market_ret").pow(2).alias("_market_sq"),
            (pl.col("_ret") * pl.col("_market_ret")).alias("_ret_market"),
        ])
        .with_columns([
            (pl.col("_market_ret") * pl.col("_down_mask")).alias("_down_x"),
            (pl.col("_ret") * pl.col("_down_mask")).alias("_down_y"),
            (pl.col("_market_sq") * pl.col("_down_mask")).alias("_down_xx"),
            (pl.col("_ret_market") * pl.col("_down_mask")).alias("_down_xy"),
        ])
        .with_columns([
            pl.col("_down_mask").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_n_down"),
            pl.col("_down_x").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sx"),
            pl.col("_down_y").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sy"),
            pl.col("_down_xx").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxx"),
            pl.col("_down_xy").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxy"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_n_down") >= min_down_days)
                & ((pl.col("_n_down") * pl.col("_sxx") - pl.col("_sx").pow(2)) > 1e-12)
            )
            .then(
                (pl.col("_n_down") * pl.col("_sxy") - pl.col("_sx") * pl.col("_sy"))
                / (pl.col("_n_down") * pl.col("_sxx") - pl.col("_sx").pow(2))
            )
            .otherwise(None)
            .alias("_market_down_beta")
        )
        .with_columns((-pl.col("_market_down_beta")).alias("market_down_beta_60d_low"))
        .select(["symbol", "date", "market_down_beta_60d_low"])
    )


def _market_down_beta_daily(prices: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("kline_index_daily 为空，无法计算 HS300 市场下跌 beta")
    market = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
    )
    return _market_down_beta_frame(prices, market)


def _beta_asymmetry_frame(
    stock_panel: pl.DataFrame,
    market_panel: pl.DataFrame,
    *,
    window_size: int = 120,
    min_up_days: int = 20,
    min_down_days: int = 20,
) -> pl.DataFrame:
    """Compute conditional HS300 up-beta minus down-beta over a rolling window."""
    stocks = (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
    )
    market = (
        market_panel.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_market_ret")
        )
        .select(["date", "_market_ret"])
    )
    return (
        stocks.join(market, on="date", how="left")
        .with_columns([
            (pl.col("_market_ret") > 0.0).cast(pl.Float64).alias("_up_mask"),
            (pl.col("_market_ret") < 0.0).cast(pl.Float64).alias("_down_mask"),
            pl.col("_market_ret").pow(2).alias("_market_sq"),
            (pl.col("_ret") * pl.col("_market_ret")).alias("_ret_market"),
            pl.col("_market_ret").is_not_null().cast(pl.Int32).alias("_valid_market"),
        ])
        .with_columns([
            (pl.col("_market_ret") * pl.col("_up_mask")).alias("_up_x"),
            (pl.col("_ret") * pl.col("_up_mask")).alias("_up_y"),
            (pl.col("_market_sq") * pl.col("_up_mask")).alias("_up_xx"),
            (pl.col("_ret_market") * pl.col("_up_mask")).alias("_up_xy"),
            (pl.col("_market_ret") * pl.col("_down_mask")).alias("_down_x"),
            (pl.col("_ret") * pl.col("_down_mask")).alias("_down_y"),
            (pl.col("_market_sq") * pl.col("_down_mask")).alias("_down_xx"),
            (pl.col("_ret_market") * pl.col("_down_mask")).alias("_down_xy"),
        ])
        .with_columns([
            pl.col("_valid_market").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_valid_window"),
            pl.col("_up_mask").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_n_up"),
            pl.col("_up_x").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sx_up"),
            pl.col("_up_y").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sy_up"),
            pl.col("_up_xx").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxx_up"),
            pl.col("_up_xy").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxy_up"),
            pl.col("_down_mask").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_n_down"),
            pl.col("_down_x").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sx_down"),
            pl.col("_down_y").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sy_down"),
            pl.col("_down_xx").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxx_down"),
            pl.col("_down_xy").rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_sxy_down"),
        ])
        .with_columns([
            pl.when(
                (pl.col("_valid_window") == window_size)
                & (pl.col("_n_up") >= min_up_days)
                & ((pl.col("_n_up") * pl.col("_sxx_up") - pl.col("_sx_up").pow(2)) > 1e-12)
            )
            .then(
                (pl.col("_n_up") * pl.col("_sxy_up") - pl.col("_sx_up") * pl.col("_sy_up"))
                / (pl.col("_n_up") * pl.col("_sxx_up") - pl.col("_sx_up").pow(2))
            )
            .otherwise(None)
            .alias("_up_beta"),
            pl.when(
                (pl.col("_valid_window") == window_size)
                & (pl.col("_n_down") >= min_down_days)
                & ((pl.col("_n_down") * pl.col("_sxx_down") - pl.col("_sx_down").pow(2)) > 1e-12)
            )
            .then(
                (pl.col("_n_down") * pl.col("_sxy_down") - pl.col("_sx_down") * pl.col("_sy_down"))
                / (pl.col("_n_down") * pl.col("_sxx_down") - pl.col("_sx_down").pow(2))
            )
            .otherwise(None)
            .alias("_down_beta"),
        ])
        .with_columns(
            pl.when(pl.col("_up_beta").is_not_null() & pl.col("_down_beta").is_not_null())
            .then(pl.col("_up_beta") - pl.col("_down_beta"))
            .otherwise(None)
            .alias("beta_asymmetry_120d")
        )
        .select(["symbol", "date", "beta_asymmetry_120d"])
    )


def _beta_asymmetry_daily(prices: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("kline_index_daily 为空，无法计算 HS300 beta 不对称性")
    market = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
    )
    return _beta_asymmetry_frame(prices, market)


def _trend_efficiency_frame(
    stock_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Compute net log trend divided by the accumulated absolute log-return path."""
    return (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol")).log().alias("_ret")
        )
        .with_columns([
            (pl.col("close") / pl.col("close").shift(window_size).over("symbol")).log().alias("_net_ret"),
            pl.col("_ret").abs().rolling_sum(window_size, min_samples=window_size).over("symbol").alias("_path"),
        ])
        .with_columns(
            pl.when(pl.col("_path") > 1e-12)
            .then(pl.col("_net_ret") / pl.col("_path"))
            .otherwise(None)
            .alias("trend_efficiency_60d")
        )
        .select(["symbol", "date", "trend_efficiency_60d"])
    )


def _risk_adjusted_momentum_frame(
    stock_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Compute 60-day return divided by annualized realized volatility."""
    return (
        stock_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret")
        )
        .with_columns([
            (pl.col("close") / pl.col("close").shift(window_size).over("symbol") - 1.0).alias("_net_ret"),
            (
                pl.col("_ret").rolling_std(window_size, min_samples=window_size).over("symbol")
                * math.sqrt(252.0)
            ).alias("_vol"),
        ])
        .with_columns(
            pl.when(pl.col("_vol") > 1e-12)
            .then(pl.col("_net_ret") / pl.col("_vol"))
            .otherwise(None)
            .alias("risk_adjusted_momentum_60d")
        )
        .select(["symbol", "date", "risk_adjusted_momentum_60d"])
    )


def _market_skew_frame(
    market_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Compute negative 60-day skewness of HS300 daily returns."""
    returns = (
        market_panel.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_ret")
        )
        .with_columns([
            pl.col("_ret").rolling_sum(window_size, min_samples=window_size).alias("_s1"),
            pl.col("_ret").pow(2).rolling_sum(window_size, min_samples=window_size).alias("_s2"),
            pl.col("_ret").pow(3).rolling_sum(window_size, min_samples=window_size).alias("_s3"),
        ])
        .with_columns([
            (pl.col("_s1") / window_size).alias("_mean"),
            (pl.col("_s2") / window_size - (pl.col("_s1") / window_size).pow(2)).alias("_m2"),
            (
                pl.col("_s3") / window_size
                - 3.0 * (pl.col("_s1") / window_size) * (pl.col("_s2") / window_size)
                + 2.0 * (pl.col("_s1") / window_size).pow(3)
            ).alias("_m3"),
        ])
        .with_columns(
            pl.when(pl.col("_m2") > 1e-12)
            .then(-pl.col("_m3") / pl.col("_m2").pow(1.5))
            .otherwise(None)
            .alias("market_skew_60d_low")
        )
        .select(["date", "market_skew_60d_low"])
    )
    return returns


def _market_skew_daily(dates: list[date]) -> pl.DataFrame:
    del dates
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("kline_index_daily 为空，无法计算 HS300 已实现偏度")
    market = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
    )
    return _market_skew_frame(market)


def _cash_flow_stability_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    """Compute negative 4-quarter stability of operating cash flow over assets."""
    from app.backtest.fundamentals import _statutory_deadline

    balance = pl.read_parquet(DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("total_assets").cast(pl.Float64),
    ])
    cash = pl.read_parquet(DATA / "financials" / "cash_flow" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("net_operating_cash_flow").cast(pl.Float64),
    ])
    financial = (
        balance.join(cash, on=["symbol", "period_end"], how="inner")
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns(
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_deadline")
        )
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("net_operating_cash_flow").shift(1).over("symbol").alias("_previous_ocf"),
        ])
        .with_columns(
            (
                pl.col("_period_index")
                - (pl.col("_previous_period").dt.year() * 4 + pl.col("_previous_period").dt.quarter())
            ).alias("_quarter_gap")
        )
        .with_columns(
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("net_operating_cash_flow"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_ocf").is_not_null())
            .then(pl.col("net_operating_cash_flow") - pl.col("_previous_ocf"))
            .otherwise(None)
            .alias("_quarter_ocf")
        )
        .with_columns(
            pl.when(pl.col("total_assets") > 0)
            .then(pl.col("_quarter_ocf") / pl.col("total_assets"))
            .otherwise(None)
            .alias("_quarter_ratio")
        )
        .with_columns([
            pl.col("_quarter_ratio").rolling_std(4, min_samples=4, ddof=0).over("symbol").alias("_std4"),
            pl.col("_quarter_ratio").is_not_null().cast(pl.Int32).rolling_sum(4, min_samples=4).over("symbol").alias("_count4"),
            pl.col("_period_index").shift(3).over("symbol").alias("_period_index_lag3"),
            pl.col("_deadline").cast(pl.Int64).rolling_max(4, min_samples=4).over("symbol").cast(pl.Date).alias("_deadline4"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag3") == 3)
                & (pl.col("_count4") == 4)
                & (pl.col("_std4") >= 0.0)
            )
            .then(-pl.col("_std4"))
            .otherwise(None)
            .alias("cash_flow_stability_4q_low")
        )
        .filter(pl.col("cash_flow_stability_4q_low").is_not_null())
        .with_columns((pl.col("_deadline4") + pl.duration(days=1)).alias("effective_date"))
        .select(["symbol", "effective_date", "cash_flow_stability_4q_low"])
        .sort(["symbol", "effective_date"])
    )
    daily = _calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return (
        daily.join_asof(
            financial.rename({"effective_date": "_effective"}),
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .select(["symbol", "date", "cash_flow_stability_4q_low"])
    )


def _net_financing_4q_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    """Compute negative trailing-four-quarter net financing scaled by assets."""
    from app.backtest.fundamentals import _statutory_deadline

    balance = pl.read_parquet(DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("total_assets").cast(pl.Float64),
    ])
    cash = pl.read_parquet(DATA / "financials" / "cash_flow" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("net_financing_cash_flow").cast(pl.Float64),
    ])
    financial = (
        balance.join(cash, on=["symbol", "period_end"], how="inner")
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns(
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_deadline")
        )
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("net_financing_cash_flow").shift(1).over("symbol").alias("_previous_financing"),
        ])
        .with_columns(
            (
                pl.col("_period_index")
                - (pl.col("_previous_period").dt.year() * 4 + pl.col("_previous_period").dt.quarter())
            ).alias("_quarter_gap")
        )
        .with_columns(
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("net_financing_cash_flow"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_financing").is_not_null())
            .then(pl.col("net_financing_cash_flow") - pl.col("_previous_financing"))
            .otherwise(None)
            .alias("_quarter_financing")
        )
        .with_columns([
            pl.col("_quarter_financing").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_financing"),
            pl.col("_quarter_financing").is_not_null().cast(pl.Int32).rolling_sum(4, min_samples=4).over("symbol").alias("_count4"),
            pl.col("_period_index").shift(3).over("symbol").alias("_period_index_lag3"),
            pl.col("_deadline").cast(pl.Int64).rolling_max(4, min_samples=4).over("symbol").cast(pl.Date).alias("_deadline4"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag3") == 3)
                & (pl.col("_count4") == 4)
                & (pl.col("total_assets") > 0)
            )
            .then(-pl.col("_ttm_financing") / pl.col("total_assets"))
            .otherwise(None)
            .alias("net_financing_4q_low")
        )
        .filter(pl.col("net_financing_4q_low").is_not_null())
        .with_columns((pl.col("_deadline4") + pl.duration(days=1)).alias("effective_date"))
        .select(["symbol", "effective_date", "net_financing_4q_low"])
        .sort(["symbol", "effective_date"])
    )
    daily = _calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return (
        daily.join_asof(
            financial.rename({"effective_date": "_effective"}),
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .select(["symbol", "date", "net_financing_4q_low"])
    )


def _sga_ratio_4q_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    """Compute negative trailing-four-quarter SG&A expense intensity."""
    from app.backtest.fundamentals import _statutory_deadline

    income = pl.read_parquet(DATA / "financials" / "income" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("revenue").cast(pl.Float64),
        pl.col("selling_expenses").cast(pl.Float64),
        pl.col("management_expenses").cast(pl.Float64),
    ])
    financial = (
        income.unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns(
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_deadline")
        )
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("revenue").shift(1).over("symbol").alias("_previous_revenue"),
            pl.col("selling_expenses").shift(1).over("symbol").alias("_previous_selling"),
            pl.col("management_expenses").shift(1).over("symbol").alias("_previous_management"),
        ])
        .with_columns(
            (
                pl.col("_period_index")
                - (pl.col("_previous_period").dt.year() * 4 + pl.col("_previous_period").dt.quarter())
            ).alias("_quarter_gap")
        )
        .with_columns([
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("revenue"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_revenue").is_not_null())
            .then(pl.col("revenue") - pl.col("_previous_revenue"))
            .otherwise(None)
            .alias("_quarter_revenue"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("selling_expenses"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_selling").is_not_null())
            .then(pl.col("selling_expenses") - pl.col("_previous_selling"))
            .otherwise(None)
            .alias("_quarter_selling"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("management_expenses"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_management").is_not_null())
            .then(pl.col("management_expenses") - pl.col("_previous_management"))
            .otherwise(None)
            .alias("_quarter_management"),
        ])
        .with_columns([
            pl.col("_quarter_revenue").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_revenue"),
            pl.col("_quarter_selling").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_selling"),
            pl.col("_quarter_management").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_management"),
            pl.col("_quarter_revenue").is_not_null().cast(pl.Int32).rolling_sum(4, min_samples=4).over("symbol").alias("_count4"),
            pl.col("_period_index").shift(3).over("symbol").alias("_period_index_lag3"),
            pl.col("_deadline").cast(pl.Int64).rolling_max(4, min_samples=4).over("symbol").cast(pl.Date).alias("_deadline4"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag3") == 3)
                & (pl.col("_count4") == 4)
                & (pl.col("_ttm_revenue") > 0)
                & pl.col("_ttm_selling").is_not_null()
                & pl.col("_ttm_management").is_not_null()
            )
            .then(-(pl.col("_ttm_selling") + pl.col("_ttm_management")) / pl.col("_ttm_revenue"))
            .otherwise(None)
            .alias("sga_ratio_4q_low")
        )
        .with_columns([
            pl.col("sga_ratio_4q_low").shift(1).over("symbol").alias("_sga_ratio_4q_lag1"),
            pl.col("_period_index").shift(1).over("symbol").alias("_period_index_lag1"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag1") == 1)
                & pl.col("sga_ratio_4q_low").is_not_null()
                & pl.col("_sga_ratio_4q_lag1").is_not_null()
            )
            .then(pl.col("sga_ratio_4q_low") - pl.col("_sga_ratio_4q_lag1"))
            .otherwise(None)
            .alias("sga_ratio_4q_qoq_improvement")
        )
        .filter(pl.col("sga_ratio_4q_low").is_not_null())
        .with_columns((pl.col("_deadline4") + pl.duration(days=1)).alias("effective_date"))
        .select([
            "symbol", "effective_date", "sga_ratio_4q_low",
            "sga_ratio_4q_qoq_improvement",
        ])
        .sort(["symbol", "effective_date"])
    )
    daily = _calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return (
        daily.join_asof(
            financial.rename({"effective_date": "_effective"}),
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .select([
            "symbol", "date", "sga_ratio_4q_low",
            "sga_ratio_4q_qoq_improvement",
        ])
    )


def _management_expense_ratio_4q_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    """Compute negative trailing-four-quarter management expense intensity."""
    from app.backtest.fundamentals import _statutory_deadline

    income = pl.read_parquet(DATA / "financials" / "income" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("revenue").cast(pl.Float64),
        pl.col("management_expenses").cast(pl.Float64),
    ])
    financial = (
        income.unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns(
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_deadline")
        )
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("revenue").shift(1).over("symbol").alias("_previous_revenue"),
            pl.col("management_expenses").shift(1).over("symbol").alias("_previous_management"),
        ])
        .with_columns(
            (
                pl.col("_period_index")
                - (pl.col("_previous_period").dt.year() * 4 + pl.col("_previous_period").dt.quarter())
            ).alias("_quarter_gap")
        )
        .with_columns([
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("revenue"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_revenue").is_not_null())
            .then(pl.col("revenue") - pl.col("_previous_revenue"))
            .otherwise(None)
            .alias("_quarter_revenue"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("management_expenses"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_management").is_not_null())
            .then(pl.col("management_expenses") - pl.col("_previous_management"))
            .otherwise(None)
            .alias("_quarter_management"),
        ])
        .with_columns([
            pl.col("_quarter_revenue").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_revenue"),
            pl.col("_quarter_management").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_management"),
            pl.col("_quarter_revenue").is_not_null().cast(pl.Int32).rolling_sum(4, min_samples=4).over("symbol").alias("_count4"),
            pl.col("_period_index").shift(3).over("symbol").alias("_period_index_lag3"),
            pl.col("_deadline").cast(pl.Int64).rolling_max(4, min_samples=4).over("symbol").cast(pl.Date).alias("_deadline4"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag3") == 3)
                & (pl.col("_count4") == 4)
                & (pl.col("_ttm_revenue") > 0)
                & pl.col("_ttm_management").is_not_null()
            )
            .then(-pl.col("_ttm_management") / pl.col("_ttm_revenue"))
            .otherwise(None)
            .alias("management_expense_ratio_4q_low")
        )
        .filter(pl.col("management_expense_ratio_4q_low").is_not_null())
        .with_columns((pl.col("_deadline4") + pl.duration(days=1)).alias("effective_date"))
        .select(["symbol", "effective_date", "management_expense_ratio_4q_low"])
        .sort(["symbol", "effective_date"])
    )
    daily = _calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return (
        daily.join_asof(
            financial.rename({"effective_date": "_effective"}),
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .select(["symbol", "date", "management_expense_ratio_4q_low"])
    )


def _corwin_schultz_spread_frame(
    stock_panel: pl.DataFrame,
    *,
    window_size: int = 60,
) -> pl.DataFrame:
    """Estimate effective bid-ask spread from consecutive daily high/low ranges.

    The two-day estimate for t-1/t is only exposed on date t, so the daily
    signal never uses next-day high/low information.
    """
    denominator = 3.0 - 2.0 * math.sqrt(2.0)
    stocks = (
        stock_panel.select(["symbol", "date", "high", "low"])
        .filter((pl.col("high") > 0.0) & (pl.col("low") > 0.0))
        .sort(["symbol", "date"])
        .with_columns([
            (pl.col("high") / pl.col("low")).log().pow(2).alias("_hl2"),
            pl.col("high").shift(-1).over("symbol").alias("_next_high"),
            pl.col("low").shift(-1).over("symbol").alias("_next_low"),
        ])
        .with_columns([
            (pl.col("_next_high") / pl.col("_next_low")).log().pow(2).alias("_next_hl2"),
            (
                pl.max_horizontal(pl.col("high"), pl.col("_next_high"))
                / pl.min_horizontal(pl.col("low"), pl.col("_next_low"))
            ).log().pow(2).alias("_gamma"),
        ])
        .with_columns(
            (pl.col("_hl2") + pl.col("_next_hl2")).alias("_beta")
        )
        .with_columns(
            (
                (pl.col("_beta").mul(2.0).sqrt() - pl.col("_beta").sqrt()) / denominator
                - (pl.col("_gamma") / denominator).sqrt()
            ).clip(-10.0, 10.0).alias("_alpha")
        )
        .with_columns(
            pl.when(pl.col("_next_high").is_not_null() & pl.col("_next_low").is_not_null())
            .then(
                (2.0 * (pl.col("_alpha").exp() - 1.0) / (1.0 + pl.col("_alpha").exp())).clip(lower_bound=0.0)
            )
            .otherwise(None)
            .alias("_spread_pair")
        )
        .with_columns(pl.col("_spread_pair").shift(1).over("symbol").alias("_available_spread"))
        .with_columns(
            pl.col("_available_spread").rolling_mean(window_size, min_samples=window_size).over("symbol").alias("cs_spread_60d")
        )
        .select(["symbol", "date", "cs_spread_60d"])
    )
    return stocks


def _prices(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_in(dates))
        .select(["symbol", "date", "close", "raw_close", "high", "low"])
        .collect()
        .sort(["symbol", "date"])
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
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    fund_holding = _fund_holding_daily(dates, symbols, instruments)
    lhb = _lhb_daily(dates, symbols)
    pledge = _pledge_daily(dates, symbols)
    repurchase = _repurchase_daily(dates, symbols)
    block_trade = _block_trade_daily(dates, symbols)
    analyst = _analyst_daily(dates, symbols)
    forecast = _forecast_daily(dates, symbols)
    express = _express_daily(dates, symbols)
    industry = _industry_daily(dates, symbols)
    mainline = _mainline_daily(dates, symbols)
    etf_flow = _etf_flow_daily(dates)
    qvix = _qvix_daily(dates)
    macro_extra = _macro_extra_daily(dates)
    regime_extra = _regime_extra_daily(dates)
    northbound = _northbound_daily(dates)
    margin = _margin_daily(dates, symbols)
    short_ratio = _short_ratio_daily(dates, symbols)
    if_position = _if_position_daily(dates)
    bond_momentum = _bond_momentum_daily(dates)
    ah_premium = _ah_premium_daily(dates)
    hk_risk = _hk_risk_daily(dates)
    margin_safety = _margin_safety_daily(dates)
    breadth = _breadth_daily(dates)
    shibor_tightness = _shibor_tightness_daily(dates)
    concentration = _concentration_daily(dates)
    turnover_level = _turnover_level_daily(dates)
    turnover_extended = _turnover_extended_daily(dates)
    goodwill = _goodwill_daily(dates, symbols)
    equity_supply = _equity_supply_daily(dates, symbols)
    erp = _erp_daily(dates)
    macro = _macro_daily(dates)
    emotion = _emotion_daily(dates)
    prices = _prices(dates, symbols)
    idio_vol = _idio_vol_daily(prices)
    price_delay = _price_delay_daily(prices)
    cs_spread = _corwin_schultz_spread_frame(prices)
    vol_of_vol = _vol_of_vol_daily(prices)
    market_down_beta = _market_down_beta_daily(prices)
    beta_asymmetry = _beta_asymmetry_daily(prices)
    trend_efficiency = _trend_efficiency_frame(prices)
    risk_adjusted_momentum = _risk_adjusted_momentum_frame(prices)
    market_skew = _market_skew_daily(dates)
    cash_flow_stability = _cash_flow_stability_daily(dates, symbols)
    net_financing = _net_financing_4q_daily(dates, symbols)
    sga_ratio = _sga_ratio_4q_daily(dates, symbols)
    management_expense_ratio = _management_expense_ratio_4q_daily(dates, symbols)

    base = _calendar_frame(dates, symbols)
    for frame in (
        holder, dividend, insider, holder_trade, fund_holding,
        lhb, pledge, repurchase, block_trade, analyst, forecast, express,
        industry, mainline, margin, short_ratio, goodwill, equity_supply, prices, idio_vol, price_delay, cs_spread, vol_of_vol, market_down_beta, beta_asymmetry, trend_efficiency, risk_adjusted_momentum, cash_flow_stability, net_financing, sga_ratio, management_expense_ratio,
    ):
        base = base.join(frame, on=["symbol", "date"], how="left")
    for frame in (erp, macro, emotion, etf_flow, qvix, macro_extra, regime_extra, northbound, if_position, bond_momentum, ah_premium, hk_risk, margin_safety, breadth, shibor_tightness, concentration, turnover_level, turnover_extended, market_skew):
        base = base.join(frame, on="date", how="left")
    base = base.with_columns(
        pl.when((pl.col("raw_close") > 0) & pl.col("dividend_ttm_cash").is_not_null())
        .then(pl.col("dividend_ttm_cash") / pl.col("raw_close"))
        .otherwise(None)
        .alias("dividend_yield")
    ).with_columns([
        pl.col(name).fill_null(0.0)
        for name in (
            "lhb_net_buy_ratio_20d", "lhb_inst_net_buy_ratio_20d",
            "lhb_event_count_20d_low", "repurchase_event_60d",
            "block_trade_ratio_20d_low", "block_premium_20d",
            "forecast_surprise_60d", "express_surprise_60d",
            "mainline_top1_flag", "mainline_strength", "mainline_leader_flag",
        )
    ]).drop("raw_close")
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
            "研究实验数据: 增持/减持事件、股东结构变化、基金持仓拥挤度、两融、"
            "龙虎榜、质押、回购、大宗交易、分析师、业绩预告/快报、商誉与研发、"
            "股权融资供给、分红、ERP、信用/期限利差、市场情绪、HS300残差波动率、价格延迟、Corwin-Schultz价差、波动率稳定性、市场下跌条件beta、beta不对称性、趋势效率、风险调整动量、市场已实现偏度、现金流稳定性、净筹资强度、SG&A费用率、SG&A费用率环比改善和管理费用率。"
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
