# 输入说明: R74 — 沪深300 ETF 510300 份额环比择时。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r74_etf_flow.py
# 结果 JSON: 同目录 r74_etf_flow.json。月末快照缓存 data/research/r74_etf_flow/ (不提交)。
from __future__ import annotations

import importlib.util
import json
import time
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
r54 = _load("r54", "script_r54_long_only.py")
r56 = _load("r56", "script_r56_index_timing.py")
r30 = r56.r30
DATA = r40.DATA
CACHE = DATA / "research" / "r74_etf_flow"
END = date(2026, 10, 16)
START = date(2021, 6, 1)
COST = 0.0028
FUND_CODE = "510300"


def _month_dates() -> list[date]:
    index = r56._index()
    return (
        index.select(pl.col("date"))
        .filter((pl.col("date") >= date(2020, 12, 1)) & (pl.col("date") <= END))
        .with_columns([pl.col("date").dt.year().alias("y"), pl.col("date").dt.month().alias("m")])
        .group_by(["y", "m"]).agg(pl.col("date").max().alias("snapshot_date"))
        .sort("snapshot_date")["snapshot_date"].to_list()
    )


def _fetch(dates: list[date]) -> tuple[dict, pl.DataFrame]:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    frames = []
    fetched = cached = 0
    for index, day in enumerate(dates, start=1):
        path = CACHE / f"{day.isoformat()}.parquet"
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
            cached += 1
        else:
            frame = None
            last_error = None
            for attempt in range(3):
                try:
                    raw = ak.fund_etf_scale_sse(date=day.strftime("%Y%m%d"))
                    frame = pl.from_pandas(raw)
                    tmp = path.with_suffix(".tmp")
                    frame.write_parquet(tmp)
                    tmp.replace(path)
                    fetched += 1
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                    last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
            if last_error:
                failures[day.isoformat()] = last_error
        if frame is not None and frame.height:
            code_col = "基金代码" if "基金代码" in frame.columns else None
            shares_col = "基金份额" if "基金份额" in frame.columns else None
            date_col = "统计日期" if "统计日期" in frame.columns else None
            if code_col and shares_col and date_col:
                hit = frame.filter(pl.col(code_col).cast(pl.Utf8).str.zfill(6) == FUND_CODE).select([
                    pl.col(code_col).cast(pl.Utf8).str.zfill(6).alias("code"),
                    pl.col(date_col).cast(pl.Date).alias("snapshot_date"),
                    pl.col(shares_col).cast(pl.Float64, strict=False).alias("shares"),
                ])
                frames.append(hit)
        if index % 6 == 0 or index == len(dates):
            print(f"ETF flow fetch {index}/{len(dates)} cached={cached} fetched={fetched} failed={len(failures)}", flush=True)
    manifest = {
        "months": len(dates), "cached_this_run": cached, "fetched_this_run": fetched,
        "failure_count": len(failures), "failure_rate": round(len(failures) / len(dates), 6) if dates else 1.0,
        "failures": failures,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if not frames:
        raise RuntimeError("510300 份额缓存为空")
    return manifest, pl.concat(frames, how="diagonal_relaxed").sort("snapshot_date")


def _monthly_signal(shares: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    frame = shares.unique(subset=["snapshot_date"], keep="last").sort("snapshot_date")
    frame = frame.with_columns([
        pl.col("snapshot_date").dt.strftime("%Y-%m").alias("ym"),
        (pl.col("shares") / pl.col("shares").shift(1) - 1.0).alias("share_chg_1m"),
    ])
    frame = frame.with_columns(pl.col("snapshot_date").shift(1).alias("_prev_date")).with_columns(
        (pl.col("snapshot_date") - pl.col("_prev_date")).dt.total_days().alias("_gap_days")
    ).with_columns(
        pl.when(pl.col("_gap_days").is_between(20, 40, closed="both")).then(pl.col("share_chg_1m")).otherwise(None).alias("share_chg_1m")
    ).drop("_prev_date")
    audit = {
        "rows": frame.height, "snapshot_min": str(frame["snapshot_date"].min()), "snapshot_max": str(frame["snapshot_date"].max()),
        "signal_nonnull": int(frame["share_chg_1m"].is_not_null().sum()),
        "share_chg_distribution": {
            "min": round(float(frame["share_chg_1m"].min()), 6) if frame["share_chg_1m"].is_not_null().any() else None,
            "median": round(float(frame["share_chg_1m"].median()), 6) if frame["share_chg_1m"].is_not_null().any() else None,
            "max": round(float(frame["share_chg_1m"].max()), 6) if frame["share_chg_1m"].is_not_null().any() else None,
        },
    }
    return frame, audit


def _excess_monthly(strategy: pl.DataFrame, base: pl.DataFrame) -> dict:
    return r54._excess(strategy, base)


def main() -> int:
    dates = _month_dates()
    manifest, shares = _fetch(dates)
    monthly, signal_audit = _monthly_signal(shares)
    index = r56._index().sort("date")
    index = index.with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("ret"))
    daily = index.filter(pl.col("date") >= START).join_asof(
        monthly.select(["snapshot_date", "share_chg_1m"]).sort("snapshot_date"),
        left_on="date", right_on="snapshot_date", strategy="backward", check_sortedness=False,
    )
    daily = daily.with_columns([
        (pl.col("share_chg_1m") > 0).cast(pl.Float64).fill_null(0.0).shift(1).alias("position"),
        pl.col("ret").alias("base_r"),
    ]).with_columns(
        pl.col("position").shift(1).fill_null(0.0).alias("_prev_position")
    ).with_columns((pl.col("position") - pl.col("_prev_position")).abs().alias("turnover")).with_columns(
        (pl.col("position") * pl.col("ret") - COST * pl.col("turnover")).alias("r")
    )
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]
    base_daily = daily.filter(pl.col("base_r").is_not_null())
    strat_daily = daily.filter(pl.col("r").is_not_null())
    base_m, base_roll = r56._phase_metrics(base_daily, "base_r", phases)
    strat_m, strat_roll = r56._phase_metrics(strat_daily, "r", phases)
    base_full = r56._daily_metrics(base_daily, "base_r")
    strat_full = r56._daily_metrics(strat_daily, "r")
    strat_month = r56._monthly(strat_daily, "r")
    base_month = r56._monthly(base_daily, "base_r")
    excess = _excess_monthly(strat_month, base_month)
    high = monthly.with_columns((pl.col("share_chg_1m") > 0).alias("high_flow"))
    ret_month = r56._monthly(index, "ret").with_columns(pl.col("r").shift(-1).alias("next_r"))
    signal_returns = high.join(ret_month.select(["ym", "next_r"]), on="ym", how="left")
    high_vals = signal_returns.filter(pl.col("high_flow"))["next_r"].drop_nulls().to_list()
    low_vals = signal_returns.filter(~pl.col("high_flow"))["next_r"].drop_nulls().to_list()
    high_mean = sum(high_vals) / len(high_vals) if high_vals else None
    low_mean = sum(low_vals) / len(low_vals) if low_vals else None
    phase_sharpes = [p["sharpe"] for p in strat_m["phases"]]
    base_sharpes = [p["sharpe"] for p in base_m["phases"]]
    phase_better = sum(1 for a, b in zip(phase_sharpes, base_sharpes, strict=False) if a is not None and b is not None and a > b)
    d1 = bool(manifest["failure_rate"] <= 0.10 and signal_audit["signal_nonnull"] >= 60)
    t1 = bool(strat_m["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10 and strat_m["annual_mean"] >= base_m["annual_mean"])
    t2 = bool(strat_m["mdd_mean"] >= base_m["mdd_mean"] - 0.01 and phase_better >= 5)
    t3 = bool(excess["validation"]["mean"] is not None and excess["validation"]["mean"] > 0 and excess["validation"]["t"] is not None and excess["validation"]["t"] > 1.0)
    t4 = bool(high_mean is not None and high_mean > 0 and low_mean is not None and high_mean > low_mean)
    gates = {"D1": d1, "T1": t1, "T2": t2, "T3": t3, "T4": t4}
    gates["all_pass"] = all(gates.values())
    persistent = {k: v for k, v in manifest.items() if k not in {"cached_this_run", "fetched_this_run"}}
    result = {
        "window": {"start": str(START), "end": str(END), "phases": phases, "fund": FUND_CODE, "cost": COST},
        "data": {"fetch": persistent, "signals": signal_audit},
        "base": {"metrics": base_m, "rolling": base_roll, "full": base_full},
        "strategy": {"metrics": strat_m, "rolling": strat_roll, "full": strat_full, "excess": excess, "phase_better": phase_better, "turnover_annual": round(float(strat_daily["turnover"].sum() / max(1.0, strat_daily.height / 252.0)), 2)},
        "conditional_returns": {"high_flow_mean": round(high_mean, 5) if high_mean is not None else None, "low_flow_mean": round(low_mean, 5) if low_mean is not None else None, "high_n": len(high_vals), "low_n": len(low_vals)},
        "gates": gates,
        "passed": ["ETF_FLOW_TIMING"] if gates["all_pass"] else [],
        "recommendation": "R75 final risk/cost audit" if gates["all_pass"] else "negative knowledge; ETF flow dimension closed",
        "caveats": ["月末快照滞后一日使用", "现金收益按 0", "只测 510300 份额环比, 不把 ETF 价格当资金流", "指数 close-to-close"],
    }
    path = EV / "r74_etf_flow.json"
    path.write_text(json.dumps(r30.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("base", base_full, "strat", strat_full, "phase_better", phase_better, "excess", excess, "conditional", result["conditional_returns"], "gates", gates, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
