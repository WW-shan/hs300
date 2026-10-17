# 输入说明: R45 — 真单季度股东户数变化 PIT 筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r45_holder_1q.py
# 结果 JSON: 同目录 r45_holder_1q.json。原始缓存复用 data/research/r44_holder/ (不提交)。
from __future__ import annotations

import hashlib
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
r44 = _load("r44", "script_r44_holder.py")
r22 = r42.r22
DATA = r42.DATA
CACHE = r44.CACHE
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTOR = "holder_chg_1q"
MAX_AGE_DAYS = 200
MIN_VALID_MONTH = 80
MIN_USABLE_MONTHS = 48
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _raw_records() -> pl.DataFrame:
    frames = [pl.read_parquet(path) for path in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("股东户数缓存为空; 先运行 R44")
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .sort(["symbol", "period_date", "announce_date"])
        .unique(subset=["symbol", "period_date"], keep="last")
    )


def _signals(raw: pl.DataFrame) -> pl.DataFrame:
    left = raw.select(["symbol", "period_date", "announce_date", "holder_count"])
    right = raw.select([
        "symbol",
        pl.col("period_date").alias("prev_period"),
        pl.col("announce_date").alias("prev_announce_date"),
        pl.col("holder_count").alias("prev_count"),
    ])
    matches = (
        left.join(right, on="symbol", how="inner")
        .with_columns((pl.col("period_date") - pl.col("prev_period")).dt.total_days().alias("gap_days"))
        .filter(
            pl.col("gap_days").is_between(75, 110, closed="both")
            & (pl.col("prev_count") > 0)
            & (pl.col("holder_count") > 0)
            & (pl.col("prev_announce_date") <= pl.col("announce_date"))
        )
        .with_columns((pl.col("gap_days") - 90).abs().alias("_gap_distance"))
        .sort(
            ["symbol", "period_date", "_gap_distance", "prev_period"],
            descending=[False, False, False, True],
        )
        .unique(subset=["symbol", "period_date"], keep="first")
    )
    return (
        matches.with_columns([
            (pl.col("holder_count") / pl.col("prev_count") - 1.0).alias(FACTOR),
            pl.col("announce_date").dt.offset_by("1d").alias("available_date"),
        ])
        .select(["symbol", "period_date", "available_date", FACTOR, "gap_days"])
        .sort(["symbol", "available_date", "period_date"])
        .unique(subset=["symbol", "available_date"], keep="last")
        .sort(["symbol", "available_date"])
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
    ).with_columns(
        pl.when(pl.col("holder_age_days").is_between(0, MAX_AGE_DAYS, closed="both"))
        .then(pl.col(FACTOR)).otherwise(None).alias(FACTOR)
    )
    return (
        joined.with_columns([
            pl.col(FACTOR).quantile(0.01).over("date").alias("_lo"),
            pl.col(FACTOR).quantile(0.99).over("date").alias("_hi"),
        ])
        .with_columns(pl.col(FACTOR).clip(pl.col("_lo"), pl.col("_hi")).alias(FACTOR))
        .drop(["_lo", "_hi"])
    )


def _digest(signals: pl.DataFrame) -> str:
    h = hashlib.sha256()
    for row in signals.select(["symbol", "available_date", FACTOR]).sort(["symbol", "available_date"]).iter_rows():
        h.update(repr(row).encode())
    return h.hexdigest()


def _age_report(frame: pl.DataFrame) -> dict:
    vals = frame.filter(pl.col(FACTOR).is_not_null())["holder_age_days"].drop_nulls().to_list()
    if not vals:
        return {"n": 0}
    vals = sorted(vals)
    def q(p: float) -> float:
        return float(vals[min(len(vals) - 1, int(p * (len(vals) - 1)))])
    return {"n": len(vals), "median": q(0.50), "p90": q(0.90), "p99": q(0.99), "max": vals[-1]}


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    raw = _raw_records()
    signals = _signals(raw)
    joined = _attach(reb, signals)
    manifest = json.loads((CACHE / "manifest.json").read_text(encoding="utf-8"))
    print("panel", panel.shape, "reb", reb.shape, "raw", raw.shape, "signals", signals.shape, flush=True)

    raw_disc = r42._period_frame(joined, DISCOVERY)
    raw_val = r42._period_frame(joined, VALIDATION)
    disc_coverage = r44._coverage(raw_disc, FACTOR)
    val_coverage = r44._coverage(raw_val, FACTOR)
    disc, disc_months = r44._usable_months(raw_disc, FACTOR)
    val, val_months = r44._usable_months(raw_val, FACTOR)
    disc = disc.with_columns((pl.col(FACTOR) * -1.0).alias("_signed"))
    val = val.with_columns((pl.col(FACTOR) * -1.0).alias("_signed"))
    full = joined.with_columns((pl.col(FACTOR) * -1.0).alias("_signed"))
    disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED, one_sided=False)
    val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000, one_sided=True)
    full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000, one_sided=True)
    disc_group = r42._group_stats(disc, "_signed")
    val_group = r42._group_stats(val, "_signed")
    corr = max(
        (
            value for value in (r22.mean_abs_corr(full, "_signed", control) for control in CONTROLS)
            if value is not None
        ),
        default=None,
    )
    vendor = r44._attach(reb, r44._load_signals()).select(["symbol", "date", "holder_chg_qoq"])
    corr_vendor = r22.mean_abs_corr(full.join(vendor, on=["symbol", "date"], how="left"), "_signed", "holder_chg_qoq")

    d1 = bool(
        manifest["failure_rate"] <= 0.10
        and disc_coverage["mean"] is not None and disc_coverage["mean"] >= 0.70
        and val_coverage["mean"] is not None and val_coverage["mean"] >= 0.70
        and disc_months.height >= 24 and val_months.height >= 24
        and disc_months.height + val_months.height >= MIN_USABLE_MONTHS
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
    f3 = bool(
        disc_stats["perm_p"] is not None and disc_stats["perm_p"] <= 0.10
        and val_stats["perm_p"] is not None and val_stats["perm_p"] <= 0.05
    )
    f4 = bool(corr is not None and corr < 0.7)
    all_pass = bool(d1 and f1 and f2 and f3 and f4)
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factor": FACTOR,
            "quarter_gap_days": [75, 110],
            "target_gap_days": 90,
            "max_age_days": MAX_AGE_DAYS,
            "min_valid_month": MIN_VALID_MONTH,
            "min_usable_months": MIN_USABLE_MONTHS,
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "raw_records": raw.height,
            "matched_records": signals.height,
            "match_coverage": round(signals.height / raw.height, 6),
            "symbols_with_signal": signals["symbol"].n_unique(),
            "gap_days": {
                "median": float(signals["gap_days"].median()),
                "p10": float(signals["gap_days"].quantile(0.10)),
                "p90": float(signals["gap_days"].quantile(0.90)),
            },
            "signal_digest": _digest(signals),
            "age_report": _age_report(joined),
            "fetch": {
                "symbols": manifest["symbols"],
                "cached": manifest["cached"],
                "failure_count": manifest["failure_count"],
                "failure_rate": manifest["failure_rate"],
            },
        },
        "coverage": {"discovery": disc_coverage, "validation": val_coverage},
        "factor": {
            "direction": "low",
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing_max": corr,
            "corr_vendor_qoq": corr_vendor,
            "industry": r44._industry_report(full, FACTOR),
        },
        "gates": {"D1": d1, "F1": f1, "F2": f2, "F3": f3, "F4": f4, "all_pass": all_pass},
        "benchmarks": r42.benchmark_metrics(reb),
        "recommendation": "R46 portfolio integration" if all_pass else "negative knowledge; do not deploy",
        "caveats": [
            "股东户数由供应商回填, 公告日可能不是首次披露的严格时点",
            "75~110 天是预先固定的单季度匹配窗口, 不是样本内参数搜索",
            "历史名单为月度快照, 不是逐日调样流水",
        ],
    }
    path = EV / "r45_holder_1q.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("digest", result["data"]["signal_digest"], flush=True)
    print("IC", disc_stats["mean"], val_stats["mean"], "groups", disc_group, val_group, flush=True)
    print("gates", result["gates"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
