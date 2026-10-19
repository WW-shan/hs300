#!/usr/bin/env python3
"""R169: commodity main-contract momentum mapped to HS300 upstream industries."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r169_commodity_industry_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
CACHE = DATA / "research" / "r169_commodity_main"
DISCOVERY_START = date(2019, 1, 1)
DISCOVERY_END = date(2021, 12, 31)
VALIDATION_START = date(2022, 1, 1)
VALIDATION_END = date(2023, 12, 31)
HOLDOUT_START = date(2024, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
MIN_NAMES = 30
MIN_INDUSTRIES = 5
TOP_N = 3
COST = 0.003
CLIP = 0.12
MOM_WINDOW = 60
Z_WINDOW = 252
Z_MIN = 120

COMMODITIES = ["CU0", "AL0", "AU0", "RB0", "I0", "SC0", "TA0", "FG0", "JM0", "M0", "SR0"]
INDUSTRY_MAP = {
    "\u6709\u8272\u91d1\u5c5e": ["CU0", "AL0", "AU0"],
    "\u94a2\u94c1": ["RB0", "I0"],
    "\u77f3\u6cb9\u77f3\u5316": ["SC0"],
    "\u57fa\u7840\u5316\u5de5": ["TA0"],
    "\u5efa\u7b51\u6750\u6599": ["FG0"],
    "\u7164\u70ad": ["JM0"],
    "\u519c\u6797\u7267\u6e14": ["M0", "SR0"],
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fetch_commodity(symbol: str) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}.parquet"
    if path.exists():
        frame = pl.read_parquet(path)
        source = "cache"
    else:
        import akshare as ak

        raw = ak.futures_main_sina(symbol=symbol, start_date="20180101", end_date=date.today().strftime("%Y%m%d"))
        frame = pl.from_pandas(raw).rename({
            raw.columns[0]: "date",
            raw.columns[1]: "open",
            raw.columns[2]: "high",
            raw.columns[3]: "low",
            raw.columns[4]: "close",
            raw.columns[5]: "volume",
            raw.columns[6]: "open_interest",
            raw.columns[7]: "settle",
        })
        frame = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
        frame.write_parquet(path)
        source = "akshare.futures_main_sina"
    frame = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    audit = {
        "symbol": symbol,
        "source": source,
        "rows": frame.height,
        "start": str(frame["date"].min()) if frame.height else None,
        "end": str(frame["date"].max()) if frame.height else None,
        "missing_close": int(frame["close"].is_null().sum()) if "close" in frame.columns else None,
        "zero_close": int((frame["close"] == 0).sum()) if "close" in frame.columns else None,
    }
    return frame, audit


def _commodity_signal(frame: pl.DataFrame, symbol: str) -> pl.DataFrame:
    out = (
        frame.select(["date", "close"])
        .with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).clip(-CLIP, CLIP).alias("ret"))
        .with_columns((1.0 + pl.col("ret")).log().alias("_log_ret"))
        .with_columns(
            (pl.col("_log_ret").rolling_sum(window_size=MOM_WINDOW, min_samples=MOM_WINDOW).exp() - 1.0).alias("mom60")
        )
        .with_columns(
            pl.col("mom60").rolling_mean(window_size=Z_WINDOW, min_samples=Z_MIN).alias("_mean"),
            pl.col("mom60").rolling_std(window_size=Z_WINDOW, min_samples=Z_MIN).alias("_std"),
        )
        .with_columns(
            pl.when(pl.col("_std") > 0)
            .then((pl.col("mom60") - pl.col("_mean")) / pl.col("_std"))
            .otherwise(None)
            .alias("z")
        )
        .select(["date", pl.lit(symbol).alias("commodity"), "mom60", "z"])
    )
    return out.filter(pl.col("z").is_not_null())


def _industry_map() -> pl.DataFrame:
    path = DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
    raw = pl.read_parquet(path)
    return (
        raw.select(
            [
                pl.col("symbol").cast(pl.Utf8),
                pl.col(raw.columns[2]).cast(pl.Utf8).str.split("-").list.first().alias("industry"),
            ]
        )
        .drop_nulls()
        .filter(pl.col("industry") != "")
        .unique(subset=["symbol"], keep="last")
    )


def _monthly_membership() -> tuple[list[date], dict[date, set[str]]]:
    frame = (
        pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
        .select([pl.col("query_date").cast(pl.Date).alias("snapshot_date"), pl.col("symbol")])
        .unique()
        .sort(["snapshot_date", "symbol"])
    )
    dates = sorted(frame["snapshot_date"].unique().to_list())
    mapping = {}
    for (snapshot,), part in frame.group_by(["snapshot_date"], maintain_order=True):
        mapping[snapshot] = set(part["symbol"].to_list())
    return dates, mapping


def _commodity_industry_map() -> pl.DataFrame:
    rows = [{"commodity": commodity, "industry": industry} for industry, commodities in INDUSTRY_MAP.items() for commodity in commodities]
    return pl.DataFrame(rows)


def _industry_signal_for_day(day: date, signals: pl.DataFrame, commodity_map: pl.DataFrame) -> pl.DataFrame:
    left = pl.DataFrame({"date": [day] * len(COMMODITIES), "commodity": COMMODITIES})
    joined = (
        left.sort(["commodity", "date"])
        .join_asof(
            signals.sort(["commodity", "date"]),
            on="date",
            by="commodity",
            strategy="backward",
            check_sortedness=False,
        )
        .join(commodity_map, on="commodity", how="inner")
        .filter(pl.col("z").is_not_null())
        .group_by("industry")
        .agg(pl.col("z").mean().alias("commodity_signal"), pl.len().alias("n_commodities"))
    )
    expected = pl.DataFrame({
        "industry": list(INDUSTRY_MAP.keys()),
        "expected_commodities": [len(v) for v in INDUSTRY_MAP.values()],
    })
    return joined.join(expected, on="industry", how="inner").filter(pl.col("n_commodities") == pl.col("expected_commodities"))


def _rank_corr(frame: pl.DataFrame, left: str, right: str) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if part.height < MIN_NAMES or part[left].n_unique() < 2 or part[right].n_unique() < 2:
        return None
    x = part[left].rank("average")
    y = part[right].rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(frame: pl.DataFrame, factor: str, control: str, target: str) -> float | None:
    part = frame.select([factor, control, target]).drop_nulls()
    n = part.height
    if n < MIN_NAMES or part[factor].n_unique() < 2 or part[control].n_unique() < 2:
        return None
    xs = part[control].to_list()
    ys = part[factor].to_list()
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    residual = part.with_columns((pl.col(factor) - alpha - beta * pl.col(control)).alias("_resid"))
    return _rank_corr(residual, "_resid", target)


def _monthly_ls(day: date, members: set[str], industry: pl.DataFrame, signals: pl.DataFrame, forward: pl.DataFrame) -> dict | None:
    if not members:
        return None
    base = pl.DataFrame({"symbol": sorted(members)}).join(industry, on="symbol", how="left")
    fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
    frame = base.join(fwd, on="symbol", how="inner").filter(pl.col("industry").is_not_null())
    if frame.height < MIN_NAMES:
        return None
    by_industry = (
        frame.group_by("industry")
        .agg(pl.col("forward_return").mean().alias("industry_return"), pl.len().alias("n_names"))
        .join(signals.select(["industry", "commodity_signal"]), on="industry", how="inner")
        .sort("commodity_signal", descending=True)
    )
    if by_industry.height < MIN_INDUSTRIES or by_industry.height < 2 * TOP_N:
        return None
    top = by_industry.head(TOP_N)
    bottom = by_industry.tail(TOP_N)
    long_ret = top["industry_return"].mean()
    short_ret = bottom["industry_return"].mean()
    if long_ret is None or short_ret is None:
        return None
    return {
        "date": str(day),
        "long_industries": top["industry"].to_list(),
        "short_industries": bottom["industry"].to_list(),
        "long_return": float(long_ret),
        "short_return": float(short_ret),
        "net_return": float(long_ret - short_ret - COST),
        "n_industries": by_industry.height,
    }


def _stats(values: list[float]) -> dict:
    if not values:
        return {"months": 0, "mean": None, "positive_rate": None, "p05": None, "worst": None}
    ordered = sorted(values)
    return {
        "months": len(values),
        "mean": round(st.mean(values), 6),
        "positive_rate": round(sum(v > 0 for v in values) / len(values), 4),
        "p05": round(ordered[max(0, int(len(values) * 0.05))], 6),
        "worst": round(min(values), 6),
    }


def _compound(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def _mdd(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    prices = r167._prices()
    daily_membership_dates, daily_membership_map = r167._membership()
    monthly_membership_dates, monthly_membership_map = _monthly_membership()
    forward = r167._forward_returns(calendar, month_ends, prices)
    industry = _industry_map()
    commodity_map = _commodity_industry_map()

    audits = []
    signal_parts = []
    for symbol in COMMODITIES:
        frame, audit = _fetch_commodity(symbol)
        audits.append(audit)
        signal_parts.append(_commodity_signal(frame, symbol))
    signals = pl.concat(signal_parts, how="vertical").sort(["commodity", "date"])

    records = []
    industry_ls = []
    for day in month_ends:
        if daily_membership_dates and day >= daily_membership_dates[0]:
            members = r167._members_on(daily_membership_dates, daily_membership_map, day)
            membership_source = "daily"
        else:
            members = r167._members_on(monthly_membership_dates, monthly_membership_map, day)
            membership_source = "monthly"
        if not members:
            continue
        industry_signals = _industry_signal_for_day(day, signals, commodity_map)
        if industry_signals.height < len(INDUSTRY_MAP):
            continue
        base = pl.DataFrame({"symbol": sorted(members)}).join(industry, on="symbol", how="left")
        mapped = base.filter(pl.col("industry").is_in(list(INDUSTRY_MAP.keys())))
        factor_frame = mapped.join(industry_signals, on="industry", how="left")
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        joined = factor_frame.join(fwd, on="symbol", how="inner")
        ic = _rank_corr(joined, "commodity_signal", "forward_return")
        factor_names = int(factor_frame["commodity_signal"].is_not_null().sum())
        coverage = float(factor_names / mapped.height) if mapped.height else 0.0
        ext_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
        control_corr = None
        residual_ic = None
        if ext_path.exists() and joined.height:
            ext = pl.read_parquet(ext_path).select(["symbol", "industry_mom_60d"])
            controlled = joined.join(ext, on="symbol", how="left")
            control_corr = _rank_corr(controlled, "commodity_signal", "industry_mom_60d")
            residual_ic = _residual_ic(controlled, "commodity_signal", "industry_mom_60d", "forward_return")
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        records.append({
            "date": str(day),
            "split": split,
            "mapped_names": mapped.height,
            "factor_names": factor_names,
            "membership_source": membership_source,
            "coverage": round(coverage, 4),
            "ic": round(ic, 6) if ic is not None else None,
            "industry_mom_corr": round(control_corr, 6) if control_corr is not None else None,
            "residual_ic": round(residual_ic, 6) if residual_ic is not None else None,
        })
        ls = _monthly_ls(day, members, industry, industry_signals, forward)
        if ls is not None:
            ls["split"] = split
            industry_ls.append(ls)

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [row for row in records if row["split"] == split and row["ic"] is not None]
        ls_part = [row["net_return"] for row in industry_ls if row["split"] == split]
        ic_values = [float(row["ic"]) for row in part]
        summary[split] = {
            "months": len(part),
            "ic_mean": round(st.mean(ic_values), 6) if ic_values else None,
            "ic_win_rate": round(sum(v > 0 for v in ic_values) / len(ic_values), 4) if ic_values else None,
            "coverage_mean": round(st.mean(row["coverage"] for row in part), 4) if part else None,
            "industry_mom_corr_mean": round(st.mean(row["industry_mom_corr"] for row in part if row["industry_mom_corr"] is not None), 4) if any(row["industry_mom_corr"] is not None for row in part) else None,
            "residual_ic_mean": round(st.mean(row["residual_ic"] for row in part if row["residual_ic"] is not None), 6) if any(row["residual_ic"] is not None for row in part) else None,
            "long_short": _stats(ls_part),
            "long_short_compound": round(_compound(ls_part), 6) if ls_part else None,
            "long_short_mdd": round(_mdd(ls_part), 6) if ls_part else None,
        }

    recent_ls = [row["net_return"] for row in industry_ls if row["split"] == "holdout"][-12:]
    recent_12m = round(_compound(recent_ls), 6) if recent_ls else None

    d1 = (
        len(audits) == len(COMMODITIES)
        and all(audit["missing_close"] == 0 and audit["zero_close"] == 0 for audit in audits)
        and len(INDUSTRY_MAP) == 7
        and summary["discovery"]["months"] >= 30
        and summary["validation"]["months"] >= 24
        and summary["holdout"]["months"] >= 24
        and all((summary[s]["coverage_mean"] or 0) >= 0.90 for s in ["discovery", "validation", "holdout"])
    )
    g1 = (
        summary["discovery"]["ic_mean"] is not None
        and summary["discovery"]["ic_mean"] >= 0.030
        and summary["discovery"]["ic_win_rate"] >= 0.55
    )
    g2 = (
        summary["validation"]["ic_mean"] is not None
        and summary["validation"]["ic_mean"] >= 0.015
        and summary["validation"]["ic_win_rate"] >= 0.52
    )
    g3 = (
        summary["holdout"]["ic_mean"] is not None
        and summary["holdout"]["ic_mean"] >= 0.010
        and summary["holdout"]["ic_win_rate"] >= 0.52
        and recent_12m is not None
        and recent_12m > 0
    )
    g4 = all(
        summary[s]["long_short"]["mean"] is not None
        and summary[s]["long_short"]["mean"] > 0
        and summary[s]["long_short"]["positive_rate"] >= 0.52
        for s in ["validation", "holdout"]
    ) and summary["holdout"]["long_short_mdd"] is not None and summary["holdout"]["long_short_mdd"] >= -0.20
    g5 = (
        summary["validation"]["industry_mom_corr_mean"] is not None
        and summary["validation"]["industry_mom_corr_mean"] < 0.70
        and summary["validation"]["residual_ic_mean"] is not None
        and summary["validation"]["residual_ic_mean"] >= 0.010
    )
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R169 commodity main-contract momentum mapped to HS300 upstream industries",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "source": "AkShare futures_main_sina",
            "cache": str(CACHE.relative_to(ROOT)),
            "commodities": audits,
            "industry_map": INDUSTRY_MAP,
            "industry_source": "data/ext_data/ext_hy_ths/part.parquet (static current classification)",
            "membership_source": "monthly r157 snapshots before 2020-12-31; daily r78 snapshots thereafter",
        },
        "method": {
            "daily_return_clip": [-CLIP, CLIP],
            "momentum_window": MOM_WINDOW,
            "zscore_window": Z_WINDOW,
            "zscore_min_observations": Z_MIN,
            "factor": "equal-weight commodity momentum z-score within mapped industry",
            "forward_return": "T+1 open to T+21 open",
            "ic": "monthly cross-sectional Spearman rank IC among mapped HS300 members",
            "long_short": "industry equal-weight top3 minus bottom3, 0.30% round-trip cost",
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_short": recent_12m,
        "decision": decision,
        "monthly_records": records,
        "industry_long_short": industry_ls,
        "limitations": [
            "Sina main-continuous prices contain roll gaps; no per-contract back-adjustment was performed.",
            "Industry mapping is static current THS L1 classification, not a point-in-time historical classification.",
            "Only 7 upstream industries and about 15% of HS300 names are mapped.",
            "Stock-level IC uses repeated industry signals; month-level win rate is the primary stability check.",
            "Commodity inventory history starts around 2020-10-28 and was not used.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_short": recent_12m, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())