# 输入说明: R39 外部/另类数据初筛。必须进程内运行, 不启动 HTTP 回测:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r39_external.py
# 结果 JSON: 同目录 r39_external.json。原始数据缓存: data/research/external_r39/ (不提交)。
# 预注册见 LEDGER R39: 只测回购事件、年度股息率、大宗交易量/折溢率, 不调分位/窗口。
from __future__ import annotations

import bisect
import calendar
import importlib.util
import json
import signal
import statistics as st
from collections.abc import Callable
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "external_r39"
START, END = date(2021, 6, 1), date(2026, 10, 14)
DIV_YEARS = tuple(range(2020, 2026))
DZ_START, DZ_END = "2023-09", "2026-09"
DZ_MONTHS = 37
ROUND_TRIP_2X = 0.0028
V2_LEGS = ("leg_low_amount", "leg_small_mv", "leg_low_vol")
VALUE_FACTORS = ("pb_latest", "ep")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r34 = _load("r34_financial_rescreen")


class _AlarmTimeoutError(Exception):
    pass


def _fetch_with_timeout(label: str, fn: Callable[[], object], seconds: int = 120):
    """AkShare 某些接口不暴露 timeout; 用进程级 alarm 防止研究脚本无限等待。"""
    if not hasattr(signal, "SIGALRM"):
        return fn()

    def _alarm(_signum, _frame):
        raise _AlarmTimeoutError(label)

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(seconds)
    try:
        return fn()
    finally:
        signal.alarm(0)


def _month_ranges(start: str, end: str) -> list[tuple[int, int]]:
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _ensure_cache() -> None:
    """缓存缺失时补齐; 已存在文件直接复用, 避免重复网络请求。"""
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    repurchase_path = CACHE / "repurchase.parquet"
    if not repurchase_path.exists():
        frame = _fetch_with_timeout("stock_repurchase_em", ak.stock_repurchase_em)
        frame.to_parquet(repurchase_path, index=False)

    for year in DIV_YEARS:
        path = CACHE / f"dividend_{year}.parquet"
        if path.exists():
            continue
        frame = _fetch_with_timeout(
            f"stock_fhps_em:{year}",
            lambda y=year: ak.stock_fhps_em(date=f"{y}1231"),
        )
        frame.to_parquet(path, index=False)

    for year, month in _month_ranges(DZ_START, DZ_END):
        path = CACHE / f"dzjy_{year:04d}-{month:02d}.parquet"
        if path.exists():
            continue
        start = f"{year:04d}{month:02d}01"
        end = f"{year:04d}{month:02d}{calendar.monthrange(year, month)[1]:02d}"
        frame = _fetch_with_timeout(
            f"stock_dzjy_mrmx:{year}-{month:02d}",
            lambda s=start, e=end: ak.stock_dzjy_mrmx(symbol="A股", start_date=s, end_date=e),
        )
        frame.to_parquet(path, index=False)


def _members_and_instruments() -> tuple[list[str], pl.DataFrame]:
    payload = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))
    symbols = [m["symbol"] for m in payload["members"]]
    instruments = (
        pl.read_parquet(DATA / "instruments" / "instruments.parquet")
        .select(["symbol", "code", "float_shares"])
        .with_columns(pl.col("code").cast(pl.Utf8).str.zfill(6))
    )
    return symbols, instruments


def _market_panel(symbols: list[str], instruments: pl.DataFrame) -> pl.DataFrame:
    """读取原始收盘价和 PIT 流通股本, 用于真实市值分母。"""
    from app.share_capital import apply_historical_float_shares, load_share_history

    panel = (
        pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= date(2021, 1, 4))
            & (pl.col("date") <= END)
        )
        .select(["symbol", "date", "raw_close"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
        .join(instruments.select(["symbol", "float_shares"]), on="symbol", how="left")
    )
    panel = apply_historical_float_shares(
        panel,
        load_share_history(DATA),
        today=date.today(),
    )
    return panel.with_columns((pl.col("raw_close") * pl.col("float_shares")).alias("float_mv"))


def _event_panel(panel: pl.DataFrame) -> tuple[dict[str, tuple[list[date], list[float]]], dict[date, float]]:
    px = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"])
    px = px.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret"))
    market = px.group_by("date").agg(pl.col("ret").mean().alias("mkt")).sort("date")
    market_returns = dict(zip(market["date"].to_list(), market["mkt"].to_list(), strict=False))
    series: dict[str, tuple[list[date], list[float]]] = {}
    for keys, group in px.group_by("symbol", maintain_order=True):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        series[symbol] = (group["date"].to_list(), group["close"].to_list())
    return series, market_returns


def _quintile(values: pl.DataFrame, column: str) -> pl.DataFrame:
    return values.with_columns(
        (
            (pl.col(column).rank("ordinal") - 1.0) * 5.0 / pl.len()
        ).floor().clip(0, 4).cast(pl.Int64).alias("q")
    )


def _group_stats(frame: pl.DataFrame, value_column: str) -> list[dict]:
    rows = []
    for q in range(5):
        values = frame.filter(pl.col("q") == q)[value_column].drop_nulls().to_list()
        if not values:
            rows.append({"q": q, "n": 0, "mean": None, "median": None, "t": None})
            continue
        mean = st.mean(values)
        sd = st.pstdev(values)
        rows.append({
            "q": q,
            "n": len(values),
            "mean": round(mean, 5),
            "median": round(st.median(values), 5),
            "t": round(mean / (sd / len(values) ** 0.5), 2) if sd > 0 else None,
        })
    return rows


def _event_gate(rows: list[dict]) -> dict:
    by_q = {row["q"]: row for row in rows}
    means = [by_q[q]["mean"] for q in range(5)]
    top = by_q[4]
    monotonic = bool(all(v is not None for v in means) and all(means[i] <= means[i + 1] for i in range(4)))
    top_median = top["median"]
    top_t = top["t"]
    return {
        "by_quintile": rows,
        "top_median": top_median,
        "top_t": top_t,
        "monotonic": monotonic,
        "pass": bool(top_median is not None and top_median > 0 and top_t is not None and top_t > 2 and monotonic),
    }


def repurchase_study(rep: pl.DataFrame, instruments: pl.DataFrame, panel: pl.DataFrame, symbols: list[str]) -> dict:
    events = (
        rep.with_columns([
            pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("最新公告日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("announce"),
            pl.col("计划回购金额区间-上限").cast(pl.Float64, strict=False).alias("amount_cap"),
            pl.col("回购起始时间").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("start_proxy"),
        ])
        .join(instruments.select(["symbol", "code"]), on="code", how="left")
        .filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("announce").is_not_null()
            & (pl.col("announce") >= START)
            & (pl.col("announce") <= END)
            & (pl.col("amount_cap") > 0)
        )
        .group_by(["symbol", "announce"])
        .agg([
            pl.col("amount_cap").max().alias("amount_cap"),
            pl.col("start_proxy").max().alias("start_proxy"),
        ])
        .sort(["symbol", "announce"])
    )
    market = _market_panel(symbols, instruments).sort(["symbol", "date"])
    events = events.join_asof(
        market.select(["symbol", "date", "raw_close", "float_mv"]),
        left_on="announce",
        right_on="date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    events = (
        events.filter(pl.col("float_mv") > 0)
        .with_columns((pl.col("amount_cap") / pl.col("float_mv")).alias("intensity"))
        .filter(pl.col("intensity").is_not_null())
        .with_columns(
            (
                (pl.col("intensity").rank("ordinal") - 1.0) * 5.0 / pl.len()
            ).floor().clip(0, 4).cast(pl.Int64).alias("q")
        )
    )

    series, market_returns = _event_panel(panel)
    rows = []
    for symbol, announce, intensity in events.select(["symbol", "announce", "intensity"]).iter_rows():
        item = series.get(symbol)
        if not item:
            continue
        dates, closes = item
        i = bisect.bisect_right(dates, announce)
        for horizon in (20, 60):
            if i + horizon >= len(dates):
                continue
            stock = closes[i + horizon] / closes[i] - 1.0
            bench = 1.0
            for j in range(i + 1, i + horizon + 1):
                bench *= 1.0 + market_returns.get(dates[j], 0.0)
            rows.append({
                "symbol": symbol,
                "announce": announce,
                "horizon": horizon,
                "car": stock - (bench - 1.0),
                "intensity": intensity,
                "q": int(events.filter(
                    (pl.col("symbol") == symbol) & (pl.col("announce") == announce)
                )["q"][0]),
            })
    if not rows:
        return {"n_events": events.height, "n_car": 0, "gate": {"pass": False}}
    car = pl.DataFrame(rows)
    gates = {str(h): _event_gate(_group_stats(car.filter(pl.col("horizon") == h), "car")) for h in (20, 60)}
    return {
        "n_events": events.height,
        "n_unique_symbol_date": events.height,
        "n_car_20": car.filter(pl.col("horizon") == 20).height,
        "n_car_60": car.filter(pl.col("horizon") == 60).height,
        "intensity": {
            "median": round(float(events["intensity"].median()), 8),
            "p90": round(float(events["intensity"].quantile(0.9)), 8),
        },
        "gates": gates,
        "pass": bool(gates["20"]["pass"] and gates["60"]["pass"]),
    }


def dividend_data(instruments: pl.DataFrame, symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    for year in DIV_YEARS:
        path = CACHE / f"dividend_{year}.parquet"
        if not path.exists():
            raise RuntimeError(f"缺少分红缓存: {path}")
        frame = pl.read_parquet(path).with_columns([
            pl.col("代码").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("现金分红-股息率").cast(pl.Float64, strict=False).alias("yield"),
            pl.col("预案公告日").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("announce"),
            pl.lit(year).alias("report_year"),
        ])
        frames.append(frame.join(instruments.select(["symbol", "code"]), on="code", how="left"))
    data = pl.concat(frames, how="diagonal_relaxed").filter(
        pl.col("symbol").is_in(symbols) & pl.col("announce").is_not_null() & pl.col("yield").is_not_null()
    )
    coverage = {}
    for year in DIV_YEARS:
        n = data.filter((pl.col("report_year") == year) & pl.col("yield").is_not_null())["symbol"].n_unique()
        coverage[str(year)] = {
            "n_symbols": n,
            "coverage": round(n / len(symbols), 4),
            "pass": n / len(symbols) >= 0.8,
        }
    years_pass = sum(v["pass"] for v in coverage.values())
    return (
        data.select(["symbol", "announce", "yield", "report_year"]).sort(["symbol", "announce"]),
        {"years": coverage, "n_years_pass": years_pass, "pass": years_pass >= 4},
    )


def attach_dividend(reb: pl.DataFrame, dividend: pl.DataFrame) -> pl.DataFrame:
    right = (
        dividend.with_columns(pl.col("announce").dt.offset_by("1d").alias("_effective"))
        .select(["symbol", "_effective", "yield", "report_year"])
        .sort(["symbol", "_effective", "report_year"])
        .unique(subset=["symbol", "_effective"], keep="last")
        .sort(["symbol", "_effective"])
    )
    out = (
        reb.sort(["symbol", "date"])
        .join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .drop("_effective")
        .rename({"yield": "dividend_yield"})
    )
    return out


def block_data(instruments: pl.DataFrame, symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    for year, month in _month_ranges(DZ_START, DZ_END):
        path = CACHE / f"dzjy_{year:04d}-{month:02d}.parquet"
        if not path.exists():
            raise RuntimeError(f"缺少大宗缓存: {path}")
        frame = pl.read_parquet(path).with_columns([
            pl.col("证券代码").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("交易日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("trade_date"),
            pl.col("成交额").cast(pl.Float64, strict=False).alias("amount"),
            pl.col("折溢率").cast(pl.Float64, strict=False).alias("premium"),
            pl.lit(f"{year:04d}-{month:02d}").alias("month"),
        ])
        frames.append(frame.join(instruments.select(["symbol", "code"]), on="code", how="left"))
    raw = pl.concat(frames, how="diagonal_relaxed").filter(
        pl.col("symbol").is_in(symbols) & pl.col("amount").is_not_null() & (pl.col("amount") > 0)
    )
    monthly = raw.group_by("month").agg(
        pl.len().alias("records"),
        pl.col("symbol").n_unique().alias("symbols"),
    ).sort("month")
    min_records = int(monthly["records"].min()) if monthly.height else 0
    return raw, {
        "months": monthly.height,
        "min_records": min_records,
        "min_symbols": int(monthly["symbols"].min()) if monthly.height else 0,
        "monthly": monthly.to_dicts(),
        "pass": monthly.height >= DZ_MONTHS and min_records >= 50,
    }


def attach_block(reb: pl.DataFrame, raw: pl.DataFrame, market: pl.DataFrame) -> pl.DataFrame:
    agg = (
        raw.group_by(["month", "symbol"])
        .agg([
            pl.col("amount").sum().alias("block_amount"),
            (pl.col("amount") * pl.col("premium")).sum().alias("_premium_num"),
            pl.col("amount").filter(pl.col("premium").is_not_null()).sum().alias("_premium_den"),
        ])
        .with_columns(
            pl.when(pl.col("_premium_den") > 0)
            .then(pl.col("_premium_num") / pl.col("_premium_den"))
            .otherwise(None)
            .alias("block_premium")
        )
        .drop(["_premium_num", "_premium_den"])
    )
    month_end = (
        market.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .sort(["symbol", "month", "date"])
        .group_by(["symbol", "month"], maintain_order=True)
        .tail(1)
        .select(["symbol", "month", "float_mv"])
    )
    agg = agg.join(month_end, on=["symbol", "month"], how="left").with_columns(
        pl.when(pl.col("float_mv") > 0)
        .then(pl.col("block_amount") / pl.col("float_mv"))
        .otherwise(None)
        .alias("block_turnover")
    ).drop("float_mv")

    block_reb = (
        reb.filter((pl.col("date") >= date(2023, 10, 1)) & (pl.col("date") <= date(2026, 9, 30)))
        .with_columns(pl.col("date").dt.offset_by("-1mo").dt.strftime("%Y-%m").alias("month"))
        .join(agg, on=["month", "symbol"], how="left")
        .filter(pl.col("block_amount") > 0)
    )
    return block_reb


def factor_gate(reb: pl.DataFrame, name: str, good_high: bool) -> dict:
    ic = r22.ic_summary(reb, name)
    grp = r22.offline_groups(reb, name, good_high=good_high)
    corr_v2 = max(
        (v for v in (r22.mean_abs_corr(reb, name, leg) for leg in V2_LEGS) if v is not None),
        default=None,
    )
    corr_value = max(
        (v for v in (r22.mean_abs_corr(reb, name, other) for other in VALUE_FACTORS) if v is not None),
        default=None,
    )
    rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
    f1 = bool(ic and abs(ic["ic_mean"]) >= 0.03 and r22.yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4)
    f2 = bool(rho_ok and grp and grp["spread_month"] > 0)
    f3 = bool(grp and grp["spread_annual_net2x"] is not None and grp["spread_annual_net2x"] > 0)
    f4 = bool(corr_v2 is not None and corr_v2 < 0.7)
    f6 = bool(corr_value is not None and corr_value < 0.7)
    return {
        "direction": "high" if good_high else "low",
        "coverage": round(float(reb[name].is_not_null().mean()), 4),
        "F1_ic": f1,
        "F1_ic_mean": ic["ic_mean"] if ic else None,
        "F1_yearly": ic["yearly"] if ic else None,
        "F2_monotonic_spread": f2,
        "F2_monotonic_rho": grp["monotonic_rho"] if grp else None,
        "F2_group_monthly": grp["group_monthly"] if grp else None,
        "F2_spread_month": grp["spread_month"] if grp else None,
        "F3_spread_2x": f3,
        "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
        "F4_max_abs_corr_v2": corr_v2,
        "F4_ok": f4,
        "F6_max_abs_corr_value": corr_value,
        "F6_ok": f6,
        "all_pass": bool(f1 and f2 and f3 and f4 and f6),
    }


def main() -> int:
    _ensure_cache()
    symbols, instruments = _members_and_instruments()
    panel, reb, _snapshot, _symbols = r34.build_panel()
    market = _market_panel(symbols, instruments)
    rep = pl.read_parquet(CACHE / "repurchase.parquet")
    dividend, dividend_feasibility = dividend_data(instruments, symbols)
    raw_block, block_feasibility = block_data(instruments, symbols)

    repurchase = repurchase_study(rep, instruments, panel, symbols)
    dividend_reb = attach_dividend(reb, dividend)
    block_reb = attach_block(reb, raw_block, market)
    factor_gates = {
        "dividend_yield": factor_gate(dividend_reb, "dividend_yield", good_high=True),
        "block_turnover": factor_gate(block_reb, "block_turnover", good_high=False),
        "block_premium": factor_gate(block_reb, "block_premium", good_high=True),
    }
    feasibility = {
        "D1_repurchase": {
            "n_events": repurchase["n_events"],
            "pass": repurchase["n_events"] >= 300,
        },
        "D2_dividend": dividend_feasibility,
        "D3_block": block_feasibility,
    }
    adopted = []
    if feasibility["D1_repurchase"]["pass"] and repurchase.get("pass"):
        adopted.append("repurchase_event")
    for name, gate in factor_gates.items():
        if gate["all_pass"]:
            adopted.append(name)
    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(r22.monthly_pairs(panel))},
        "feasibility": feasibility,
        "repurchase": repurchase,
        "factor_gates": factor_gates,
        "block_sample": {
            "n_rebalances": block_reb["date"].n_unique(),
            "n_symbol_rows": block_reb.height,
            "direction_note": "block_turnover low expected; block_premium high expected",
        },
        "adopted_for_r40": adopted,
        "decision": {
            "repurchase": repurchase.get("pass", False),
            "dividend_yield": factor_gates["dividend_yield"]["all_pass"],
            "block_turnover": factor_gates["block_turnover"]["all_pass"],
            "block_premium": factor_gates["block_premium"]["all_pass"],
        },
    }
    path = EV / "r39_external.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {path}", flush=True)
    print(f"feasibility: {json.dumps(feasibility, ensure_ascii=False)}", flush=True)
    for name, gate in factor_gates.items():
        print(
            f"{name}: IC={gate['F1_ic_mean']} cov={gate['coverage']} "
            f"F1={gate['F1_ic']} F2={gate['F2_monotonic_spread']} F3={gate['F3_spread_2x']} "
            f"F4={gate['F4_max_abs_corr_v2']} F6={gate['F6_max_abs_corr_value']} "
            f"all={gate['all_pass']}",
            flush=True,
        )
    print("repurchase:", {k: repurchase.get(k) for k in ("n_events", "n_car_20", "n_car_60", "pass")}, flush=True)
    print("adopted:", adopted, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
