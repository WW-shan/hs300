# 输入说明: R44 — 历史 HS300 成员股东户数变化 PIT 因子筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r44_holder.py
# 结果 JSON: 同目录 r44_holder.json。原始逐股缓存 data/research/r44_holder/ (不提交)。
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import time
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r22 = r42.r22
DATA = r42.DATA
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
PERMUTATIONS = r42.PERMUTATIONS
RNG_SEED = r42.RNG_SEED
CACHE = DATA / "research" / "r44_holder"
MAX_AGE_DAYS = 200
MIN_VALID_MONTH = 80
MIN_USABLE_MONTHS = 48
FACTORS: dict[str, bool] = {
    "holder_chg_qoq": False,
    "holder_chg_yoy": False,
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _normalize(df, symbol: str) -> pl.DataFrame | None:
    if df is None or len(df) == 0:
        return None
    columns = {
        "股东户数统计截止日": "period_date",
        "股东户数公告日期": "announce_date",
        "股东户数-本次": "holder_count",
        "股东户数-上次": "holder_prev_count",
        "股东户数-增减": "holder_change",
        "股东户数-增减比例": "holder_chg_pct",
        "户均持股市值": "avg_hold_value",
        "户均持股数量": "avg_hold_shares",
    }
    available = [c for c in columns if c in df.columns]
    if len(available) < 4:
        return None
    frame = pl.from_pandas(df[available].copy()).rename(columns)
    frame = frame.with_columns([
        pl.lit(symbol).alias("symbol"),
        *[
            pl.col(c).cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False)
            for c in ("period_date", "announce_date")
            if c in frame.columns
        ],
        *[
            pl.col(c).cast(pl.Float64, strict=False)
            for c in ("holder_count", "holder_prev_count", "holder_change", "holder_chg_pct", "avg_hold_value", "avg_hold_shares")
            if c in frame.columns
        ],
    ])
    if "period_date" not in frame.columns or "announce_date" not in frame.columns or "holder_count" not in frame.columns:
        return None
    return (
        frame.filter(
            pl.col("period_date").is_not_null()
            & pl.col("announce_date").is_not_null()
            & (pl.col("holder_count") > 0)
        )
        .unique(subset=["symbol", "period_date", "announce_date"], keep="last")
        .sort(["symbol", "period_date", "announce_date"])
    )


def _fetch_holder_cache(symbols: list[str]) -> tuple[dict, list[dict]]:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    devnull = io.StringIO()
    try:
        for index, symbol in enumerate(symbols, start=1):
            path = CACHE / f"{symbol}.parquet"
            if path.exists() and path.stat().st_size > 0:
                continue
            last_error = "empty"
            for attempt in range(3):
                try:
                    with contextlib.redirect_stderr(devnull):
                        raw = ak.stock_zh_a_gdhs_detail_em(symbol=symbol[:6])
                    frame = _normalize(raw, symbol)
                    if frame is not None and frame.height:
                        frame.write_parquet(path)
                        last_error = ""
                    else:
                        last_error = "empty_or_missing_columns"
                    break
                except Exception as exc:  # noqa: BLE001 - research cache records provider failures and continues
                    last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
            if last_error:
                failures[symbol] = last_error
            if index % 25 == 0 or index == len(symbols):
                cached = len(list(CACHE.glob("*.parquet")))
                print(f"holder fetch {index}/{len(symbols)} cached={cached} failures={len(failures)}", flush=True)
            time.sleep(0.02)
    finally:
        devnull.close()
    manifest = {
        "symbols": len(symbols),
        "cached": len(list(CACHE.glob("*.parquet"))),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6),
        "failures": failures,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest, list(failures)


def _load_signals() -> pl.DataFrame:
    frames = [pl.read_parquet(path) for path in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("股东户数缓存为空")
    data = (
        pl.concat(frames, how="diagonal_relaxed")
        .sort(["symbol", "period_date", "announce_date"])
        .unique(subset=["symbol", "period_date"], keep="last")
        .with_columns(pl.col("announce_date").dt.offset_by("1d").alias("available_date"))
    )
    qoq = data.select([
        "symbol", "period_date", "announce_date", "available_date", "holder_count",
        (pl.col("holder_chg_pct") / 100.0).alias("holder_chg_qoq"),
    ])
    left = data.select(["symbol", "period_date", "holder_count"])
    right = data.select([
        "symbol",
        pl.col("period_date").alias("prev_period"),
        pl.col("holder_count").alias("prev_count"),
    ])
    yoy_match = (
        left.join(right, on="symbol", how="inner")
        .with_columns((pl.col("period_date") - pl.col("prev_period")).dt.total_days().alias("gap_days"))
        .filter(
            pl.col("gap_days").is_between(300, 430, closed="both")
            & (pl.col("prev_count") > 0)
        )
        .with_columns((pl.col("gap_days") - 365).abs().alias("_gap_distance"))
        .sort(
            ["symbol", "period_date", "_gap_distance", "prev_period"],
            descending=[False, False, False, True],
        )
        .unique(subset=["symbol", "period_date"], keep="first")
        .select(["symbol", "period_date", "prev_count", "gap_days"])
    )
    signals = qoq.join(yoy_match, on=["symbol", "period_date"], how="left").with_columns(
        pl.when((pl.col("prev_count") > 0) & (pl.col("holder_count") > 0))
        .then(pl.col("holder_count") / pl.col("prev_count") - 1.0)
        .otherwise(None)
        .alias("holder_chg_yoy")
    )
    return (
        signals.select([
            "symbol", "period_date", "available_date", "holder_chg_qoq", "holder_chg_yoy", "gap_days",
        ])
        .sort(["symbol", "available_date", "period_date"])
        .unique(subset=["symbol", "available_date"], keep="last")
    )


def _attach(reb: pl.DataFrame, signals: pl.DataFrame) -> pl.DataFrame:
    joined = reb.sort(["symbol", "date"]).join_asof(
        signals.sort(["symbol", "available_date"]),
        left_on="date",
        right_on="available_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).with_columns(
        (pl.col("date") - pl.col("available_date")).dt.total_days().alias("holder_age_days")
    )
    joined = joined.with_columns([
        pl.when(pl.col("holder_age_days").is_between(0, MAX_AGE_DAYS, closed="both"))
        .then(pl.col(name)).otherwise(None).alias(name)
        for name in FACTORS
    ])
    for name in FACTORS:
        lo = f"_{name}_lo"
        hi = f"_{name}_hi"
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias(lo),
            pl.col(name).quantile(0.99).over("date").alias(hi),
        ]).with_columns(
            pl.col(name).clip(pl.col(lo), pl.col(hi)).alias(name)
        ).drop([lo, hi])
    return joined


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(col).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge80": 0}
    coverage = grouped["valid"] / grouped["eligible"]
    return {
        "mean": round(float(coverage.mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge80": int((grouped["valid"] >= MIN_VALID_MONTH).sum()),
    }


def _usable_months(frame: pl.DataFrame, col: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    grouped = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).sort("date")
    usable = grouped.filter(pl.col("valid") >= MIN_VALID_MONTH)
    return frame.filter(pl.col("date").is_in(usable["date"].to_list())), usable


def _age_report(frame: pl.DataFrame) -> dict:
    vals = frame.filter(pl.col("holder_chg_qoq").is_not_null())["holder_age_days"].drop_nulls().to_list()
    if not vals:
        return {"n": 0}
    vals = sorted(vals)
    def q(p: float) -> float:
        return float(vals[min(len(vals) - 1, int(p * (len(vals) - 1)))])
    return {
        "n": len(vals),
        "median": q(0.50),
        "p90": q(0.90),
        "p99": q(0.99),
        "max": vals[-1],
    }


def _industry_report(frame: pl.DataFrame, col: str) -> dict:
    try:
        from app.services.industry_map import load_industry_l1

        industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    except Exception as exc:  # noqa: BLE001 - industry coverage is report-only
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    d = (
        frame.filter(pl.col(col).is_not_null() & pl.col("fwd").is_not_null())
        .join(industry, on="symbol", how="left")
        .with_columns(
            ((pl.col(col).rank().over("date") - 1.0) * 5.0 / pl.len().over("date"))
            .floor().clip(0, 4).cast(pl.Int64).add(1).alias("q")
        )
    )
    rows = (
        d.filter(pl.col("q") == 1)
        .group_by("industry_l1").len().sort("len", descending=True).head(12)
        .rename({"len": "q1_rows"})
        .to_dicts()
    )
    return {"coverage": round(d["industry_l1"].is_not_null().mean(), 4), "q1_top": rows}


def main() -> int:
    panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = sorted(memberships["symbol"].unique().to_list())
    manifest, failures = _fetch_holder_cache(symbols)
    signals = _load_signals()
    joined = _attach(reb, signals)
    print("panel", panel.shape, "reb", reb.shape, "signals", signals.shape, "fetch failures", len(failures), flush=True)

    records = {}
    discovery_p = []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc = r42._period_frame(joined, DISCOVERY)
        raw_val = r42._period_frame(joined, VALIDATION)
        disc_coverage = _coverage(raw_disc, name)
        val_coverage = _coverage(raw_val, name)
        disc, disc_months = _usable_months(raw_disc, name)
        val, val_months = _usable_months(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
        corr = max(
            (
                value for value in (
                    r22.mean_abs_corr(full, "_signed", control) for control in CONTROLS
                )
                if value is not None
            ),
            default=None,
        )
        d1 = bool(
            manifest["failure_rate"] <= 0.10
            and disc_coverage["mean"] is not None and disc_coverage["mean"] >= 0.70
            and val_coverage["mean"] is not None and val_coverage["mean"] >= 0.70
            and disc_coverage["months_ge80"] >= MIN_USABLE_MONTHS
            and val_coverage["months_ge80"] >= MIN_USABLE_MONTHS
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        f4 = bool(corr is not None and corr < 0.7)
        d1_corrected = bool(
            manifest["failure_rate"] <= 0.10
            and disc_coverage["mean"] is not None and disc_coverage["mean"] >= 0.70
            and val_coverage["mean"] is not None and val_coverage["mean"] >= 0.70
            and disc_months.height >= 24 and val_months.height >= 24
            and disc_months.height + val_months.height >= MIN_USABLE_MONTHS
        )
        records[name] = {
            "direction": "high" if good_high else "low",
            "coverage_discovery": disc_coverage,
            "coverage_validation": val_coverage,
            "usable_months": {"discovery": disc_months.height, "validation": val_months.height},
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing_max": corr,
            "industry": _industry_report(full, name),
            "D1_preregistered": d1,
            "D1_arithmetic_corrected": d1_corrected,
            "F1": f1,
            "F2": f2,
            "F4": f4,
        }
        discovery_p.append(disc_stats["perm_p"])
        print(
            f"{name}: D1={d1} cov={disc_coverage['mean']}/{val_coverage['mean']} "
            f"IC={disc_stats['mean']}/{val_stats['mean']} "
            f"rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} "
            f"corr={corr} p={disc_stats['perm_p']}", flush=True,
        )

    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (name, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[name]
        rec["discovery_q"] = q
        rec["F3"] = bool(
            q is not None and q <= 0.10
            and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05
        )
        rec["all_pass"] = bool(rec.get("D1_preregistered") and rec.get("F1") and rec.get("F2") and rec.get("F3") and rec.get("F4"))
        if rec["all_pass"]:
            passed.append(name)

    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "max_age_days": MAX_AGE_DAYS,
            "min_valid_month": MIN_VALID_MONTH,
            "min_usable_months": MIN_USABLE_MONTHS,
            "permutations": PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            **manifest,
            "signals": signals.height,
            "symbols_with_signal": signals["symbol"].n_unique(),
            "age_report": _age_report(joined),
        },
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r45": passed,
        "decision": {
            "D1_preregistered_count": sum(bool(records[name].get("D1_preregistered")) for name in FACTORS),
            "D1_arithmetic_corrected_count": sum(bool(records[name].get("D1_arithmetic_corrected")) for name in FACTORS),
            "F1_count": sum(bool(records[name].get("F1")) for name in FACTORS),
            "F2_count": sum(bool(records[name].get("F2")) for name in FACTORS),
            "F3_count": sum(bool(records[name].get("F3")) for name in FACTORS),
            "F4_count": sum(bool(records[name].get("F4")) for name in FACTORS),
            "all_pass_count": len(passed),
        },
        "recommendation": "R45 portfolio integration" if passed else "negative knowledge; do not deploy",
        "caveats": [
            "股东户数公告日用于 PIT, 但供应商可能回填修订值, 无法完全证明首次披露时点",
            "历史名单来自月度 BaoStock 快照, 不是逐日调样流水",
            "当前行业快照只用于暴露报告, 不作为本轮门槛",
            "原始股东户数缓存不提交, 结果可由脚本重拉",
            "原 R44-D1 要求每个子区间≥48个月, 与31/33个月窗口矛盾; D1_arithmetic_corrected 仅作算术纠错报告, 不改变其他门槛",
        ],
    }
    path = EV / "r44_holder.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("passed:", passed, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
