# 输入说明: 进程内运行 (无需后端, 不读网络)。
#   运行: cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r23_screen.py
# 作用: R23 预注册筛查 — 盈利质量/成长/安全 + PEAD 事件研究 + 组合 D1~D4。
# 结果 JSON: 同目录 r23_quality_growth.json。
# 数据口径: 累计值(YTD)按同财年差分还原单季; 流量指标 TTM 化; 有效期 = max(所涉表 announce_date)+1 交易日。
import bisect
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
START, END = date(2021, 6, 1), date(2026, 10, 14)
ROUND_TRIP_2X = 0.0028

_spec = importlib.util.spec_from_file_location("r22", EV / "script_r22_scan.py")
r22 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r22)

DIRECTION = {
    "roe_std8": False,
    "ocf_ni": True,
    "accrual": False,
    "ocf_debt": True,
    "int_cover": True,
    "rev_accel": True,
    "ni_accel": True,
    "gm_yoy": True,
}

FLOWS = ("revenue", "net_income", "total_profit", "financial_expenses", "net_operating_cash_flow")


def load_panel(symbols: list[str]) -> pl.DataFrame:
    lf = pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
    panel = (
        lf.filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= date(2021, 1, 4))
            & (pl.col("date") <= END)
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
    )
    from app.indicators.pipeline import compute_indicators

    return compute_indicators(panel, needed={"annual_vol_20d"})


def read_table(symbols: list[str], table: str, cols: list[str]) -> pl.DataFrame:
    df = pl.read_parquet(DATA / "financials" / table / "part.parquet")
    return (
        df.filter(pl.col("symbol").is_in(symbols))
        .select(["symbol", "period_end", "announce_date", *cols])
        .with_columns([
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("announce"),
        ])
        .select(["symbol", "period_end", "announce", *cols])
    )


def merge_tables(symbols: list[str]) -> pl.DataFrame:
    frames = [
        read_table(symbols, "metrics", ["bps", "roe", "gross_margin", "net_margin", "revenue", "revenue_yoy", "net_income", "net_income_yoy"]),
        read_table(symbols, "income", ["total_profit", "financial_expenses", "operating_profit"]),
        read_table(symbols, "balance_sheet", ["total_assets", "total_liabilities", "total_equity", "debt_to_asset_ratio"]),
        read_table(symbols, "cash_flow", ["net_operating_cash_flow"]),
    ]
    out = frames[0]
    for i, frame in enumerate(frames[1:], start=1):
        suffix = f"_t{i}"
        out = out.join(frame, on=["symbol", "period_end"], how="full", suffix=suffix)
        out = out.with_columns(
            pl.max_horizontal("announce", f"announce{suffix}").alias("announce")
        ).drop(f"announce{suffix}")
    return out.sort(["symbol", "period_end"])


def to_quarterly(df: pl.DataFrame) -> pl.DataFrame:
    year = pl.col("period_end").dt.year()
    prev_year = year.shift(1).over("symbol")
    exprs = []
    for col in FLOWS:
        diff = pl.col(col) - pl.col(col).shift(1).over("symbol")
        exprs.append(
            pl.when((year == prev_year) & diff.is_not_null())
            .then(diff)
            .otherwise(pl.col(col))
            .alias(f"q_{col}")
        )
    return df.with_columns(exprs).with_columns([
        pl.col(f"q_{c}").rolling_sum(window_size=4, min_periods=4).over("symbol").alias(f"ttm_{c}")
        for c in FLOWS
    ])


def derived(df: pl.DataFrame) -> pl.DataFrame:
    exprs = [
        (pl.col("q_net_income") / pl.col("total_equity")).alias("q_roe"),
        pl.when(pl.col("ttm_net_income") > 0)
        .then(pl.col("ttm_net_operating_cash_flow") / pl.col("ttm_net_income"))
        .otherwise(None)
        .alias("ocf_ni"),
        ((pl.col("ttm_net_income") - pl.col("ttm_net_operating_cash_flow")) / pl.col("total_assets"))
        .alias("accrual"),
        pl.when(pl.col("total_liabilities") > 0)
        .then(pl.col("ttm_net_operating_cash_flow") / pl.col("total_liabilities"))
        .otherwise(None)
        .alias("ocf_debt"),
        pl.when(pl.col("ttm_financial_expenses").abs() > 0.01 * pl.col("ttm_total_profit").abs())
        .then(pl.col("ttm_total_profit") / pl.col("ttm_financial_expenses").abs())
        .otherwise(None)
        .alias("int_cover"),
        (pl.col("revenue_yoy") - pl.col("revenue_yoy").shift(1).over("symbol")).alias("rev_accel"),
        (pl.col("net_income_yoy") - pl.col("net_income_yoy").shift(1).over("symbol")).alias("ni_accel"),
        (pl.col("gross_margin") - pl.col("gross_margin").shift(4).over("symbol")).alias("gm_yoy"),
    ]
    return df.with_columns(exprs).with_columns(
        pl.col("q_roe").rolling_std(window_size=8, min_periods=8).over("symbol").alias("roe_std8")
    )


def attach_pit(panel: pl.DataFrame, fin: pl.DataFrame) -> pl.DataFrame:
    cols = ["bps", "roe", "gross_margin", "net_margin", "revenue_yoy", "net_income_yoy",
            "debt_to_asset_ratio", *DIRECTION]
    right = (
        fin.select(["symbol", "_eff", *cols])
        .with_columns(pl.col("_eff").dt.offset_by("1d").alias("_eff"))
        .sort(["symbol", "_eff"])
    )
    joined = panel.sort(["symbol", "date"]).join_asof(
        right, left_on="date", right_on="_eff", by="symbol", strategy="backward", check_sortedness=False
    )
    return joined.with_columns(
        pl.when((pl.col("bps") > 0) & pl.col("close").is_not_null())
        .then(pl.col("close") / pl.col("bps")).otherwise(None).alias("pb")
    ).with_columns(
        pl.when(pl.col("pb") > 0).then(pl.col("roe") / (100.0 * pl.col("pb"))).otherwise(None).alias("ep")
    )


def portfolio_stats(frame: pl.DataFrame, legs: list[tuple[str, str, float]], cost: float = ROUND_TRIP_2X) -> dict:
    """月度等权组合: 毛年化 / 2×成本净年化 (按成员换手计费) / 换手 / 逐年。"""
    d = frame
    for col, direction, q in legs:
        d = d.filter(pl.col(col).is_not_null()).with_columns(
            (pl.col(col).rank().over("date") / pl.len().over("date")).alias("_pct")
        )
        d = d.filter(pl.col("_pct") <= q) if direction == "low" else d.filter(pl.col("_pct") >= 1 - q)
    monthly = (
        d.filter(pl.col("fwd").is_not_null())
        .group_by("date")
        .agg([pl.col("fwd").mean().alias("r"), pl.col("symbol").alias("symbols")])
        .sort("date")
    )
    if monthly.height < 12:
        return {"annual": None, "annual_net2x": None, "months": monthly.height, "members_mean": None,
                "turnover_mean": None, "yearly_gross": {}, "yearly_net2x": {}}
    total = total_net = 1.0
    turnovers: list[float] = []
    counts: list[int] = []
    gprod: dict[str, float] = {}
    nprod: dict[str, float] = {}
    prev: set[str] | None = None
    for row in monthly.iter_rows(named=True):
        members = set(row["symbols"])
        turn = 1.0 if prev is None else 1.0 - len(members & prev) / max(len(members), 1)
        turnovers.append(turn)
        counts.append(len(members))
        year = str(row["date"].year)
        total *= 1.0 + row["r"]
        total_net *= 1.0 + (row["r"] - turn * cost)
        gprod[year] = gprod.get(year, 1.0) * (1.0 + row["r"])
        nprod[year] = nprod.get(year, 1.0) * (1.0 + row["r"] - turn * cost)
        prev = members
    n = monthly.height
    return {
        "annual": round(total ** (12.0 / n) - 1.0, 4),
        "annual_net2x": round(total_net ** (12.0 / n) - 1.0, 4),
        "months": n,
        "members_mean": round(st.mean(counts), 1),
        "turnover_mean": round(st.mean(turnovers), 4),
        "yearly_gross": {k: round(v - 1.0, 4) for k, v in sorted(gprod.items())},
        "yearly_net2x": {k: round(v - 1.0, 4) for k, v in sorted(nprod.items())},
    }


def pead(panel: pl.DataFrame, fin: pl.DataFrame) -> dict:
    px = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"])
    px = px.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret"))
    mkt_df = px.group_by("date").agg(pl.col("ret").mean().alias("mkt")).sort("date")
    mkt = dict(zip(mkt_df["date"].to_list(), mkt_df["mkt"].to_list(), strict=False))
    series = {}
    for sym, group in px.group_by("symbol"):
        series[sym[0] if isinstance(sym, tuple) else sym] = (
            group["date"].to_list(), group["close"].to_list(),
        )
    events = fin.select(["symbol", "announce", "net_income_yoy"]).filter(
        (pl.col("announce") >= START) & (pl.col("announce") <= END)
    ).drop_nulls()
    rows = []
    for sym, ann, yoy in events.iter_rows():
        item = series.get(sym)
        if not item:
            continue
        dates, closes = item
        i = bisect.bisect_right(dates, ann)
        if i + 60 >= len(dates):
            continue
        stock = closes[i + 60] / closes[i] - 1.0
        bench = 1.0
        for j in range(i + 1, i + 61):
            bench *= 1.0 + mkt.get(dates[j], 0.0)
        rows.append({"month": dates[i].strftime("%Y-%m"), "car60": stock - (bench - 1.0), "yoy": yoy})
    if not rows:
        return {"n_events": 0}
    df = pl.DataFrame(rows).with_columns(
        ((pl.col("yoy").rank().over("month") - 1.0) * 5.0 / pl.len().over("month")).floor().clip(0, 4).cast(pl.Int64).alias("q")
    )
    summary = []
    for q, group in df.group_by("q", maintain_order=True):
        vals = group["car60"].to_list()
        if len(vals) < 20:
            continue
        mean = st.mean(vals)
        sd = st.pstdev(vals)
        summary.append({
            "q": int(q[0] if isinstance(q, tuple) else q),
            "n": len(vals),
            "mean": round(mean, 4),
            "median": round(st.median(vals), 4),
            "t": round(mean / (sd / len(vals) ** 0.5), 2) if sd > 0 else None,
        })
    summary.sort(key=lambda x: x["q"])
    top = summary[-1] if summary else None
    bottom = summary[0] if summary else None
    monotonic = bool(summary and len(summary) == 5 and all(
        summary[i]["mean"] <= summary[i + 1]["mean"] for i in range(4)
    ))
    return {
        "n_events": df.height,
        "by_quintile": summary,
        "top_median": top["median"] if top else None,
        "top_t": top["t"] if top else None,
        "bottom_median": bottom["median"] if bottom else None,
        "monotonic": monotonic,
    }


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = attach_pit(load_panel(symbols), derived(to_quarterly(merge_tables(symbols))).with_columns(
        pl.col("announce").alias("_eff")
    ))
    pairs = r22.monthly_pairs(panel)
    reb = r22.leg_columns(r22.build_monthly(panel, pairs))
    print(f"panel rows={panel.height}, rebalances={len(pairs)}", flush=True)

    gates = {}
    for name, good_high in DIRECTION.items():
        ic = r22.ic_summary(reb, name)
        grp = r22.offline_groups(reb, name, good_high=good_high)
        corr_v2 = max(
            (v for v in (r22.mean_abs_corr(reb, name, leg) for leg in ("leg_low_amount", "leg_small_mv", "leg_low_vol")) if v is not None),
            default=None,
        )
        corr_value = max(
            (v for v in (r22.mean_abs_corr(reb, name, other) for other in ("pb", "ep")) if v is not None),
            default=None,
        )
        rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
        gates[name] = {
            "F1_ic": bool(ic and abs(ic["ic_mean"]) >= 0.03 and r22.yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4),
            "F1_ic_mean": ic["ic_mean"] if ic else None,
            "F1_yearly": ic["yearly"] if ic else None,
            "F2_monotonic_spread": bool(rho_ok and grp and grp["spread_month"] > 0),
            "F2_group_monthly": grp["group_monthly"] if grp else None,
            "F3_spread_2x": bool(grp and grp["spread_annual_net2x"] > 0),
            "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
            "F4_max_abs_corr_v2": corr_v2,
            "F6_max_abs_corr_value": corr_value,
            "F6_ok": bool(corr_value is not None and corr_value < 0.7),
        }
        gates[name]["all_pass"] = bool(
            gates[name]["F1_ic"] and gates[name]["F2_monotonic_spread"]
            and gates[name]["F3_spread_2x"] and gates[name]["F4_max_abs_corr_v2"] is not None
            and gates[name]["F4_max_abs_corr_v2"] < 0.7 and gates[name]["F6_ok"]
        )

    legs = {
        "low_pb": [("pb", "low", 1 / 3)],
        "high_roe": [("roe", "high", 1 / 3)],
        "D1_qv_ocf": [("pb", "low", 1 / 3), ("roe", "high", 1 / 3), ("ocf_ni", "high", 0.5)],
        "D2_quality": [("roe", "high", 1 / 3), ("ocf_ni", "high", 1 / 3), ("accrual", "low", 1 / 3)],
        "D3_growth_quality": [("ni_accel", "high", 1 / 3), ("ocf_ni", "high", 1 / 3)],
        "D4_safety": [("debt_to_asset_ratio", "low", 1 / 3), ("ocf_debt", "high", 1 / 3)],
        "high_ni_accel": [("ni_accel", "high", 1 / 3)],
        "low_debt": [("debt_to_asset_ratio", "low", 1 / 3)],
    }
    portfolios = {name: portfolio_stats(reb, spec) for name, spec in legs.items()}

    def f5b(port: str, *single_legs: str, net: bool = False) -> bool:
        key = "annual_net2x" if net else "annual"
        p = portfolios[port][key]
        best_single = max((portfolios[s][key] for s in single_legs if portfolios[s][key] is not None), default=None)
        ref = max(v for v in (portfolios["low_pb"][key], portfolios["high_roe"][key]) if v is not None)
        return bool(p is not None and best_single is not None and p > best_single - 0.01 and p > ref)

    f5b_result = {
        "D1": f5b("D1_qv_ocf", "low_pb", "high_roe"),
        "D2": f5b("D2_quality", "high_roe", "low_pb"),
        "D3": f5b("D3_growth_quality", "high_ni_accel", "high_roe", "low_pb"),
        "D4": f5b("D4_safety", "low_debt", "high_roe", "low_pb"),
    }
    f5b_net_result = {
        "D1": f5b("D1_qv_ocf", "low_pb", "high_roe", net=True),
        "D2": f5b("D2_quality", "high_roe", "low_pb", net=True),
        "D3": f5b("D3_growth_quality", "high_ni_accel", "high_roe", "low_pb", net=True),
        "D4": f5b("D4_safety", "low_debt", "high_roe", "low_pb", net=True),
    }

    pead_result = pead(panel, derived(to_quarterly(merge_tables(symbols))))

    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "gates": gates,
        "all_pass_factors": sorted(k for k, v in gates.items() if v["all_pass"]),
        "portfolios": portfolios,
        "f5b": f5b_result,
        "f5b_net2x": f5b_net_result,
        "pead": pead_result,
    }
    out = EV / "r23_quality_growth.json"
    out.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    for name, g in gates.items():
        print(f"{name}: F1={g['F1_ic']} ({g['F1_ic_mean']}) F2={g['F2_monotonic_spread']} F3={g['F3_spread_2x']} F4={g['F4_max_abs_corr_v2']} F6={g['F6_max_abs_corr_value']}")
    print("portfolios:", {k: (v["annual"], v["annual_net2x"], v["turnover_mean"]) for k, v in portfolios.items()})
    print("F5b:", f5b_result, "| PEAD:", {k: pead_result.get(k) for k in ("n_events", "top_median", "top_t", "monotonic")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
