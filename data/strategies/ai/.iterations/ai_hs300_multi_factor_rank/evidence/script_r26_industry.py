# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r26_industry.py
# 作用: R26 预注册 — 行业 RPS top3 vs 全体等权 / 行业内中性动量 vs 全局动量 / 行业拥挤度 / 主线报告。
# 结果 JSON: 同目录 r26_industry_rotation.json。
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
LAST_YEAR_START = date(2025, 10, 14)

_spec = importlib.util.spec_from_file_location("r22", EV / "script_r22_scan.py")
r22 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r22)


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
    from app.backtest.factor import FactorBacktestService

    return FactorBacktestService._compute_missing_factors(
        panel, {"overnight_ret_20d", "amplitude_trend_20_60"}
    )


def portfolio_stats(monthly_frame: pl.DataFrame) -> dict:
    monthly = monthly_frame.sort("date")
    vals = monthly["r"].to_list()
    if len(vals) < 12:
        return {"annual": None, "sharpe": None, "mdd": None, "yearly": {}, "last_year_return": None, "months": len(vals)}
    total = 1.0
    for v in vals:
        total *= 1.0 + v
    n = len(vals)
    sd = st.pstdev(vals)
    sharpe = st.mean(vals) / sd * (12 ** 0.5) if sd > 0 else None
    peak = value = 1.0
    mdd = 0.0
    for v in vals:
        value *= 1.0 + v
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    gprod: dict[str, float] = {}
    for d, v in zip(monthly["date"].to_list(), vals, strict=False):
        y = str(d.year)
        gprod[y] = gprod.get(y, 1.0) * (1.0 + v)
    yearly = {k: round(v - 1.0, 4) for k, v in sorted(gprod.items())}
    last = sum(v for d, v in zip(monthly["date"].to_list(), vals, strict=False) if d >= LAST_YEAR_START)
    return {
        "annual": round(total ** (12.0 / n) - 1.0, 4),
        "sharpe": round(sharpe, 3) if sharpe else None,
        "mdd": round(mdd, 4),
        "yearly": yearly,
        "positive_years": sum(1 for v in yearly.values() if v > 0),
        "last_year_return": round(last, 4),
        "months": n,
    }


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]

    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    coverage = industry.filter(pl.col("symbol").is_in(symbols))["symbol"].n_unique() / len(symbols)

    panel = load_panel(symbols).join(industry, on="symbol", how="left")
    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    reb = reb.drop_nulls("industry_l1")

    # 行业 RPS: 行业成员 trailing 60/20 日等权收益 (在调仓日)
    close_hist = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"]).with_columns([
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("ret60"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("ret20"),
    ])
    reb = reb.join(close_hist.select(["symbol", "date", "ret60", "ret20"]), on=["symbol", "date"], how="left")
    industry_rps = (
        reb.filter(pl.col("ret60").is_not_null() & pl.col("fwd").is_not_null())
        .group_by(["date", "industry_l1"])
        .agg([
            pl.col("ret60").mean().alias("rps60"),
            pl.col("ret20").mean().alias("rps20"),
            pl.col("fwd").mean().alias("ind_fwd"),
            pl.len().alias("n_members"),
        ])
        .sort(["date", "rps60"], descending=[False, True])
    )
    industry_rank = industry_rps.with_columns(pl.col("rps60").rank(method="ordinal", descending=True).over("date").alias("rps60_rank"))
    top3 = industry_rank.filter(pl.col("rps60_rank") <= 3)

    def top3_members(frame: pl.DataFrame, key: str, k: int = 3) -> pl.DataFrame:
        ranked = frame.with_columns(pl.col(key).rank(method="ordinal", descending=True).over("date").alias("_rk"))
        return ranked.filter(pl.col("_rk") <= k)

    # A1: top3 行业成员等权; A2: 全部成员等权 (基准)
    ind_top3_dates = top3.select(["date", "industry_l1"])
    a1 = (
        reb.join(ind_top3_dates, on=["date", "industry_l1"], how="inner")
        .filter(pl.col("fwd").is_not_null())
        .group_by("date").agg(pl.col("fwd").mean().alias("r"))
    )
    a2 = reb.filter(pl.col("fwd").is_not_null()).group_by("date").agg(pl.col("fwd").mean().alias("r"))
    rps_result = {
        "A_top3_industries": portfolio_stats(a1),
        "benchmark_equal_weight": portfolio_stats(a2),
    }
    gate_a = bool(
        rps_result["A_top3_industries"]["annual"] is not None
        and rps_result["A_top3_industries"]["annual"] >= rps_result["benchmark_equal_weight"]["annual"] + 0.02
        and rps_result["A_top3_industries"]["sharpe"] >= rps_result["benchmark_equal_weight"]["sharpe"] + 0.10
        and rps_result["A_top3_industries"]["positive_years"] >= rps_result["benchmark_equal_weight"]["positive_years"]
    )
    rps_result["R26_1_pass"] = gate_a
    rps_result["top3_industries_last12m"] = (
        industry_rank.filter(pl.col("date") >= LAST_YEAR_START)
        .group_by("industry_l1").agg(pl.col("ind_fwd").mean().alias("mean_fwd")).sort("mean_fwd", descending=True)
        .head(5).to_dicts()
    )

    # B: 全局动量 top20 vs 行业内去均值 top20
    mom = reb.filter(
        pl.col("overnight_ret_20d").is_not_null()
        & pl.col("amplitude_trend_20_60").is_not_null()
        & pl.col("fwd").is_not_null()
    ).with_columns([
        (pl.col("overnight_ret_20d").rank().over("date") / pl.len().over("date")).alias("_m1"),
        (pl.col("amplitude_trend_20_60").rank().over("date") / pl.len().over("date")).alias("_m2"),
    ]).with_columns(((pl.col("_m1") + pl.col("_m2")) / 2.0).alias("mom"))
    mom = mom.with_columns((pl.col("mom") - pl.col("mom").mean().over(["date", "industry_l1"])).alias("mom_neutral"))

    def top20(frame: pl.DataFrame, col: str) -> pl.DataFrame:
        return (
            frame.with_columns(pl.col(col).rank(method="ordinal", descending=True).over("date").alias("_rk"))
            .filter(pl.col("_rk") <= 20)
            .group_by("date").agg(pl.col("fwd").mean().alias("r"))
        )

    b_global = portfolio_stats(top20(mom, "mom"))
    b_neutral = portfolio_stats(top20(mom, "mom_neutral"))
    gate_b = bool(
        b_neutral["annual"] is not None and b_global["annual"] is not None
        and (
            b_neutral["annual"] >= b_global["annual"] + 0.02
            or b_neutral["sharpe"] >= b_global["sharpe"] + 0.10
        )
        and (b_neutral["last_year_return"] or -9) >= (b_global["last_year_return"] or -9)
    )

    # C: 行业拥挤度: 行业 20 日成交额占比分位 -> 未来 20 日行业收益
    amount_hist = (
        panel.select(["symbol", "date", "amount"])
        .sort(["symbol", "date"])
        .with_columns(pl.col("amount").rolling_mean(window_size=20, min_periods=20).over("symbol").alias("amt20"))
    )
    reb_c = reb.join(amount_hist.select(["symbol", "date", "amt20"]), on=["symbol", "date"], how="left")
    ind_crowd = (
        reb_c.filter(pl.col("amt20").is_not_null() & pl.col("fwd").is_not_null())
        .group_by(["date", "industry_l1"])
        .agg([pl.col("amt20").sum().alias("amt"), pl.col("fwd").mean().alias("ind_fwd")])
        .with_columns((pl.col("amt") / pl.col("amt").sum().over("date")).alias("share"))
        .with_columns(pl.col("share").rank().over("date").alias("crowd_rank"))
    )
    n_ind = ind_crowd.group_by("date").len()["len"].max() or 1
    high = ind_crowd.filter(pl.col("crowd_rank") > n_ind - 3)["ind_fwd"].to_list()
    low = ind_crowd.filter(pl.col("crowd_rank") <= 3)["ind_fwd"].to_list()
    crowd_result = {
        "high_crowd_in_top3": {"mean": round(st.mean(high), 4) if high else None, "n": len(high)},
        "low_crowd_top3": {"mean": round(st.mean(low), 4) if low else None, "n": len(low)},
    }
    crowd_result["R26_3_warning"] = bool(high and st.mean(high) < -0.01)

    # D: 主线报告
    mainline = pl.read_parquet(DATA / "mainline_history" / "part.parquet")
    px = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"])
    closes: dict[str, list] = {}
    for key, group in px.group_by("symbol"):
        closes[key[0] if isinstance(key, tuple) else key] = (group["date"].to_list(), group["close"].to_list())
    import bisect

    rows = []
    for row in mainline.filter(pl.col("rank") == 1).sort("date").iter_rows(named=True):
        item = closes.get(row["leader_symbol"])
        if not item:
            continue
        dates, values = item
        i = bisect.bisect_right(dates, row["date"])
        if i == 0 or i >= len(dates):
            continue
        rec = {"date": str(row["date"]), "member": row["member"]}
        if i + 5 < len(dates):
            rec["fwd5"] = values[i + 5] / values[i] - 1.0
        if i + 20 < len(dates):
            rec["fwd20"] = values[i + 20] / values[i] - 1.0
        rows.append(rec)
    f5 = [r["fwd5"] for r in rows if "fwd5" in r]
    f20 = [r["fwd20"] for r in rows if "fwd20" in r]
    mainline_result = {
        "n_days": mainline.height,
        "n_events": len(rows),
        "fwd5_median": round(st.median(f5), 4) if f5 else None,
        "fwd20_median": round(st.median(f20), 4) if f20 else None,
    }

    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "industry_coverage": round(coverage, 4),
        "A_industry_rps": rps_result,
        "B_within_industry_momentum": {"global_top20": b_global, "industry_neutral_top20": b_neutral, "R26_2_pass": gate_b},
        "C_crowding": crowd_result,
        "D_mainline": mainline_result,
    }
    out = EV / "r26_industry_rotation.json"
    out.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    print("coverage:", result["industry_coverage"], "industries:", industry["industry_l1"].n_unique())
    print("A top3:", rps_result["A_top3_industries"]["annual"], rps_result["A_top3_industries"]["sharpe"], "| bench:", rps_result["benchmark_equal_weight"]["annual"], rps_result["benchmark_equal_weight"]["sharpe"], "| pass:", gate_a)
    print("B global:", b_global["annual"], b_global["sharpe"], "| neutral:", b_neutral["annual"], b_neutral["sharpe"], "| pass:", gate_b)
    print("C:", crowd_result)
    print("D:", mainline_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
