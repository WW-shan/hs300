# 输入说明: R83 — 2018-2020 独立早期样本稳健性检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r83_early_holdout.py
# 原始缓存 data/research/r83_* (不入库); 结果 JSON 同目录 r83_early_holdout.json。
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22", "script_r22_scan.py")
r40 = _load("r40", "script_r40_survivorship.py")
r42 = _load("r42", "script_r42_historical_factor_screen.py")
r78 = _load("r78", "script_r78_daily_membership_deletions.py")
DATA = r40.DATA
MEMBERSHIP_DIR = DATA / "research" / "r83_membership"
PRICE_DIR = DATA / "research" / "r83_prices"
FIN_DIR = DATA / "research" / "r83_financials"
MEMBERSHIP_CACHE = MEMBERSHIP_DIR / "daily_snapshots.parquet"
START = date(2018, 1, 1)
END = date(2020, 12, 31)
SAMPLE_A = (date(2018, 7, 1), date(2019, 12, 31))
SAMPLE_B = (date(2020, 1, 1), date(2020, 12, 31))
MIN_VALID = 80
COST_2X = 0.0028
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "annual_vol_20d": False,
    "amount": False,
    "momentum_20d": True,
    "ocf_ni": True,
    "accrual": False,
    "asset_growth_yoy": False,
    "cash_conversion_cycle": False,
}
FIN_EFFECTIVE = {
    "ocf_ni": "_ttm_effective",
    "accrual": "_ttm_effective",
    "asset_growth_yoy": "_growth_effective",
    "cash_conversion_cycle": "_ccc_effective",
}


def _trading_dates(start: date, end: date) -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    values = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    return [date.fromisoformat(str(value)[:10]) for value in values]


def _ensure_membership(dates: list[date]) -> tuple[pl.DataFrame, dict]:
    import baostock as bs

    MEMBERSHIP_DIR.mkdir(parents=True, exist_ok=True)
    if MEMBERSHIP_CACHE.exists():
        frame = pl.read_parquet(MEMBERSHIP_CACHE).with_columns([
            pl.col("query_date").cast(pl.Date),
            pl.col("update_date").cast(pl.Date),
            pl.col("symbol").cast(pl.String),
            pl.col("name").cast(pl.String),
        ])
    else:
        frame = pl.DataFrame(schema={
            "query_date": pl.Date,
            "update_date": pl.Date,
            "symbol": pl.String,
            "name": pl.String,
        })
    done = set(frame["query_date"].unique().to_list()) if frame.height else set()
    missing = [day for day in dates if day not in done]
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        buffer = []
        for index, day in enumerate(missing, start=1):
            buffer.extend(r78._query_snapshot(bs, day))
            if index % 50 == 0 or index == len(missing):
                chunk = pl.DataFrame(buffer).with_columns([
                    pl.col("query_date").cast(pl.Date),
                    pl.col("update_date").cast(pl.Date),
                ])
                frame = pl.concat([frame, chunk], how="diagonal_relaxed") if frame.height else chunk
                frame = frame.unique(subset=["query_date", "symbol"], keep="last").sort(
                    ["query_date", "symbol"]
                )
                frame.write_parquet(MEMBERSHIP_CACHE)
                buffer.clear()
                print(f"membership {index}/{len(missing)}", flush=True)
    finally:
        bs.logout()
    frame = pl.read_parquet(MEMBERSHIP_CACHE).with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("update_date").cast(pl.Date),
    ])
    per_day = frame.group_by("query_date").agg([
        pl.len().alias("rows"),
        pl.col("symbol").n_unique().alias("symbols"),
    ]).sort("query_date")
    expected = set(dates)
    observed = set(per_day["query_date"].to_list())
    audit = {
        "requested_dates": len(dates),
        "available_dates": len(observed),
        "missing_dates": sorted(expected - observed),
        "rows": frame.height,
        "symbols": frame["symbol"].n_unique(),
        "min_rows": int(per_day["rows"].min()) if per_day.height else 0,
        "max_rows": int(per_day["rows"].max()) if per_day.height else 0,
        "min_symbols": int(per_day["symbols"].min()) if per_day.height else 0,
        "max_symbols": int(per_day["symbols"].max()) if per_day.height else 0,
        "future_update_rows": frame.filter(pl.col("update_date") > pl.col("query_date")).height,
    }
    return frame, audit


def _transition_membership(snapshots: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    rows = []
    previous: set[str] | None = None
    transition_dates = []
    for keys, group in snapshots.group_by("query_date", maintain_order=True):
        day = keys[0] if isinstance(keys, tuple) else keys
        current = set(group["symbol"].to_list())
        if previous is None or current != previous:
            rows.extend({"effective_date": day, "symbol": symbol} for symbol in sorted(current))
            transition_dates.append({"date": str(day), "members": len(current)})
        previous = current
    frame = pl.DataFrame(rows, schema={"effective_date": pl.Date, "symbol": pl.String})
    return frame, {"transition_dates": transition_dates, "transition_count": len(transition_dates)}


def _fetch_qfq(symbol: str) -> pl.DataFrame:
    import baostock as bs

    code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        result = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,isST",
            start_date="2017-06-01",
            end_date=str(END),
            frequency="d",
            adjustflag="2",
        )
        if result.error_code != "0":
            raise RuntimeError(f"history {symbol}: {result.error_code} {result.error_msg}")
        rows = []
        while result.next():
            rows.append(result.get_row_data())
    finally:
        bs.logout()
    if not rows:
        raise RuntimeError(f"history {symbol}: empty")
    return pl.DataFrame(rows, schema=result.fields, orient="row").with_columns(pl.lit(symbol).alias("symbol"))


def _ensure_prices(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    local = (
        pl.scan_parquet(
            sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet")),
            extra_columns="ignore",
        )
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= START)
            & (pl.col("date") <= END)
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
        .collect()
        .with_columns([
            pl.col("date").cast(pl.Date),
            *[pl.col(column).cast(pl.Float64) for column in ("open", "high", "low", "close", "volume", "amount")],
        ])
    )
    local_symbols = set(local["symbol"].unique().to_list())
    missing = sorted(set(symbols) - local_symbols)
    failures = []
    frames = []
    for index, symbol in enumerate(missing, start=1):
        path = PRICE_DIR / f"{symbol}.parquet"
        if not path.exists():
            try:
                _fetch_qfq(symbol).write_parquet(path)
            except Exception as exc:  # noqa: BLE001 - record and fail closed
                failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        if path.exists():
            frames.append(pl.read_parquet(path))
        if index % 25 == 0 or index == len(missing):
            print(f"missing prices {index}/{len(missing)}", flush=True)
    if frames:
        bootstrap = pl.concat(frames, how="diagonal_relaxed").select([
            "symbol", "date",
            pl.col("open").cast(pl.Float64, strict=False),
            pl.col("high").cast(pl.Float64, strict=False),
            pl.col("low").cast(pl.Float64, strict=False),
            pl.col("close").cast(pl.Float64, strict=False),
            (pl.col("volume").cast(pl.Float64, strict=False) / 100.0).alias("volume"),
            pl.col("amount").cast(pl.Float64, strict=False),
        ]).with_columns(pl.col("date").str.to_date())
    else:
        bootstrap = pl.DataFrame()
    combined = pl.concat([local, bootstrap], how="diagonal_relaxed").unique(
        subset=["symbol", "date"], keep="last"
    ).sort(["symbol", "date"])
    audit = {
        "symbols_requested": len(symbols),
        "local_symbols": len(local_symbols),
        "missing_symbols_requested": len(missing),
        "fetched_missing_symbols": len(frames),
        "failures": failures,
        "combined_rows": combined.height,
        "combined_symbols": combined["symbol"].n_unique(),
    }
    return combined, audit


def _source_parity(combined: pl.DataFrame, symbols: list[str]) -> dict:
    import baostock as bs

    rows = []
    for symbol in sorted(symbols)[:10]:
        path = PRICE_DIR / f"validation_{symbol}.parquet"
        if not path.exists():
            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
            try:
                code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
                result = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,isST",
                    start_date=str(START),
                    end_date=str(END),
                    frequency="d",
                    adjustflag="2",
                )
                got = []
                while result.next():
                    got.append(result.get_row_data())
            finally:
                bs.logout()
            pl.DataFrame(got, schema=result.fields, orient="row").with_columns(
                pl.lit(symbol).alias("symbol")
            ).write_parquet(path)
        external = pl.read_parquet(path).select([
            "symbol", "date", pl.col("close").cast(pl.Float64).alias("close_ext")
        ]).with_columns(pl.col("date").str.to_date())
        local = combined.filter(pl.col("symbol") == symbol).select(["symbol", "date", "close"])
        joined = local.join(external, on=["symbol", "date"], how="inner").sort("date")
        if joined.height < 100:
            continue
        joined = joined.with_columns([
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("r_local"),
            (pl.col("close_ext") / pl.col("close_ext").shift(1) - 1.0).alias("r_ext"),
        ]).drop_nulls(["r_local", "r_ext"])
        if joined.height < 80:
            continue
        corr = st.correlation(joined["r_local"].to_list(), joined["r_ext"].to_list())
        diff = (joined["r_local"] - joined["r_ext"]).abs()
        rows.append({
            "symbol": symbol,
            "n": joined.height,
            "corr": round(corr, 6),
            "mean_abs_diff": round(float(diff.mean()), 8),
        })
    return {
        "symbols": rows,
        "min_corr": min((row["corr"] for row in rows), default=None),
        "max_mean_abs_diff": max((row["mean_abs_diff"] for row in rows), default=None),
        "pass": bool(rows and all(row["corr"] >= 0.995 and row["mean_abs_diff"] <= 0.0005 for row in rows)),
    }


def _fetch_financial_cross_sections() -> dict:
    import akshare as ak

    FIN_DIR.mkdir(parents=True, exist_ok=True)
    dates = []
    for year in range(2017, 2021):
        for month_day in ("0331", "0630", "0930", "1231"):
            dates.append(f"{year}{month_day}")
    functions = {
        "income": ak.stock_lrb_em,
        "balance": ak.stock_zcfz_em,
        "cash": ak.stock_xjll_em,
    }
    for index, day in enumerate(dates, start=1):
        for name, function in functions.items():
            path = FIN_DIR / f"{name}_{day}.parquet"
            if not path.exists():
                function(date=day).to_parquet(path, index=False)
        print(f"financial cross-sections {index}/{len(dates)}", flush=True)
    return {"requests": len(dates) * len(functions), "periods": len(dates)}


def _financial_factors(universe: set[str]) -> tuple[pl.DataFrame, dict]:
    from app.backtest.fundamentals import _statutory_deadline

    frames = []
    for year in range(2017, 2021):
        for month_day in ("0331", "0630", "0930", "1231"):
            day = f"{year}{month_day}"
            income = pl.read_parquet(FIN_DIR / f"income_{day}.parquet").select([
                pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
                pl.col("营业总收入").cast(pl.Float64, strict=False).alias("revenue"),
                pl.col("营业总支出-营业支出").cast(pl.Float64, strict=False).alias("operating_costs"),
                pl.col("净利润").cast(pl.Float64, strict=False).alias("net_income"),
            ]).unique(subset=["code"], keep="last")
            balance = pl.read_parquet(FIN_DIR / f"balance_{day}.parquet").select([
                pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
                pl.col("资产-总资产").cast(pl.Float64, strict=False).alias("total_assets"),
                pl.col("资产-应收账款").cast(pl.Float64, strict=False).alias("accounts_receivable"),
                pl.col("资产-存货").cast(pl.Float64, strict=False).alias("inventory"),
                pl.col("负债-应付账款").cast(pl.Float64, strict=False).alias("accounts_payable"),
            ]).unique(subset=["code"], keep="last")
            cash = pl.read_parquet(FIN_DIR / f"cash_{day}.parquet").select([
                pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
                pl.col("经营性现金流-现金流量净额").cast(pl.Float64, strict=False).alias("net_operating_cash_flow"),
            ]).unique(subset=["code"], keep="last")
            merged = income.join(balance, on="code", how="full", suffix="_bal").join(
                cash, on="code", how="full", suffix="_cash"
            ).with_columns(
                pl.lit(day).str.to_date("%Y%m%d").alias("period_end")
            )
            frames.append(merged)
    raw = pl.concat(frames, how="diagonal_relaxed")
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    raw = raw.join(instruments, on="code", how="inner").filter(pl.col("symbol").is_in(universe))
    raw = raw.unique(subset=["symbol", "period_end"], keep="last").sort(["symbol", "period_end"])
    raw = raw.with_columns(
        pl.col("period_end").map_elements(_statutory_deadline, return_dtype=pl.Date).dt.offset_by("3d").alias("effective_date")
    )
    period_index = pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()
    raw = raw.with_columns([
        period_index.alias("_period_index"),
        period_index.shift(1).over("symbol").alias("_period_lag1"),
        period_index.shift(3).over("symbol").alias("_period_lag3"),
        period_index.shift(4).over("symbol").alias("_period_lag4"),
        pl.col("revenue").shift(1).over("symbol").alias("_revenue_prev"),
        pl.col("operating_costs").shift(1).over("symbol").alias("_cost_prev"),
        pl.col("net_income").shift(1).over("symbol").alias("_ni_prev"),
        pl.col("net_operating_cash_flow").shift(1).over("symbol").alias("_ocf_prev"),
        pl.col("total_assets").shift(4).over("symbol").alias("_assets_lag4"),
        pl.col("accounts_receivable").shift(4).over("symbol").alias("_receivable_lag4"),
        pl.col("inventory").shift(4).over("symbol").alias("_inventory_lag4"),
        pl.col("effective_date").shift(4).over("symbol").alias("_effective_lag4"),
    ]).with_columns([
        pl.when(pl.col("period_end").dt.quarter() == 1).then(pl.col("revenue"))
        .when((pl.col("_period_index") - pl.col("_period_lag1") == 1) & pl.col("_revenue_prev").is_not_null())
        .then(pl.col("revenue") - pl.col("_revenue_prev")).otherwise(None).alias("_q_revenue"),
        pl.when(pl.col("period_end").dt.quarter() == 1).then(pl.col("operating_costs"))
        .when((pl.col("_period_index") - pl.col("_period_lag1") == 1) & pl.col("_cost_prev").is_not_null())
        .then(pl.col("operating_costs") - pl.col("_cost_prev")).otherwise(None).alias("_q_cost"),
        pl.when(pl.col("period_end").dt.quarter() == 1).then(pl.col("net_income"))
        .when((pl.col("_period_index") - pl.col("_period_lag1") == 1) & pl.col("_ni_prev").is_not_null())
        .then(pl.col("net_income") - pl.col("_ni_prev")).otherwise(None).alias("_q_ni"),
        pl.when(pl.col("period_end").dt.quarter() == 1).then(pl.col("net_operating_cash_flow"))
        .when((pl.col("_period_index") - pl.col("_period_lag1") == 1) & pl.col("_ocf_prev").is_not_null())
        .then(pl.col("net_operating_cash_flow") - pl.col("_ocf_prev")).otherwise(None).alias("_q_ocf"),
        (pl.col("_period_index") - pl.col("_period_lag4") == 4).alias("_lag4_valid"),
    ]).with_columns([
        pl.col("_q_revenue").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_revenue"),
        pl.col("_q_cost").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_cost"),
        pl.col("_q_ni").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_ni"),
        pl.col("_q_ocf").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_ocf"),
        pl.col("effective_date").cast(pl.Int64).rolling_max(4, min_samples=4).over("symbol").alias("_ttm_effective_i"),
    ]).with_columns(
        pl.col("_ttm_effective_i").cast(pl.Date).alias("_ttm_effective")
    ).with_columns([
        pl.when((pl.col("_ttm_ni") > 0) & pl.col("_ttm_ocf").is_not_null())
        .then(pl.col("_ttm_ocf") / pl.col("_ttm_ni")).otherwise(None).alias("ocf_ni"),
        pl.when((pl.col("total_assets") > 0) & pl.col("_ttm_ni").is_not_null() & pl.col("_ttm_ocf").is_not_null())
        .then((pl.col("_ttm_ni") - pl.col("_ttm_ocf")) / pl.col("total_assets")).otherwise(None).alias("accrual"),
        pl.when(pl.col("_lag4_valid") & (pl.col("_assets_lag4") > 0) & (pl.col("total_assets") > 0))
        .then(pl.col("total_assets") / pl.col("_assets_lag4") - 1.0).otherwise(None).alias("asset_growth_yoy"),
        pl.when(
            (pl.col("_ttm_revenue") > 0) & (pl.col("_ttm_cost") > 0)
            & pl.col("accounts_receivable").is_not_null()
            & pl.col("inventory").is_not_null()
            & pl.col("accounts_payable").is_not_null()
        ).then(
            365.0 * pl.col("accounts_receivable") / pl.col("_ttm_revenue")
            + 365.0 * pl.col("inventory") / pl.col("_ttm_cost")
            - 365.0 * pl.col("accounts_payable") / pl.col("_ttm_cost")
        ).otherwise(None).alias("cash_conversion_cycle"),
    ]).with_columns([
        pl.when(pl.col("_lag4_valid"))
        .then(pl.max_horizontal("effective_date", "_effective_lag4")).otherwise(None).alias("_growth_effective"),
        pl.when(pl.col("_ttm_effective").is_not_null())
        .then(pl.max_horizontal("_ttm_effective", "effective_date")).otherwise(None).alias("_ccc_effective"),
    ])
    audit = {
        "rows": raw.height,
        "symbols": raw["symbol"].n_unique(),
        "period_min": str(raw["period_end"].min()),
        "period_max": str(raw["period_end"].max()),
        "non_null": {factor: int(raw[factor].is_not_null().sum()) for factor in FIN_EFFECTIVE},
    }
    return raw, audit


def _attach_financials(panel: pl.DataFrame, financial: pl.DataFrame) -> pl.DataFrame:
    out = panel.sort(["symbol", "date"])
    for factor, effective_column in FIN_EFFECTIVE.items():
        right = financial.select(["symbol", effective_column, factor]).filter(
            pl.col(effective_column).is_not_null()
        ).rename({effective_column: "_announce", factor: "_value"}).with_columns(
            pl.col("_announce").dt.offset_by("1d").alias("_effective")
        ).sort(["symbol", "_effective"])
        out = out.join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        ).with_columns(
            pl.when(pl.col("date") > pl.col("_announce")).then(pl.col("_value")).otherwise(None).alias(factor)
        ).drop(["_announce", "_value", "_effective"])
    return out


def _build_panel(combined: pl.DataFrame, financial: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    from app.indicators.pipeline import compute_indicators

    panel = compute_indicators(combined.sort(["symbol", "date"]), needed={"annual_vol_20d", "momentum_20d"})
    panel = _attach_financials(panel, financial)
    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    reb = reb.filter(pl.col("date") >= SAMPLE_A[0])
    return panel, reb


def _period_frame(frame: pl.DataFrame, bounds: tuple[date, date]) -> pl.DataFrame:
    return frame.filter((pl.col("date") >= bounds[0]) & (pl.col("date") <= bounds[1]))


def _coverage(frame: pl.DataFrame, column: str) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(column).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
    }


def _perm_p(values: list[float], seed: int, *, one_sided: bool) -> float | None:
    if len(values) < 8:
        return None
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(np.array([-1.0, 1.0]), size=(r42.PERMUTATIONS, len(arr)))
    means = (draws * arr).mean(axis=1)
    observed = float(arr.mean())
    if one_sided:
        return float((1 + np.sum(means >= observed)) / (r42.PERMUTATIONS + 1))
    return float((1 + np.sum(np.abs(means) >= abs(observed))) / (r42.PERMUTATIONS + 1))


def _stats(frame: pl.DataFrame, column: str, seed: int, *, one_sided: bool) -> dict:
    series = r22.ic_frame(frame, column).drop_nulls("ic")
    values = series["ic"].to_list() if not series.is_empty() else []
    if not values:
        return {
            "mean": None,
            "ir": None,
            "n_dates": 0,
            "yearly": {},
            "perm_p": None,
            "coverage": _coverage(frame, column),
        }
    mean = st.mean(values)
    std = st.pstdev(values)
    yearly: dict[int, list[float]] = {}
    for day, value in zip(series["date"].to_list(), values, strict=False):
        yearly.setdefault(day.year, []).append(value)
    return {
        "mean": round(mean, 4),
        "ir": round(mean / std, 4) if std > 0 else None,
        "n_dates": len(values),
        "yearly": {str(year): round(st.mean(items), 4) for year, items in sorted(yearly.items())},
        "perm_p": _perm_p(values, seed, one_sided=one_sided),
        "coverage": _coverage(frame, column),
    }


def _screen(reb: pl.DataFrame) -> dict[str, Any]:
    sample_a = _period_frame(reb, SAMPLE_A)
    sample_b = _period_frame(reb, SAMPLE_B)
    records = {}
    pvalues = []
    for index, (factor, good_high) in enumerate(FACTORS.items()):
        sign = 1.0 if good_high else -1.0
        a = sample_a.with_columns((pl.col(factor) * sign).alias("_signed"))
        b = sample_b.with_columns((pl.col(factor) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(factor) * sign).alias("_signed"))
        a_stats = _stats(a, "_signed", RNG_SEED + index, one_sided=False)
        b_stats = _stats(b, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = _stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        a_group = r22.offline_groups(a, "_signed", good_high=True)
        b_group = r22.offline_groups(b, "_signed", good_high=True)
        d1 = bool(
            a_stats["coverage"]["mean"] is not None and a_stats["coverage"]["mean"] >= 0.70
            and b_stats["coverage"]["mean"] is not None and b_stats["coverage"]["mean"] >= 0.70
            and a_stats["coverage"]["min_valid"] >= MIN_VALID
            and b_stats["coverage"]["min_valid"] >= MIN_VALID
        )
        f1 = bool(
            a_stats["mean"] is not None and a_stats["mean"] >= 0.03
            and b_stats["mean"] is not None and b_stats["mean"] >= 0.02
            and a_stats["mean"] > 0 and b_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 2
        )
        f2 = bool(
            a_group and b_group
            and a_group["monotonic_rho"] >= 0.7 and b_group["monotonic_rho"] >= 0.7
            and a_group["spread_annual_net2x"] > 0 and b_group["spread_annual_net2x"] > 0
        )
        records[factor] = {
            "direction": "high" if good_high else "low",
            "sample_a": a_stats,
            "sample_b": b_stats,
            "full": full_stats,
            "sample_a_groups": a_group,
            "sample_b_groups": b_group,
            "D1": d1,
            "F1": f1,
            "F2": f2,
        }
        pvalues.append(a_stats["perm_p"])
    qvalues = r42._bh_qvalues(pvalues)
    passed = []
    for factor, q in zip(FACTORS, qvalues, strict=True):
        record = records[factor]
        record["discovery_q"] = q
        record["F3"] = bool(
            q is not None and q <= 0.10
            and record["sample_b"]["perm_p"] is not None
            and record["sample_b"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(record["D1"] and record["F1"] and record["F2"] and record["F3"])
        if record["all_pass"]:
            passed.append(factor)
    return {
        "records": records,
        "passed": passed,
        "decision": {
            "D1_count": sum(bool(record["D1"]) for record in records.values()),
            "F1_count": sum(bool(record["F1"]) for record in records.values()),
            "F2_count": sum(bool(record["F2"]) for record in records.values()),
            "F3_count": sum(bool(record["F3"]) for record in records.values()),
            "all_pass_count": len(passed),
        },
    }


def _comparison_2021(records: dict) -> dict:
    r42_evidence = json.loads((EV / "r42_historical_factor_screen.json").read_text(encoding="utf-8"))
    r82_evidence = json.loads((EV / "r82_working_capital_factors.json").read_text(encoding="utf-8"))
    out = {}
    for factor in FACTORS:
        if factor in r42_evidence["factors"]:
            old = r42_evidence["factors"][factor]
            out[factor] = {"disc_ic_2021_2023": old["discovery"]["mean"], "val_ic_2024_2026": old["validation"]["mean"]}
        elif factor in r82_evidence["records"]:
            old = r82_evidence["records"][factor]
            out[factor] = {"disc_ic_2021_2023": old["discovery"]["mean"], "val_ic_2024_2026": old["validation"]["mean"]}
    return out


def main() -> int:
    days = _trading_dates(START, END)
    snapshots, membership_audit = _ensure_membership(days)
    membership, transition_audit = _transition_membership(snapshots)
    union = sorted(snapshots["symbol"].unique().to_list())
    combined, price_audit = _ensure_prices(union)
    parity = _source_parity(combined, union)
    finance_fetch = _fetch_financial_cross_sections()
    financial, financial_audit = _financial_factors(set(union))
    _panel, reb = _build_panel(combined, financial)
    from app.backtest.strategy import _hs300_panel_row_mask

    reb = reb.filter(_hs300_panel_row_mask(reb, membership).fill_null(False))
    result_screen = _screen(reb)
    result = {
        "window": {
            "start": str(START),
            "end": str(END),
            "sample_a": [str(SAMPLE_A[0]), str(SAMPLE_A[1])],
            "sample_b": [str(SAMPLE_B[0]), str(SAMPLE_B[1])],
            "factors": list(FACTORS),
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "membership": membership_audit,
            "transitions": transition_audit,
            "prices": price_audit,
            "source_parity": parity,
            "financial_fetch": finance_fetch,
            "financial": financial_audit,
            "rebalances": reb.height,
            "rebalance_dates": reb.select("date").n_unique(),
        },
        **result_screen,
        "comparison_2021_2026": _comparison_2021(result_screen["records"]),
        "adopted_for_r84": result_screen["passed"],
        "recommendation": "R84 stability integration" if result_screen["passed"] else "negative knowledge; early-sample line closed",
        "caveats": [
            "2017-2019 公告日无本地历史缓存, 财务 PIT 使用法定截止日 +3 天, 不是首次披露日",
            "历史成员来自 BaoStock 查询, 不是中证指数原始调样公告",
            "2018-2020 是独立早期样本, 但仍与 2021-2026 存在市场制度差异",
            "价格使用 qfq 收盘; 月度收益为相邻调仓日收盘收益, 未模拟 T+1 成交",
        ],
    }
    path = EV / "r83_early_holdout.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("membership", membership_audit, "transitions", transition_audit, flush=True)
    print("prices", price_audit, "parity", parity, flush=True)
    print("financial", financial_audit, "rebalances", reb.height, reb.select("date").n_unique(), flush=True)
    print("decision", result_screen["decision"], "passed", result_screen["passed"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
