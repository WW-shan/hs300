# 输入说明: R43 — 历史 HS300 龙虎榜事件研究。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r43_lhb_event.py
# 结果 JSON: 同目录 r43_lhb_event.json。原始缓存 data/research/r43_lhb/ (不提交)。
from __future__ import annotations

import bisect
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
DATA = r40.DATA
START, END = date(2021, 6, 1), date(2026, 9, 30)
COST_2X = 0.0028


def _details(memberships: pl.DataFrame, instruments: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames = []
    for path in sorted((DATA / "research" / "r43_lhb").glob("detail_*.parquet")):
        frames.append(pl.read_parquet(path))
    details = pl.concat(frames, how="diagonal_relaxed").with_columns([
        pl.col("代码").cast(pl.Utf8).str.zfill(6).alias("code"),
        pl.col("上榜日").cast(pl.Utf8).str.to_date(strict=False).alias("date"),
        *[
            pl.col(c).cast(pl.Float64, strict=False)
            for c in (
                "龙虎榜净买额", "龙虎榜买入额", "龙虎榜卖出额",
                "龙虎榜成交额", "市场总成交额", "净买额占总成交比",
                "成交额占总成交比", "换手率", "流通市值",
            )
        ],
    ]).join(instruments.select(["symbol", "code"]), on="code", how="left")
    details = details.filter(
        pl.col("symbol").is_not_null()
        & pl.col("date").is_not_null()
        & (pl.col("date") >= START)
        & (pl.col("date") <= END)
    )
    from app.backtest.strategy import _hs300_panel_row_mask

    details = details.filter(_hs300_panel_row_mask(details, memberships).fill_null(False))
    reasons = (
        details.group_by("上榜原因").len().sort("len", descending=True).head(20).to_dicts()
    )
    agg = details.group_by(["symbol", "date"]).agg([
        pl.col("龙虎榜净买额").sum().alias("net_buy"),
        pl.col("龙虎榜买入额").sum().alias("buy"),
        pl.col("龙虎榜卖出额").sum().alias("sell"),
        pl.col("龙虎榜成交额").sum().alias("lhb_turnover"),
        pl.col("市场总成交额").max().alias("market_turnover"),
        pl.col("流通市值").max().alias("float_mv"),
        pl.len().alias("reason_count"),
    ]).with_columns([
        pl.when(pl.col("market_turnover") > 0)
        .then(pl.col("net_buy") / pl.col("market_turnover") * 100.0)
        .otherwise(None).alias("net_buy_ratio"),
        pl.when(pl.col("market_turnover") > 0)
        .then(pl.col("lhb_turnover") / pl.col("market_turnover") * 100.0)
        .otherwise(None).alias("lhb_ratio"),
        pl.col("date").dt.year().alias("year"),
    ]).sort(["symbol", "date"])
    return agg, reasons


def _institutional(memberships: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
    frames = []
    for path in sorted((DATA / "research" / "r43_lhb").glob("jgmm_*.parquet")):
        frames.append(pl.read_parquet(path))
    raw = pl.concat(frames, how="diagonal_relaxed").with_columns([
        pl.col("代码").cast(pl.Utf8).str.zfill(6).alias("code"),
        pl.col("上榜日期").cast(pl.Utf8).str.to_date(strict=False).alias("date"),
        *[
            pl.col(c).cast(pl.Float64, strict=False)
            for c in ("机构买入净额", "机构买入总额", "机构卖出总额", "市场总成交额", "机构净买额占总成交额比")
        ],
    ]).join(instruments.select(["symbol", "code"]), on="code", how="left")
    raw = raw.filter(
        pl.col("symbol").is_not_null()
        & pl.col("date").is_not_null()
        & (pl.col("date") >= START)
        & (pl.col("date") <= END)
    )
    from app.backtest.strategy import _hs300_panel_row_mask

    raw = raw.filter(_hs300_panel_row_mask(raw, memberships).fill_null(False))
    return raw.group_by(["symbol", "date"]).agg([
        pl.col("机构买入净额").sum().alias("inst_net_buy"),
        pl.col("机构买入总额").sum().alias("inst_buy"),
        pl.col("机构卖出总额").sum().alias("inst_sell"),
        pl.col("市场总成交额").max().alias("market_turnover"),
    ]).with_columns(
        pl.when(pl.col("market_turnover") > 0)
        .then(pl.col("inst_net_buy") / pl.col("market_turnover") * 100.0)
        .otherwise(None).alias("inst_ratio")
    ).sort(["symbol", "date"])


def _daily_benchmark(combined: pl.DataFrame, memberships: pl.DataFrame) -> dict[date, float]:
    from app.backtest.strategy import _hs300_panel_row_mask

    panel = combined.select(["symbol", "date", "open"]).sort(["symbol", "date"])
    panel = panel.with_columns(pl.col("open").shift(-1).over("symbol").alias("next_open"))
    dates = sorted(panel["date"].unique().to_list())
    member_panel = panel.filter(_hs300_panel_row_mask(panel, memberships).fill_null(False))
    market = (
        member_panel.filter(
            pl.col("next_open").is_not_null()
            & (pl.col("open") > 0)
            & (pl.col("next_open") > 0)
        )
        .with_columns((pl.col("next_open") / pl.col("open") - 1.0).alias("r"))
        .group_by("date").agg(pl.col("r").mean().alias("mkt_r"))
        .sort("date")
    )
    bench = dict(zip(market["date"].to_list(), market["mkt_r"].to_list(), strict=False))
    return bench


def _series_and_bench(combined: pl.DataFrame, memberships: pl.DataFrame):
    from app.backtest.strategy import _hs300_panel_row_mask

    panel = combined.select(["symbol", "date", "open"]).sort(["symbol", "date"])
    bench = _daily_benchmark(combined, memberships)
    series = {}
    for keys, group in panel.group_by("symbol", maintain_order=True):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        series[symbol] = (group["date"].to_list(), group["open"].to_list())
    # 交易日轴与基准完全一致
    dates = sorted(panel["date"].unique().to_list())
    return series, bench, dates


def _deoverlap(frame: pl.DataFrame, dates: list[date], column: str) -> tuple[pl.DataFrame, int]:
    date_index = {day: index for index, day in enumerate(dates)}
    rows = []
    last = {}
    for row in frame.sort(["symbol", "date"]).iter_rows(named=True):
        symbol = row["symbol"]
        idx = date_index.get(row["date"])
        if idx is None:
            continue
        if symbol in last and idx - last[symbol] < 20:
            continue
        last[symbol] = idx
        row["_date_index"] = idx
        rows.append(row)
    return pl.DataFrame(rows), len(frame)


def _quintiles(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    return frame.with_columns(
        ((pl.col(column).rank("ordinal") - 1.0) * 5.0 / pl.len()).floor().clip(0, 4).cast(pl.Int64).alias("q")
    )


def _event_stats(frame: pl.DataFrame, car_column: str) -> list[dict]:
    rows = []
    for q in range(5):
        values = frame.filter(pl.col("q") == q)[car_column].drop_nulls().to_list()
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


def _gate(stats: list[dict], *, top_median_min: float, top_t_min: float) -> dict:
    means = [row["mean"] for row in stats]
    top = stats[-1]
    monotonic = bool(all(v is not None for v in means) and all(means[i] <= means[i + 1] for i in range(4)))
    return {
        "top_median": top["median"],
        "top_t": top["t"],
        "monotonic": monotonic,
        "pass": bool(
            top["median"] is not None and top["median"] > top_median_min
            and top["t"] is not None and top["t"] > top_t_min and monotonic
        ),
    }


def main() -> int:
    memberships, _ = r40.membership_frame()
    symbols = sorted(memberships["symbol"].unique().to_list())
    combined, _ = r40.load_combined_prices(symbols)
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    events, reason_dist = _details(memberships, instruments)
    inst = _institutional(memberships, instruments)
    series, bench, dates = _series_and_bench(combined, memberships)
    print("events", events.height, "institutional", inst.height, "reasons", reason_dist[:3], flush=True)

    merged = events.join(
        inst.select(["symbol", "date", "inst_ratio"]),
        on=["symbol", "date"],
        how="left",
    )
    deov, raw_n = _deoverlap(merged, dates, "net_buy_ratio")
    deov_inst, raw_inst_n = _deoverlap(
        merged.filter(pl.col("inst_ratio").is_not_null()), dates, "inst_ratio"
    )

    def add_car(frame: pl.DataFrame) -> pl.DataFrame:
        rows = []
        for row in frame.iter_rows(named=True):
            item = series.get(row["symbol"])
            if not item:
                continue
            sym_dates, opens = item
            i = bisect.bisect_right(sym_dates, row["date"])
            out = dict(row)
            for horizon in (5, 20):
                if i + horizon >= len(sym_dates):
                    out[f"car{horizon}"] = None
                    continue
                stock = opens[i + horizon] / opens[i] - 1.0
                bench_ret = 1.0
                for day in sym_dates[i:i + horizon]:
                    bench_ret *= 1.0 + bench.get(day, 0.0)
                out[f"car{horizon}"] = stock - (bench_ret - 1.0)
            rows.append(out)
        return pl.DataFrame(rows)

    deov = _quintiles(add_car(deov), "net_buy_ratio")
    deov_inst = _quintiles(add_car(deov_inst), "inst_ratio")
    primary = {}
    for horizon in (5, 20):
        stats = _event_stats(deov, f"car{horizon}")
        primary[str(horizon)] = {"by_quintile": stats, **_gate(stats, top_median_min=0.0, top_t_min=2.0)}
    inst_stats = _event_stats(deov_inst, "car20")
    inst_gate = _gate(inst_stats, top_median_min=0.0, top_t_min=1.5)

    q5 = deov.filter(pl.col("q") == 4).drop_nulls(["car20"])
    yearly = {}
    for year in range(2021, 2027):
        values = q5.filter(pl.col("year") == year)["car20"].to_list()
        yearly[str(year)] = {
            "n": len(values),
            "median": round(st.median(values), 5) if values else None,
            "mean": round(st.mean(values), 5) if values else None,
        }
    positive_years = sum(1 for value in yearly.values() if value["median"] is not None and value["median"] > 0)
    q5_median = st.median(q5["car20"].to_list()) if q5.height else None
    e3 = positive_years >= 4
    e4 = bool(q5_median is not None and q5_median - COST_2X > 0)
    d1 = bool(
        events.height >= 1000
        and deov.height >= 300
        and events["date"].dt.strftime("%Y-%m").n_unique() >= 48
        and events["net_buy_ratio"].is_not_null().mean() == 1.0
    )
    e1 = bool(primary["20"]["pass"] and primary["5"]["pass"])
    e2 = bool(inst_gate["pass"])
    decision = {"D1": d1, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "all_pass": bool(d1 and e1 and e2 and e3 and e4)}
    result = {
        "window": {"start": str(START), "end": str(END)},
        "data": {
            "raw_events": events.height,
            "dedup_events": deov.height,
            "raw_institutional_events": inst.height,
            "dedup_institutional_events": deov_inst.height,
            "months": events["date"].dt.strftime("%Y-%m").n_unique(),
            "symbols": events["symbol"].n_unique(),
            "net_ratio_coverage": round(float(events["net_buy_ratio"].is_not_null().mean()), 6),
            "monthly_events": events.group_by("date").len()["len"].describe().to_dicts(),
        },
        "importance": {
            "net_buy_ratio": "sum net buy / max market turnover * 100",
            "inst_ratio": "sum institutional net buy / max market turnover * 100",
            "entry": "T+1 open",
            "exit": "T+1+horizon open",
            "benchmark": "historical HS300 equal-weight open-to-open",
            "deoverlap": "one event per symbol per rolling 20 trading days",
        },
        "primary_net_buy": primary,
        "institutional": {"by_quintile": inst_stats, **inst_gate},
        "q5_yearly_car20": yearly,
        "q5_median_car20": round(q5_median, 5) if q5_median is not None else None,
        "q5_cost_adjusted_median_car20": round(q5_median - COST_2X, 5) if q5_median is not None else None,
        "positive_q5_years": positive_years,
        "decision": decision,
        "recommendation": "R44 event combo candidate" if decision["all_pass"] else "negative knowledge; do not deploy",
        "reason_distribution": reason_dist,
        "caveats": [
            "龙虎榜 only includes extreme-event days, not a complete institutional holdings view",
            "event t-statistics are naive and ignore cross-sectional correlation across same-day events",
            "historical membership is monthly BaoStock snapshot, not daily official adjustment",
            "transaction cost is a fixed 0.28% round-trip, no market impact model",
        ],
    }
    path = EV / "r43_lhb_event.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("primary", primary, flush=True)
    print("institutional", inst_gate, "yearly", yearly, "q5 median", q5_median, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
