# 输入说明: R55 — 历史 HS300 涨跌停/连板结构因子。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r55_limit_structure.py
# 结果 JSON: 同目录 r55_limit_structure.json。
from __future__ import annotations

import importlib.util
import json
import math
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
r42 = _load("r42", "script_r42_historical_factor_screen.py")
r53 = _load("r53", "script_r53_price_volume_zoo.py")
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "limit_up_count_20d": False,
    "limit_up_count_60d": False,
    "max_limit_up_streak_60d": False,
    "limit_down_count_20d": False,
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
END = r40.END


def _rate() -> pl.Expr:
    growth = pl.col("symbol").str.starts_with("300") | pl.col("symbol").str.starts_with("688")
    return (
        pl.when(growth & (pl.col("date") >= pl.date(2020, 8, 24))).then(0.20)
        .when(growth).then(0.10)
        .when(pl.col("isST") == 1).then(0.05)
        .otherwise(0.10)
    )


def _local_flags(symbols: list[str]) -> pl.DataFrame:
    local = (
        pl.scan_parquet(r40.DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & (pl.col("date") >= pl.date(2020, 6, 1)) & (pl.col("date") <= END))
        .select(["symbol", "date", "raw_close", "consecutive_limit_ups", "consecutive_limit_downs"])
        .collect()
        .with_columns([
            pl.col("date").cast(pl.Date),
            (pl.col("consecutive_limit_ups") > 0).alias("limit_up_flag"),
            (pl.col("consecutive_limit_downs") > 0).alias("limit_down_flag"),
            pl.lit("local_exact").alias("flag_source"),
            pl.lit(0).alias("_priority"),
        ])
    )
    return local.sort(["symbol", "date"])


def _bootstrap_flags(symbols: list[str]) -> pl.DataFrame:
    raw = pl.read_parquet(r40.PRICE_CACHE).filter(pl.col("price_mode") == "raw").select([
        "symbol", "date", "pctChg", "isST", "tradestatus",
    ])
    raw = raw.with_columns([
        pl.col("date").cast(pl.Date),
        pl.col("pctChg").cast(pl.Float64, strict=False),
        pl.col("isST").cast(pl.Int64, strict=False),
        pl.col("tradestatus").cast(pl.Utf8),
    ]).filter(
        pl.col("symbol").is_in(symbols)
        & (pl.col("date") >= pl.date(2020, 6, 1))
        & (pl.col("date") <= END)
        & (pl.col("tradestatus") == "1")
    )
    raw = raw.with_columns(_rate().alias("rate"))
    return raw.with_columns([
        (pl.col("pctChg") >= pl.col("rate") * 100.0 - 0.5).alias("limit_up_flag"),
        (pl.col("pctChg") <= -pl.col("rate") * 100.0 + 0.5).alias("limit_down_flag"),
        pl.lit("bootstrap_proxy").alias("flag_source"),
        pl.lit(1).alias("_priority"),
    ]).select(["symbol", "date", "limit_up_flag", "limit_down_flag", "flag_source", "_priority"])


def _flags(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    local = _local_flags(symbols)
    boot = _bootstrap_flags(symbols)
    combined = pl.concat([local.select(["symbol", "date", "limit_up_flag", "limit_down_flag", "flag_source", "_priority"]), boot], how="diagonal_relaxed")
    combined = combined.sort(["symbol", "date", "_priority"]).unique(subset=["symbol", "date"], keep="first").drop("_priority")
    # Same-day source counts before priority.
    source = combined.group_by("flag_source").len().sort("flag_source").to_dicts()
    # Agreement between exact local flags and a raw-close proxy on local rows with a prior close.
    local_a = local.sort(["symbol", "date"]).with_columns(
        (pl.col("raw_close") / pl.col("raw_close").shift(1).over("symbol") - 1.0).alias("_ret")
    ).with_columns(
        (pl.when(pl.col("symbol").str.starts_with("300") | pl.col("symbol").str.starts_with("688"))
         .then(pl.when(pl.col("date") >= pl.date(2020, 8, 24)).then(0.20).otherwise(0.10))
         .otherwise(0.10)).alias("_rate")
    ).with_columns([
        (pl.col("_ret") >= pl.col("_rate") - 0.005).alias("_proxy_up"),
        (pl.col("_ret") <= -pl.col("_rate") + 0.005).alias("_proxy_down"),
    ]).filter(pl.col("_ret").is_not_null())
    audit = {
        "local_rows": local.height,
        "bootstrap_rows": boot.height,
        "combined_rows": combined.height,
        "source_rows": source,
        "exact_proxy_agreement_up": round(float((local_a["limit_up_flag"] == local_a["_proxy_up"]).mean()), 6) if local_a.height else None,
        "exact_proxy_agreement_down": round(float((local_a["limit_down_flag"] == local_a["_proxy_down"]).mean()), 6) if local_a.height else None,
        "exact_up": int(local["limit_up_flag"].sum()),
        "exact_down": int(local["limit_down_flag"].sum()),
    }
    return combined, audit


def _ic_stats_safe(frame: pl.DataFrame, col: str, seed: int, *, one_sided: bool) -> dict:
    series = r42.r22.ic_frame(frame, col)
    if series.is_empty():
        return {"mean": None, "ir": None, "n_dates": 0, "yearly": {}, "perm_p": None, "coverage": r53._coverage(frame, col)}
    dates = series["date"].to_list()
    vals = []
    yearly = {}
    for day, value in zip(dates, series["ic"].to_list(), strict=False):
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        vals.append(float(value))
        yearly.setdefault(day.year, []).append(float(value))
    if len(vals) < 12:
        return {"mean": None, "ir": None, "n_dates": len(vals), "yearly": {}, "perm_p": None, "coverage": r53._coverage(frame, col)}
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    p = r42._perm_p(vals, seed, one_sided=one_sided)
    return {
        "mean": round(mean, 4),
        "ir": round(mean / sd, 4) if sd > 0 else None,
        "n_dates": len(vals),
        "yearly": {str(y): round(sum(v) / len(v), 4) for y, v in sorted(yearly.items())},
        "perm_p": p,
        "coverage": r53._coverage(frame, col),
    }


def _streak(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    parts = []
    for _keys, group in frame.group_by("symbol", maintain_order=True):
        values = group[col].to_list()
        streak, out = 0, []
        for value in values:
            streak = streak + 1 if value else 0
            out.append(streak)
        parts.append(group.with_columns(pl.Series(f"_{col}_streak", out)))
    return pl.concat(parts, how="diagonal_relaxed") if parts else frame


def _limit_factors(flags: pl.DataFrame) -> pl.DataFrame:
    f = _streak(flags.sort(["symbol", "date"]), "limit_up_flag")
    f = f.with_columns([
        pl.col("limit_up_flag").cast(pl.Int64).rolling_sum(20, min_samples=10).over("symbol").alias("limit_up_count_20d"),
        pl.col("limit_up_flag").cast(pl.Int64).rolling_sum(60, min_samples=30).over("symbol").alias("limit_up_count_60d"),
        pl.col("_limit_up_flag_streak").rolling_max(60, min_samples=30).over("symbol").alias("max_limit_up_streak_60d"),
        pl.col("limit_down_flag").cast(pl.Int64).rolling_sum(20, min_samples=10).over("symbol").alias("limit_down_count_20d"),
    ])
    return f.select(["symbol", "date", *FACTORS])


def main() -> int:
    panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = sorted(memberships["symbol"].unique().to_list())
    flags, audit = _flags(symbols)
    factors = _limit_factors(flags)
    reb = reb.join(factors, on=["symbol", "date"], how="left")
    print("panel", panel.shape, "flags", flags.shape, "factors", factors.shape, "reb", reb.shape, "audit", audit, flush=True)
    flag_keys = flags.select(["symbol", "date"]).unique().height
    panel_keys = panel.select(["symbol", "date"]).unique().height
    data_quality = {"flag_coverage": round(flag_keys / panel_keys, 6) if panel_keys else None, **audit}

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc = r42._period_frame(reb, DISCOVERY)
        raw_val = r42._period_frame(reb, VALIDATION)
        disc_cov, val_cov = r53._coverage(raw_disc, name), r53._coverage(raw_val, name)
        disc, val = r53._usable(raw_disc, name), r53._usable(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = _ic_stats_safe(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = _ic_stats_safe(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = _ic_stats_safe(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r42.r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None and math.isfinite(v)), default=None)
        d1 = bool(
            data_quality["flag_coverage"] is not None and data_quality["flag_coverage"] >= 0.95
            and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.70
            and disc_cov["months_ge_min"] >= 24 and val_cov["months_ge_min"] >= 24
            and disc_cov["months_ge_min"] + val_cov["months_ge_min"] >= 48
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r42.r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        f4 = bool(corr is not None and corr < 0.7)
        records[name] = {
            "direction": "high" if good_high else "low", "coverage_discovery": disc_cov,
            "coverage_validation": val_cov, "discovery": disc_stats, "validation": val_stats,
            "full": full_stats, "discovery_groups": disc_group, "validation_groups": val_group,
            "corr_existing_max": corr, "D1": d1, "F1": f1, "F2": f2, "F4": f4,
        }
        discovery_p.append(disc_stats["perm_p"])
        print(f"{name}: D1={d1} cov={disc_cov['mean']}/{val_cov['mean']} IC={disc_stats['mean']}/{val_stats['mean']} rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} corr={corr} p={disc_stats['perm_p']}", flush=True)
    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (name, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[name]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec["D1"] and rec["F1"] and rec["F2"] and rec["F3"] and rec["F4"])
        if rec["all_pass"]:
            passed.append(name)
    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "data": data_quality,
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r56": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R56 portfolio integration" if passed else "negative knowledge; do not deploy",
        "caveats": ["历史缺失成员的涨跌停为板块/ST pctChg 代理, 非逐笔判定价", "本地精确标记与代理一致率约99.75%/99.91%", "历史名单为月度快照", "未覆盖盘中炸板/封单"],
    }
    path = EV / "r55_limit_structure.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
