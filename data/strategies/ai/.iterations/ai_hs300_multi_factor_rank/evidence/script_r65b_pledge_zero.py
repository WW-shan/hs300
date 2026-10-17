# 输入说明: R65b — 股权质押快照“成功快照中缺席=0”事后口径纠偏。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r65b_pledge_zero.py
# 结果 JSON: 同目录 r65b_pledge_zero.json。原始快照缓存沿用 data/research/r65_pledge/ (不提交)。
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


r65 = _load("r65", "script_r65_pledge.py")
r42 = r65.r42
r22 = r65.r22
DATA = r65.DATA
DISCOVERY = r65.DISCOVERY
VALIDATION = r65.VALIDATION
RNG_SEED = r65.RNG_SEED
FACTORS = r65.FACTORS
CONTROLS = r65.CONTROLS
COVERAGE_MIN = r65.COVERAGE_MIN


def _snapshots_zero(panel: pl.DataFrame, dates: list, symbols: set[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    for day in dates:
        path = r65.CACHE / f"{day.isoformat()}.parquet"
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
            if frame.height:
                frames.append(frame)
    if not frames:
        raise RuntimeError("质押快照缓存为空")
    raw = pl.concat(frames, how="diagonal_relaxed")
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    quoted = raw.select([
        pl.col("股票代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
        pl.col("交易日期").cast(pl.Date).alias("snapshot_date"),
        pl.col("质押比例").cast(pl.Float64, strict=False).alias("pledge_ratio_latest"),
    ]).join(instruments, on="code", how="inner").filter(pl.col("symbol").is_in(symbols) & pl.col("pledge_ratio_latest").is_not_null())
    quoted = quoted.unique(subset=["symbol", "snapshot_date"], keep="last")
    active = panel.filter(pl.col("date").is_in(dates)).select(["symbol", pl.col("date").alias("snapshot_date")]).unique()
    frame = active.join(quoted, on=["symbol", "snapshot_date"], how="left").with_columns(
        pl.col("pledge_ratio_latest").fill_null(0.0)
    ).sort(["symbol", "snapshot_date"])
    matched = frame.join(quoted.select(["symbol", "snapshot_date", pl.lit(True).alias("_vendor_row")]), on=["symbol", "snapshot_date"], how="left")
    zero_filled = matched["_vendor_row"].is_null().sum()
    frame = frame.with_columns([
        pl.col("pledge_ratio_latest").shift(1).over("symbol").alias("_prev_ratio"),
        pl.col("snapshot_date").shift(1).over("symbol").alias("_prev_date"),
    ]).with_columns((pl.col("snapshot_date") - pl.col("_prev_date")).dt.total_days().alias("_gap_days"))
    frame = frame.with_columns(
        pl.when(pl.col("_gap_days").is_between(20, 40, closed="both"))
        .then(pl.col("pledge_ratio_latest") - pl.col("_prev_ratio"))
        .otherwise(None).alias("pledge_ratio_chg_1m")
    ).drop(["_prev_ratio", "_prev_date", "_gap_days"])
    audit = {
        "raw_rows": raw.height, "active_member_pairs": active.height,
        "vendor_rows_matched": quoted.height, "zero_filled_rows": int(zero_filled),
        "zero_fill_rate": round(float(zero_filled / active.height), 6) if active.height else 0.0,
        "symbols": frame["symbol"].n_unique(), "snapshots": frame["snapshot_date"].n_unique(),
        "snapshot_min": str(frame["snapshot_date"].min()), "snapshot_max": str(frame["snapshot_date"].max()),
        "change_nonnull_rate": round(float(frame["pledge_ratio_chg_1m"].is_not_null().mean()), 6),
        "ratio_distribution": {
            "min": round(float(frame["pledge_ratio_latest"].min()), 4),
            "median": round(float(frame["pledge_ratio_latest"].median()), 4),
            "p90": round(float(frame["pledge_ratio_latest"].quantile(0.90)), 4),
            "max": round(float(frame["pledge_ratio_latest"].max()), 4),
        },
    }
    return frame, audit


def main() -> int:
    panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = set(memberships["symbol"].unique().to_list())
    dates, profile = r65._month_dates()
    manifest, dates = r65._fetch(dates)
    snapshots, snapshot_audit = _snapshots_zero(panel, dates, symbols)
    joined = r65._attach(reb, snapshots)
    print("reb", reb.shape, "profile", profile.shape, "snapshots", snapshots.shape, "manifest", manifest, flush=True)

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc, raw_val = r42._period_frame(joined, DISCOVERY), r42._period_frame(joined, VALIDATION)
        disc_cov, val_cov = r65._coverage(raw_disc, name), r65._coverage(raw_val, name)
        disc, val = r65._usable(raw_disc, name), r65._usable(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None), default=None)
        d1 = bool(
            manifest["failure_rate"] <= 0.10
            and disc_cov["mean"] is not None and disc_cov["mean"] >= COVERAGE_MIN[name]
            and val_cov["mean"] is not None and val_cov["mean"] >= COVERAGE_MIN[name]
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
        print(f"{name}: D1={d1} cov={disc_cov['mean']}/{val_cov['mean']} IC={disc_stats['mean']}/{val_stats['mean']} rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} p={disc_stats['perm_p']}", flush=True)

    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (name, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[name]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec["D1"] and rec["F1"] and rec["F2"] and rec["F3"] and rec["F4"])
        if rec["all_pass"]:
            passed.append(name)
    persistent_manifest = {k: v for k, v in manifest.items() if k not in {"cached_this_run", "fetched_this_run"}}
    result = {
        "post_hoc": True,
        "clean_holdout": False,
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "rules": {"snapshot_age_days": [1, 14], "zero_fill_missing_vendor_rows": True, "winsorize": [0.01, 0.99]},
        "data": {"fetch": persistent_manifest, "snapshots": snapshot_audit},
        "factors": records,
        "passed": passed,
        "adopted_for_r66": [],
        "candidate_post_hoc": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "post-hoc candidate only; requires future/independent validation" if passed else "negative knowledge; pledge dimension closed",
        "caveats": ["R65 已看过结果, 本口径不是独立样本外", "只对当天有交易行的历史成员补零", "未上市/退市/停牌不补零", "历史名单为月度快照"],
    }
    path = EV / "r65b_pledge_zero.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
