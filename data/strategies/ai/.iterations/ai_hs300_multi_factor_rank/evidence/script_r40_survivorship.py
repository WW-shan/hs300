# 输入说明: R40 历史 HS300 官方成分幸存者偏差压力测试。进程内运行, 不启动 HTTP:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r40_survivorship.py
# 作用: 用 BaoStock 历史 HS300 名单 + 历史成员价格复跑 C6_IND, 并与当前名单阳性对照比较。
# 原始缓存 data/research/r40_* 不入库; 结果 JSON 同目录 r40_survivorship.json。
from __future__ import annotations

import importlib.util
import json
import shutil
import statistics as st
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
MEMBERSHIP_CACHE = DATA / "research" / "r40_membership" / "monthly_snapshots.parquet"
PRICE_CACHE = DATA / "research" / "r40_prices" / "bootstrap_missing.parquet"
ENGINE_DATA = DATA / "research" / "r40_engine_data"
RUN_CACHE = DATA / "research" / "r40_cache"
START, END = date(2021, 1, 4), date(2026, 10, 14)
D_SLEEVE_START = date(2021, 1, 4)
COST = 0.0052  # 3× 佣金/滑点往返 (0.42%) + 0.1% 卖出印花税
PHASE_TARGETS = [
    date(2022, 5, 5),
    date(2022, 11, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
    date(2024, 7, 1),
    date(2025, 1, 2),
]
R38_TARGET = {"sharpe_mean": 1.327, "annual_mean": 0.286}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")
r28 = _load("r28_combos")
r30 = _load("r30_final")
r31 = _load("r31_d1_industry")
r32 = _load("r32_pit_recheck")
r33 = _load("r33_parity_7phase")
r36 = _load("r36_execution")


def _current_members() -> list[str]:
    payload = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))
    return [m["symbol"] for m in payload["members"]]


def _baostock_login():
    import baostock as bs

    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {result.error_code} {result.error_msg}")
    return bs


def _ensure_membership_cache() -> None:
    """缺失时按月补历史官方名单; 正常研究直接读缓存。"""
    if MEMBERSHIP_CACHE.exists():
        return
    MEMBERSHIP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    bs = _baostock_login()
    rows = []
    try:
        for year in range(2021, 2027):
            for month in range(1, 13):
                if year == 2026 and month > 10:
                    continue
                query = f"{year:04d}-{month:02d}-01"
                result = bs.query_hs300_stocks(date=query)
                if result.error_code != "0":
                    raise RuntimeError(f"query_hs300_stocks {query}: {result.error_msg}")
                while result.next():
                    update_date, code, name = result.get_row_data()
                    rows.append({
                        "query_date": query,
                        "effective_date": update_date,
                        "code": code,
                        "name": name,
                    })
    finally:
        bs.logout()
    frame = pl.DataFrame(rows).with_columns([
        pl.col("query_date").str.to_date(),
        pl.col("effective_date").str.to_date(),
        pl.col("code").str.extract(r"\.(\d{6})$", 1).alias("code6"),
    ]).with_columns(
        pl.when(pl.col("code").str.starts_with("sh."))
        .then(pl.col("code6") + pl.lit(".SH"))
        .when(pl.col("code").str.starts_with("sz."))
        .then(pl.col("code6") + pl.lit(".SZ"))
        .otherwise(None)
        .alias("symbol")
    )
    frame.write_parquet(MEMBERSHIP_CACHE)


def _ensure_price_cache(symbols: list[str]) -> None:
    """补齐本地不存在的历史 member 的 BaoStock qfq/raw 日线; 分批登录防会话过期。"""
    PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    local = set(
        pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )
    existing = pl.read_parquet(PRICE_CACHE) if PRICE_CACHE.exists() else pl.DataFrame()
    done = set(existing["symbol"].unique().to_list()) if existing.height else set()
    missing = sorted(set(symbols) - local - done)
    if not missing:
        return
    frames = [existing] if existing.height else []
    try:
        for offset in range(0, len(missing), 10):
            bs = _baostock_login()
            try:
                for symbol in missing[offset:offset + 10]:
                    code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
                    for adjust, mode in (("2", "qfq"), ("3", "raw")):
                        result = bs.query_history_k_data_plus(
                            code,
                            "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,isST",
                            start_date="2020-06-01",
                            end_date=str(END),
                            frequency="d",
                            adjustflag=adjust,
                        )
                        if result.error_code != "0":
                            raise RuntimeError(
                                f"history {symbol} {mode}: {result.error_code} {result.error_msg}"
                            )
                        rows = []
                        while result.next():
                            rows.append(result.get_row_data())
                        if rows:
                            frames.append(
                                pl.DataFrame(rows, schema=result.fields, orient="row")
                                .with_columns([pl.lit(symbol).alias("symbol"), pl.lit(mode).alias("price_mode")])
                            )
            finally:
                bs.logout()
            if frames:
                pl.concat(frames, how="diagonal_relaxed").write_parquet(PRICE_CACHE)
    except Exception:
        if frames:
            pl.concat(frames, how="diagonal_relaxed").write_parquet(PRICE_CACHE)
        raise


def membership_frame() -> tuple[pl.DataFrame, dict]:
    _ensure_membership_cache()
    snapshots = pl.read_parquet(MEMBERSHIP_CACHE)
    snapshots = snapshots.with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("effective_date").cast(pl.Date),
        pl.col("symbol").cast(pl.Utf8),
    ])
    frame = snapshots.select(["effective_date", "symbol"]).unique().sort(["effective_date", "symbol"])
    per_snapshot = snapshots.group_by("query_date").agg([
        pl.len().alias("rows"),
        pl.col("symbol").n_unique().alias("symbols"),
        pl.col("effective_date").max().alias("max_effective"),
    ]).sort("query_date")
    future_leak = snapshots.filter(pl.col("effective_date") > pl.col("query_date")).height
    latest_query = per_snapshot["query_date"].max()
    latest_symbols = set(snapshots.filter(pl.col("query_date") == latest_query)["symbol"].to_list())
    current_symbols = set(_current_members())
    overlap = len(latest_symbols & current_symbols)
    per_effective = frame.group_by("effective_date").len().sort("effective_date")
    audit = {
        "snapshots": per_snapshot.height,
        "snapshot_rows_ok": bool(
            per_snapshot.height >= 60
            and per_snapshot.filter(pl.col("symbols") != 300).height == 0
        ),
        "future_leak": future_leak,
        "union_symbols": frame["symbol"].n_unique(),
        "latest_query": str(latest_query),
        "latest_overlap": overlap,
        "latest_overlap_ok": overlap == 300,
        "effective_dates": per_effective.height,
        "effective_sizes_ok": per_effective.filter(pl.col("len") != 300).height == 0,
        "per_effective": per_effective.to_dicts(),
    }
    return frame, audit


def _numeric_qfq(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns([
        pl.col("date").str.to_date(),
        pl.col("open").cast(pl.Float64, strict=False),
        pl.col("high").cast(pl.Float64, strict=False),
        pl.col("low").cast(pl.Float64, strict=False),
        pl.col("close").cast(pl.Float64, strict=False),
        pl.col("volume").cast(pl.Float64, strict=False),
        pl.col("amount").cast(pl.Float64, strict=False),
        pl.col("turn").cast(pl.Float64, strict=False),
        pl.col("tradestatus").cast(pl.Utf8),
    ])


def _numeric_raw(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns([
        pl.col("date").str.to_date(),
        pl.col("open").cast(pl.Float64, strict=False),
        pl.col("high").cast(pl.Float64, strict=False),
        pl.col("low").cast(pl.Float64, strict=False),
        pl.col("close").cast(pl.Float64, strict=False),
        pl.col("tradestatus").cast(pl.Utf8),
    ])


def load_combined_prices(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    _ensure_price_cache(symbols)
    local = (
        pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= date(2020, 6, 1))
            & (pl.col("date") <= END)
        )
        .select([
            "symbol", "date", "open", "high", "low", "close", "volume", "amount",
            "raw_close", "raw_high", "raw_low", "turnover_rate",
            "consecutive_limit_ups", "consecutive_limit_downs",
        ])
        .collect()
        .with_columns([
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Float64),
            pl.lit(None, dtype=pl.Int64).alias("quote_ts"),
            pl.lit("1").alias("trade_status"),
        ])
    )
    bootstrap = pl.read_parquet(PRICE_CACHE).unique(
        subset=["symbol", "date", "price_mode"], keep="last"
    )
    qfq = _numeric_qfq(bootstrap.filter(pl.col("price_mode") == "qfq"))
    raw = _numeric_raw(bootstrap.filter(pl.col("price_mode") == "raw"))
    qfq = qfq.select([
        "symbol", "date", "open", "high", "low", "close", "volume", "amount",
        pl.col("turn").alias("turnover_rate"), "tradestatus",
    ])
    raw = raw.select([
        "symbol", "date",
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
        pl.col("close").alias("raw_close"),
        pl.col("tradestatus").alias("_raw_status"),
    ])
    boot = (
        qfq.join(raw, on=["symbol", "date"], how="inner")
        .filter(
            pl.col("close").is_not_null()
            & pl.col("raw_close").is_not_null()
            & (pl.col("close") > 0)
            & (pl.col("raw_close") > 0)
            & (pl.col("open") > 0)
            & (pl.col("high") > 0)
            & (pl.col("low") > 0)
        )
        .with_columns([
            (pl.col("volume") / 100.0).alias("volume"),
            pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_ups"),
            pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_downs"),
            pl.lit(None, dtype=pl.Int64).alias("quote_ts"),
            pl.col("tradestatus").alias("trade_status"),
        ])
        .drop(["tradestatus", "_raw_status"])
    )
    combined = pl.concat([local, boot], how="diagonal_relaxed").unique(
        subset=["symbol", "date"], keep="last"
    ).sort(["symbol", "date"])
    local_symbols = set(local["symbol"].unique().to_list())
    boot_symbols = set(boot["symbol"].unique().to_list())
    audit = {
        "rows": combined.height,
        "symbols": combined["symbol"].n_unique(),
        "local_symbols": len(local_symbols),
        "bootstrap_symbols": len(boot_symbols),
        "bootstrap_missing": len(set(symbols) - local_symbols - boot_symbols),
        "date_min": str(combined["date"].min()),
        "date_max": str(combined["date"].max()),
        "bootstrap_trade_status_values": boot["trade_status"].value_counts().to_dicts(),
    }
    return combined, audit


def _link_engine_dirs() -> None:
    for name in (
        "instruments", "financials", "kline_daily", "kline_index_daily",
        "kline_index_enriched", "kline_etf_daily", "kline_etf_enriched",
        "instruments_etf", "instruments_index", "adj_factor", "adj_factor_etf",
        "regime_history", "mainline_history", "ext_data",
    ):
        source = DATA / name
        destination = ENGINE_DATA / name
        if source.exists() and not destination.exists():
            destination.symlink_to(source, target_is_directory=True)


def build_engine_data(combined: pl.DataFrame) -> dict:
    """写研究专用 enriched 分区 + 维表软链, 让生产回测引擎读取同一份历史面板。"""
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    _link_engine_dirs()
    manifest_path = ENGINE_DATA / "manifest.json"
    signature = {
        "rows": combined.height,
        "symbols": combined["symbol"].n_unique(),
        "date_min": str(combined["date"].min()),
        "date_max": str(combined["date"].max()),
    }
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            if old.get("signature") == signature and (ENGINE_DATA / "kline_daily_enriched").exists():
                return {"reused": True, **signature}
        except (OSError, ValueError):
            pass

    if ENGINE_DATA.exists():
        resolved = ENGINE_DATA.resolve()
        allowed = (DATA / "research").resolve()
        if allowed not in resolved.parents:
            raise RuntimeError(f"refusing to clean unexpected path: {resolved}")
        shutil.rmtree(ENGINE_DATA)
    ENGINE_DATA.mkdir(parents=True, exist_ok=True)
    target = ENGINE_DATA / "kline_daily_enriched"
    target.mkdir(parents=True, exist_ok=True)
    for group in combined.partition_by("date", maintain_order=True):
        day = group["date"][0]
        out_dir = target / f"date={day.isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        group.write_parquet(out_dir / "part.parquet")
    _link_engine_dirs()
    manifest = {"signature": signature, "engine_data": str(ENGINE_DATA)}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"reused": False, **signature}


def _market_dates(combined: pl.DataFrame) -> list[date]:
    return sorted(combined["date"].unique().to_list())


def audit_price_coverage(combined: pl.DataFrame, memberships: pl.DataFrame) -> dict:
    counts = (
        combined.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by(["symbol", "month"]).len().rename({"len": "bars"})
    )
    snap = pl.read_parquet(MEMBERSHIP_CACHE).with_columns(pl.col("query_date").cast(pl.Date))
    snap = snap.with_columns(pl.col("query_date").dt.strftime("%Y-%m").alias("month"))
    snap = snap.select(["symbol", "month"]).unique()
    market_month = (
        combined.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").agg(pl.col("date").n_unique().alias("market_days"))
    )
    joined = (
        snap.join(counts, on=["symbol", "month"], how="left")
        .with_columns(pl.col("bars").fill_null(0))
        .join(market_month, on="month", how="left")
        .with_columns(pl.min_horizontal(pl.lit(10), pl.col("market_days")).alias("required"))
    )
    coverage_ok = joined.filter(pl.col("bars") >= pl.col("required")).height / max(joined.height, 1)
    # 缺失 gap: 在官方成员-月内部按交易日计数; 已知停牌日不计入 gap。
    market_dates = _market_dates(combined)
    market_by_month: dict[str, list[date]] = {}
    for day in market_dates:
        market_by_month.setdefault(day.strftime("%Y-%m"), []).append(day)
    obs_by_month = (
        combined.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by(["symbol", "month"]).agg([
            pl.col("date").alias("observed"),
            pl.col("date").filter(pl.col("trade_status") != "1").alias("suspended"),
        ])
    )
    obs_lookup = {
        (row["symbol"], row["month"]): (set(row["observed"]), set(row["suspended"]))
        for row in obs_by_month.iter_rows(named=True)
    }
    sources = []
    if PRICE_CACHE.exists():
        sources.append(pl.read_parquet(PRICE_CACHE).filter(pl.col("price_mode") == "raw"))
    suspension_check = DATA / "research" / "r40_prices" / "suspension_check.parquet"
    if suspension_check.exists():
        sources.append(pl.read_parquet(suspension_check))
    for source in sources:
        source = source.with_columns([
            pl.col("date").str.to_date(),
            pl.col("tradestatus").cast(pl.Utf8),
            pl.col("date").str.slice(0, 7).alias("_month"),
        ]).filter(pl.col("tradestatus") != "1")
        for row in source.select(["symbol", "_month", "date"]).iter_rows(named=True):
            observed, suspended = obs_lookup.get((row["symbol"], row["_month"]), (set(), set()))
            suspended = set(suspended)
            suspended.add(row["date"])
            obs_lookup[(row["symbol"], row["_month"])] = (set(observed), suspended)
    stock_basic = DATA / "research" / "r40_prices" / "stock_basic_gap_check.parquet"
    if stock_basic.exists():
        basics = pl.read_parquet(stock_basic)
        for row in basics.iter_rows(named=True):
            out_date = row["out_date"]
            if out_date is None:
                continue
            for month, days in market_by_month.items():
                after_out = {day for day in days if day > out_date}
                if not after_out:
                    continue
                observed, suspended = obs_lookup.get((row["symbol"], month), (set(), set()))
                obs_lookup[(row["symbol"], month)] = (set(observed), set(suspended) | after_out)
    gaps = []
    for row in snap.iter_rows(named=True):
        symbol, month = row["symbol"], row["month"]
        observed, suspended = obs_lookup.get((symbol, month), (set(), set()))
        longest = run = 0
        for day in market_by_month.get(month, []):
            if day in observed or day in suspended:
                run = 0
            else:
                run += 1
                longest = max(longest, run)
        gaps.append({"symbol": symbol, "month": month, "max_gap": longest})
    max_gap = max((g["max_gap"] for g in gaps), default=0)
    return {
        "member_months": joined.height,
        "monthly_coverage": round(coverage_ok, 6),
        "max_gap": max_gap,
        "max_gap_windows": sorted([g for g in gaps if g["max_gap"] > 10], key=lambda x: -x["max_gap"])[:20],
        "pass": coverage_ok >= 0.98 and max_gap <= 10,
    }


def audit_source_parity() -> dict:
    path = DATA / "research" / "r40_prices" / "validation_current.parquet"
    if not path.exists():
        return {"pass": False, "reason": "validation_current.parquet missing"}
    value = (
        pl.read_parquet(path)
        .filter(pl.col("price_mode") == "qfq")
        .with_columns([
            pl.col("date").str.to_date(),
            *[
                pl.col(c).cast(pl.Float64, strict=False)
                for c in ("open", "high", "low", "close", "volume", "amount", "turn")
            ],
        ])
    )
    symbols = value["symbol"].unique().to_list()
    local = (
        pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & (pl.col("date") >= date(2021, 1, 1)))
        .select(["symbol", "date", "close"])
        .collect()
    )
    rows = []
    for symbol in symbols:
        left = local.filter(pl.col("symbol") == symbol).sort("date")
        right = value.filter(pl.col("symbol") == symbol).sort("date")
        joined = left.join(right, on="date", suffix="_bao").sort("date")
        if joined.height < 100:
            continue
        joined = joined.with_columns([
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("r_local"),
            (pl.col("close_bao") / pl.col("close_bao").shift(1) - 1.0).alias("r_bao"),
        ]).drop_nulls(["r_local", "r_bao"])
        diffs = (joined["r_bao"] - joined["r_local"]).abs().to_list()
        corr = st.correlation(joined["r_local"].to_list(), joined["r_bao"].to_list())
        rows.append({
            "symbol": symbol,
            "n": joined.height,
            "corr": round(corr, 6),
            "mean_abs": round(sum(diffs) / len(diffs), 8) if diffs else None,
        })
    return {
        "symbols": rows,
        "min_corr": min((r["corr"] for r in rows), default=None),
        "max_mean_abs": max((r["mean_abs"] for r in rows if r["mean_abs"] is not None), default=None),
        "pass": bool(
            rows
            and all(
                r["corr"] >= 0.995 and r["mean_abs"] is not None and r["mean_abs"] <= 0.0005
                for r in rows
            )
        ),
    }


def _prepare_d_panel(combined: pl.DataFrame) -> pl.DataFrame:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.indicators.pipeline import compute_indicators, compute_limit_signals
    from app.services.industry_map import load_industry_l1

    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet")
    panel = compute_indicators(
        combined.filter(pl.col("date") >= D_SLEEVE_START).sort(["symbol", "date"]),
        needed={"change_pct", "vol_ratio_5d"},
    )
    panel = compute_limit_signals(
        panel,
        instruments,
        needed={"signal_limit_up", "signal_limit_down"},
    )
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    panel = attach_fundamental_factors(
        panel,
        snapshot,
        ["pb_latest", "roe_latest", "debt_ratio_latest", "ocf_ni", "accrual", "ocf_debt"],
    ).with_columns([
        pl.col("pb_latest").alias("pb"),
        pl.col("roe_latest").alias("roe"),
        pl.col("debt_ratio_latest").alias("debt_to_asset_ratio"),
    ])
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    return panel.join(industry, on="symbol", how="left")


def build_d_sleeves(panel: pl.DataFrame, memberships: pl.DataFrame) -> tuple[dict, dict]:
    from app.backtest.strategy import _hs300_panel_row_mask

    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    mask = _hs300_panel_row_mask(reb, memberships)
    reb = reb.filter(mask.fill_null(False))
    d1 = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    d1_rows, d1_exec = r36.t1_gross(d1, panel)
    d4_rows, d4_exec = r36.t1_gross(d4, panel)
    d1_frame = r36.cost_series(d1_rows, COST)
    d4_frame = r36.cost_series(d4_rows, COST)
    _, d1_stats = r31.series_and_stats(d1, COST)
    _, d4_stats = r31.series_and_stats(d4, COST)
    details = {
        "D1_IND": {**d1_stats, "execution": d1_exec, "standalone": r28.stats(d1_frame)},
        "D4": {**d4_stats, "execution": d4_exec, "standalone": r28.stats(d4_frame)},
        "n_rebalances": reb.select("date").n_unique(),
    }
    return {"D1": d1_frame, "D4": d4_frame}, details


def _strategy_dirs() -> list[Path]:
    return [
        EV.parents[5] / "backend" / "app" / "strategy" / "research",
        DATA / "strategies" / "custom",
        DATA / "strategies" / "ai",
        DATA / "strategies" / "composite",
    ]


def make_service():
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestService
    from app.factors.store import load_into_registry
    from app.services.industry_map import industry_loader_for
    from app.strategy import config as strategy_config
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    load_into_registry(DATA)
    strategy_engine = StrategyEngine(
        strategy_dirs=_strategy_dirs(),
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(ENGINE_DATA))), strategy_engine)


@contextmanager
def patched_membership(membership: pl.DataFrame):
    from app.backtest import strategy as strategy_module

    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def run_v20_phases(phases: list[date], membership: pl.DataFrame, label: str) -> dict[str, pl.DataFrame]:
    from app.backtest.strategy import StrategyBacktestConfig

    service = make_service()
    outputs: dict[str, pl.DataFrame] = {}
    for phase in phases:
        cache = RUN_CACHE / f"{label}_{phase.isoformat()}.parquet"
        if cache.exists():
            outputs[str(phase)] = pl.read_parquet(cache)
            continue
        cfg = StrategyBacktestConfig(
            strategy_id="ai_hs300_multi_factor_rank",
            symbols=None,
            start=phase,
            end=END,
            params=r28.V20_PARAMS,
            overrides=r28.V20_OVERRIDES,
            matching="open_t+1",
            fees_pct=0.0006,
            stamp_tax_pct=0.001,
            slippage_bps=15.0,
            max_positions=20,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        with patched_membership(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"{label} {phase}: {result.error}")
        frame = r28.monthly_from_curve(result.equity_curve)
        frame.write_parquet(cache)
        outputs[str(phase)] = frame
        print(f"[{label}] {phase}: months={frame.height}", flush=True)
    return outputs


def build_combo_frames(phases: list[date], runs: dict[str, pl.DataFrame], d1: pl.DataFrame, d4: pl.DataFrame) -> list[pl.DataFrame]:
    return [
        r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25},
            phase,
            {
                "V20_3x_stamp": runs[str(phase)],
                # build_combo 会预先构造 B80 sleeves; 本轮权重不含 P2, 占位不参与计算。
                "P2_3x_stamp": runs[str(phase)],
            },
            d1,
            d4,
            "3x_stamp",
        )
        for phase in phases
    ]


def metrics(frames: list[pl.DataFrame]) -> dict:
    return {
        "metrics": r30.phase_metrics(frames),
        "rolling": r30.rolling_windows(frames),
        "yearly": [r28.stats(f)["yearly"] for f in frames],
    }


def main() -> int:
    memberships, membership_audit = membership_frame()
    union_symbols = sorted(memberships["symbol"].unique().to_list())
    combined, price_audit = load_combined_prices(union_symbols)
    engine_audit = build_engine_data(combined)
    coverage_audit = audit_price_coverage(combined, memberships)
    parity_audit = audit_source_parity()
    current_frame = pl.DataFrame({
        "effective_date": [date(1900, 1, 1)] * len(_current_members()),
        "symbol": _current_members(),
    }, schema={"effective_date": pl.Date, "symbol": pl.String})
    data_pass = bool(
        membership_audit["snapshot_rows_ok"]
        and membership_audit["future_leak"] == 0
        and membership_audit["latest_overlap_ok"]
        and membership_audit["effective_sizes_ok"]
        and coverage_audit["pass"]
        and parity_audit["pass"]
    )
    print("membership:", membership_audit, flush=True)
    print("prices:", price_audit, "engine:", engine_audit, "coverage:", coverage_audit, flush=True)
    print("source parity:", parity_audit, flush=True)

    phases = r33.phase_dates(PHASE_TARGETS)
    current_set = set(_current_members())
    effective_dates = sorted(memberships["effective_date"].unique().to_list())
    phase_overlap = {}
    for phase in phases:
        eligible = [d for d in effective_dates if d <= phase]
        effective = eligible[-1] if eligible else None
        members_at_phase = set(
            memberships.filter(pl.col("effective_date") == effective)["symbol"].to_list()
        ) if effective else set()
        phase_overlap[str(phase)] = {
            "effective_date": str(effective),
            "current_in_historical": len(current_set & members_at_phase),
            "current_not_in_historical": len(current_set - members_at_phase),
            "former_members_not_current": len(members_at_phase - current_set),
        }
    panel = _prepare_d_panel(combined)
    historical_runs = run_v20_phases(phases, memberships, "historical")
    current_runs = run_v20_phases(phases, current_frame, "current")
    d_hist, d_hist_details = build_d_sleeves(panel, memberships)
    d_curr, d_curr_details = build_d_sleeves(panel, current_frame)

    hist_frames = build_combo_frames(phases, historical_runs, d_hist["D1"], d_hist["D4"])
    curr_frames = build_combo_frames(phases, current_runs, d_curr["D1"], d_curr["D4"])
    hist = metrics(hist_frames)
    curr = metrics(curr_frames)
    v20_hist = {"phase_metrics": r30.phase_metrics([historical_runs[str(p)] for p in phases])}
    v20_curr = {"phase_metrics": r30.phase_metrics([current_runs[str(p)] for p in phases])}

    control_sharpe = curr["metrics"]["sharpe_mean"]
    control_annual = curr["metrics"]["annual_mean"]
    e1 = bool(
        abs(control_sharpe - R38_TARGET["sharpe_mean"]) <= 0.05
        and abs(control_annual - R38_TARGET["annual_mean"]) <= 0.02
    )
    e2 = bool(
        hist["metrics"]["sharpe_mean"] >= 1.05
        and hist["metrics"]["annual_mean"] >= 0.20
        and hist["metrics"]["mdd_mean"] >= -0.15
        and hist["rolling"]["positive_rate"] >= 0.90
        and hist["rolling"]["worst"] >= -0.08
    )
    d1_stats = d_hist_details["D1_IND"]
    d4_stats = d_hist_details["D4"]
    e3 = bool(
        15 <= d1_stats["members_mean"] <= 35
        and 15 <= d4_stats["members_mean"] <= 35
        and d1_stats["top_industry_share_mean"] <= 0.40
        and d4_stats["top_industry_share_mean"] <= 0.40
        and max(d1_stats["turnover_mean"], d4_stats["turnover_mean"]) <= 0.25
    )
    retention = hist["metrics"]["sharpe_mean"] / control_sharpe if control_sharpe else None
    e4 = bool(retention is not None and retention >= 0.70 and abs(hist["metrics"]["annual_mean"] - control_annual) <= 0.08)
    decision = {
        "D1": membership_audit["snapshot_rows_ok"],
        "D2": coverage_audit["pass"],
        "D3": parity_audit["pass"],
        "D4": membership_audit["future_leak"] == 0 and membership_audit["latest_overlap_ok"],
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4_report_only": e4,
    }
    experiment_valid = bool(data_pass and e1)
    adopted = bool(experiment_valid and e2 and e3)
    result = {
        "window": {"start": str(START), "end": str(END), "phases": [str(p) for p in phases]},
        "membership_audit": {
            **membership_audit,
            "current_symbols": len(current_set),
            "historical_only_symbols": len(set(union_symbols) - current_set),
        },
        "phase_membership": phase_overlap,
        "price_audit": price_audit,
        "price_coverage": coverage_audit,
        "source_parity": parity_audit,
        "engine_data": engine_audit,
        "historical": {"C6_IND": hist, "V20": v20_hist, "D_sleeves": d_hist_details},
        "current_control": {"C6_IND": curr, "V20": v20_curr, "D_sleeves": d_curr_details},
        "comparison": {
            "sharpe": hist["metrics"]["sharpe_mean"],
            "control_sharpe": control_sharpe,
            "sharpe_retention": round(retention, 4) if retention is not None else None,
            "annual_diff": round(hist["metrics"]["annual_mean"] - control_annual, 4),
        },
        "decision": decision,
        "experiment_valid": experiment_valid,
        "adopted": adopted,
        "final_recommendation": "C6_IND survivorship-robust" if adopted else "C6_IND research candidate; B80 default",
        "caveats": [
            "历史名单来自 BaoStock 月度快照, 不是中证交易所逐日官方调样流水",
            "行业分类为当前快照回填, 仍有行业前视偏差",
            "D1_IND/D4 的财务 PIT 使用本地快照, 价格来自跨源合并",
            "BaoStock 只在缺失 historical members 时使用; 当前 300 只仍用本地 enriched",
        ],
    }
    path = EV / "r40_survivorship.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("historical:", hist, flush=True)
    print("current:", curr, flush=True)
    print("decision:", decision, "valid:", experiment_valid, "adopted:", adopted, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
