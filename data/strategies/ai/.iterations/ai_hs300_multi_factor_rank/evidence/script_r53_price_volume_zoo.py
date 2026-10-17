# 输入说明: R53 — 第二轮价量/尾部/趋势因子 zoo。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r53_price_volume_zoo.py
# 结果 JSON: 同目录 r53_price_volume_zoo.json。
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
r22 = r42.r22
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "momentum_120d": True,
    "dist_52w_high": True,
    "residual_momentum_60d": True,
    "idio_vol_60d": False,
    "downside_beta_60d": False,
    "skew_60d": False,
    "kurt_60d": False,
    "amihud_20d": True,
    "turnover_accel_5_20": False,
    "obv_trend_20d": True,
    "bollinger_position_20d": False,
    "up_volume_ratio_20d": True,
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
MIN_MONTH = 80


def _factors(panel: pl.DataFrame) -> pl.DataFrame:
    p = panel.sort(["symbol", "date"]).with_columns([
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret"),
        pl.col("mkt_ret").fill_null(0.0).alias("_mkt_ret"),
    ]).with_columns([
        (pl.col("close") / pl.col("close").shift(120).over("symbol") - 1.0).alias("momentum_120d"),
        (pl.col("close") / pl.col("close").rolling_max(window_size=250, min_samples=250).over("symbol") - 1.0).alias("dist_52w_high"),
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("_ret60"),
        (pl.col("_mkt_ret").log1p().rolling_sum(60, min_samples=50).over("symbol").exp() - 1.0).alias("_mkt_ret60"),
        (pl.col("_ret").rolling_mean(60).over("symbol")).alias("_mean60"),
        ((pl.col("_ret").abs() / pl.col("amount")).alias("_amihud")),
    ]).with_columns([
        (pl.col("_ret60") - pl.col("beta_60d") * pl.col("_mkt_ret60")).alias("residual_momentum_60d"),
        (pl.col("_ret") - pl.col("beta_60d") * pl.col("_mkt_ret")).alias("_resid_ret"),
        ((pl.col("_ret") - pl.col("_mean60")).pow(2).rolling_mean(60).over("symbol")).alias("_m2"),
        ((pl.col("_ret") - pl.col("_mean60")).pow(3).rolling_mean(60).over("symbol")).alias("_m3"),
        ((pl.col("_ret") - pl.col("_mean60")).pow(4).rolling_mean(60).over("symbol")).alias("_m4"),
        pl.when(pl.col("_ret") > 0).then(pl.col("volume")).otherwise(0.0).alias("_up_vol"),
        pl.when(pl.col("_ret") < 0).then(pl.col("_mkt_ret")).otherwise(0.0).alias("_down_mkt"),
        pl.when(pl.col("_ret") < 0).then(pl.col("_mkt_ret")).otherwise(0.0).alias("_down_mkt_dup"),
        pl.when(pl.col("_ret") < 0).then(pl.col("_ret") * pl.col("_mkt_ret")).otherwise(0.0).alias("_down_xy"),
        pl.when(pl.col("_ret") < 0).then(pl.col("_mkt_ret").pow(2)).otherwise(0.0).alias("_down_yy"),
    ]).with_columns([
        (pl.col("_resid_ret").rolling_std(60).over("symbol")).alias("idio_vol_60d"),
        pl.when(pl.col("_m2") > 0).then(pl.col("_m3") / pl.col("_m2").pow(1.5)).otherwise(None).alias("skew_60d"),
        pl.when(pl.col("_m2") > 0).then(pl.col("_m4") / pl.col("_m2").pow(2.0) - 3.0).otherwise(None).alias("kurt_60d"),
        pl.col("_amihud").rolling_mean(20).over("symbol").alias("amihud_20d"),
        (pl.col("turnover_rate").rolling_mean(5).over("symbol") / pl.col("turnover_rate").rolling_mean(20).over("symbol") - 1.0).alias("turnover_accel_5_20"),
        (pl.col("_ret").sign() * pl.col("volume")).rolling_sum(20).over("symbol").alias("_obv_sum"),
        pl.col("_up_vol").rolling_sum(20).over("symbol").alias("_up_vol_sum"),
        ((pl.col("close") - pl.col("close").rolling_mean(20).over("symbol")) / (2.0 * pl.col("close").rolling_std(20).over("symbol"))).alias("bollinger_position_20d"),
        pl.col("_up_vol").rolling_sum(20).over("symbol").alias("_up_vol_ratio_num"),
        pl.col("volume").rolling_sum(20).over("symbol").alias("_vol_sum"),
    ]).with_columns([
        pl.when(pl.col("_vol_sum") > 0).then(pl.col("_up_vol_ratio_num") / pl.col("_vol_sum")).otherwise(None).alias("up_volume_ratio_20d"),
        pl.when(pl.col("_vol_sum") > 0).then(pl.col("_obv_sum") / pl.col("_vol_sum")).otherwise(None).alias("obv_trend_20d"),
    ])
    # Downside beta over the subset of market-down days.
    p = p.with_columns([
        (pl.col("_mkt_ret") < 0).cast(pl.Int64).rolling_sum(60).over("symbol").alias("_dn"),
        pl.when(pl.col("_mkt_ret") < 0).then(pl.col("_ret")).otherwise(0.0).rolling_sum(60).over("symbol").alias("_sx"),
        pl.when(pl.col("_mkt_ret") < 0).then(pl.col("_mkt_ret")).otherwise(0.0).rolling_sum(60).over("symbol").alias("_sy"),
        pl.col("_down_xy").rolling_sum(60).over("symbol").alias("_sxy"),
        pl.col("_down_yy").rolling_sum(60).over("symbol").alias("_syy"),
    ])
    p = p.with_columns([
        ((pl.col("_sxy") - pl.col("_sx") * pl.col("_sy") / pl.col("_dn")) / pl.col("_dn")).alias("_dn_cov"),
        ((pl.col("_syy") - pl.col("_sy").pow(2) / pl.col("_dn")) / pl.col("_dn")).alias("_dn_var"),
    ]).with_columns(
        pl.when((pl.col("_dn") >= 20) & (pl.col("_dn_var") > 0))
        .then(pl.col("_dn_cov") / pl.col("_dn_var")).otherwise(None).alias("downside_beta_60d")
    )
    return p.select(["symbol", "date", *FACTORS])


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge_min": 0}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge_min": int((grouped["valid"] >= MIN_MONTH).sum()),
    }


def _usable(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(pl.col("valid") >= MIN_MONTH)["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    factors = _factors(panel)
    reb = reb.join(factors, on=["symbol", "date"], how="left")
    reb = r42._scores(reb)
    print("panel", panel.shape, "reb", reb.shape, "factor rows", factors.shape, flush=True)
    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc = r42._period_frame(reb, DISCOVERY)
        raw_val = r42._period_frame(reb, VALIDATION)
        disc_cov, val_cov = _coverage(raw_disc, name), _coverage(raw_val, name)
        disc, val = _usable(raw_disc, name), _usable(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None), default=None)
        d1 = bool(
            disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.70
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
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r54": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R54 portfolio integration" if passed else "negative knowledge; do not deploy",
        "caveats": ["技术因子均在历史成员宇宙上重算", "beta_60d 使用当日收盘可得数据", "历史名单为月度快照"],
    }
    path = EV / "r53_price_volume_zoo.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
