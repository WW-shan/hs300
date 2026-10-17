# 输入说明: R48 — 分析师调高/调低与目标价空间 PIT 筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r48_analyst_events.py
# 结果 JSON: 同目录 r48_analyst_events.json。原始报告缓存复用 data/research/r47_analyst/。
from __future__ import annotations

import importlib.util
import json
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
r47 = _load("r47", "script_r47_analyst.py")
r22 = r42.r22
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "analyst_upgrade_90d": {"min_month": 60, "min_cov": 0.60, "min_reports": 3},
    "analyst_target_upside_90d": {"min_month": 30, "min_cov": 0.30, "min_reports": 2},
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _daily_metric(reports: pl.DataFrame, value_col: str) -> pl.DataFrame:
    return (
        reports.filter(pl.col(value_col).is_not_null())
        .with_columns(pl.col("report_date").dt.offset_by("1d").alias("available_date"))
        .group_by(["symbol", "available_date"])
        .agg([
            pl.len().alias("metric_count"),
            pl.col(value_col).sum().alias("metric_sum"),
        ])
        .sort(["symbol", "available_date"])
        .with_columns([
            pl.col("metric_count").cum_sum().over("symbol").alias("cum_count"),
            pl.col("metric_sum").cum_sum().over("symbol").alias("cum_sum"),
        ])
    )


def _asof(reb: pl.DataFrame, daily: pl.DataFrame, days: int, suffix: str) -> pl.DataFrame:
    left = reb.with_columns((pl.col("date") - pl.duration(days=days)).alias("_cut")).sort(["symbol", "_cut"])
    right = daily.select(["symbol", "available_date", "cum_count", "cum_sum"]).sort(["symbol", "available_date"])
    out = left.join_asof(
        right,
        left_on="_cut",
        right_on="available_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).rename({
        "cum_count": f"cum_count_{suffix}",
        "cum_sum": f"cum_sum_{suffix}",
    })
    return out.drop(["_cut", "available_date"])


def _attach_metric(reb: pl.DataFrame, daily: pl.DataFrame, col: str, min_reports: int) -> pl.DataFrame:
    out = reb.sort(["symbol", "date"])
    for days, suffix in ((0, "t"), (90, "t90")):
        out = out.join(
            _asof(out, daily, days, suffix).select(["symbol", "date", f"cum_count_{suffix}", f"cum_sum_{suffix}"]),
            on=["symbol", "date"],
            how="left",
        )
    out = out.with_columns([
        pl.col("cum_count_t").fill_null(0).cast(pl.Int64).alias("cum_count_t"),
        pl.col("cum_count_t90").fill_null(0).cast(pl.Int64).alias("cum_count_t90"),
        pl.col("cum_sum_t").fill_null(0.0).cast(pl.Float64).alias("cum_sum_t"),
        pl.col("cum_sum_t90").fill_null(0.0).cast(pl.Float64).alias("cum_sum_t90"),
    ]).with_columns([
        (pl.col("cum_count_t") - pl.col("cum_count_t90")).alias("_count"),
        (pl.col("cum_sum_t") - pl.col("cum_sum_t90")).alias("_sum"),
    ]).with_columns(
        pl.when(pl.col("_count") >= min_reports).then(pl.col("_sum") / pl.col("_count")).otherwise(None).alias(col)
    )
    return out.drop([
        "cum_count_t", "cum_count_t90", "cum_sum_t", "cum_sum_t90", "_count", "_sum",
    ])


def _target_upside(reports: pl.DataFrame, panel: pl.DataFrame) -> pl.DataFrame:
    close = panel.select([
        "symbol",
        pl.col("date").alias("report_date"),
        pl.col("raw_close").alias("_raw_close"),
    ])
    return (
        reports.join(close, on=["symbol", "report_date"], how="left")
        .with_columns(
            pl.when((pl.col("target_high") > 0) & (pl.col("_raw_close") > 0))
            .then(pl.col("target_high") / pl.col("_raw_close") - 1.0)
            .otherwise(None)
            .alias("analyst_target_upside")
        )
        .drop("_raw_close")
    )


def _coverage(frame: pl.DataFrame, col: str, min_month: int) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(col).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge_min": 0, "min_month": min_month}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge_min": int((grouped["valid"] >= min_month).sum()),
        "min_month": min_month,
    }


def _usable(frame: pl.DataFrame, col: str, min_month: int) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(
        pl.col("valid") >= min_month
    )["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    reports = r47._reports()
    reports = _target_upside(reports, panel)
    upgrade_daily = _daily_metric(reports, "rating_change_score")
    target_daily = _daily_metric(reports, "analyst_target_upside")
    joined = _attach_metric(reb, upgrade_daily, "analyst_upgrade_90d", FACTORS["analyst_upgrade_90d"]["min_reports"])
    joined = _attach_metric(joined, target_daily, "analyst_target_upside_90d", FACTORS["analyst_target_upside_90d"]["min_reports"])
    r47_joined = r47._attach(reb, r47._daily(reports))
    r47_cols = r47_joined.select(["symbol", "date", "analyst_rating_90d", "analyst_revision_90d"])
    joined = joined.join(r47_cols, on=["symbol", "date"], how="left")
    print("reports", reports.height, "upgrade_daily", upgrade_daily.shape, "target_daily", target_daily.shape, flush=True)

    records, discovery_p = {}, []
    for index, (name, spec) in enumerate(FACTORS.items()):
        min_month = spec["min_month"]
        raw_disc = r42._period_frame(joined, DISCOVERY)
        raw_val = r42._period_frame(joined, VALIDATION)
        disc_cov = _coverage(raw_disc, name, min_month)
        val_cov = _coverage(raw_val, name, min_month)
        disc = _usable(raw_disc, name, min_month)
        val = _usable(raw_val, name, min_month)
        sign = 1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
        corr = max(
            (value for value in (r22.mean_abs_corr(full, "_signed", control) for control in CONTROLS) if value is not None),
            default=None,
        )
        corr_r47 = max(
            (value for value in (
                r22.mean_abs_corr(full, "_signed", "analyst_rating_90d"),
                r22.mean_abs_corr(full, "_signed", "analyst_revision_90d"),
            ) if value is not None),
            default=None,
        )
        d1 = bool(
            disc_cov["mean"] is not None and disc_cov["mean"] >= spec["min_cov"]
            and val_cov["mean"] is not None and val_cov["mean"] >= spec["min_cov"]
            and disc_cov["months_ge_min"] >= 24 and val_cov["months_ge_min"] >= 24
            and disc_cov["months_ge_min"] + val_cov["months_ge_min"] >= 48
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
        records[name] = {
            "direction": "high",
            "coverage_discovery": disc_cov,
            "coverage_validation": val_cov,
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing_max": corr,
            "corr_r47_max": corr_r47,
            "D1": d1,
            "F1": f1,
            "F2": f2,
            "F4": f4,
        }
        discovery_p.append(disc_stats["perm_p"])
        print(f"{name}: D1={d1} cov={disc_cov['mean']}/{val_cov['mean']} IC={disc_stats['mean']}/{val_stats['mean']} rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} p={disc_stats['perm_p']}", flush=True)

    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (name, _spec), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[name]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec["D1"] and rec["F1"] and rec["F2"] and rec["F3"] and rec["F4"])
        if rec["all_pass"]:
            passed.append(name)

    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "window_days": 90,
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "reports": reports.height,
            "upgrade_score_coverage": round(float(reports["rating_change_score"].is_not_null().mean()), 4),
            "target_upside_coverage": round(float(reports["analyst_target_upside"].is_not_null().mean()), 4),
            "rating_change_distribution": reports.group_by("rating_change").len().sort(["len", "rating_change"], descending=[True, False]).to_dicts(),
            "upgrade_rows": reports.filter(pl.col("rating_change_score") > 0).height,
            "downgrade_rows": reports.filter(pl.col("rating_change_score") < 0).height,
        },
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r49": passed,
        "decision": {
            "D1_count": sum(bool(records[n]["D1"]) for n in FACTORS),
            "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS),
            "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS),
            "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS),
            "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS),
            "all_pass_count": len(passed),
        },
        "recommendation": "R49 portfolio integration" if passed else "negative knowledge; analyst dimension closed",
        "caveats": [
            "卖方报告存在覆盖和选择性披露偏差",
            "目标价使用报告日 raw_close, 避免复权价与名义目标价错配",
            "历史成员为月度快照",
            "只测 90 天固定窗口, 不做参数网格",
        ],
    }
    path = EV / "r48_analyst_events.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
