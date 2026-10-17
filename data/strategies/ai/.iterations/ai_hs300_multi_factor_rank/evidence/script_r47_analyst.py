# 输入说明: R47 — 历史 HS300 分析师评级水平与修正 PIT 因子筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r47_analyst.py
# 结果 JSON: 同目录 r47_analyst.json。原始逐日缓存 data/research/r47_analyst/ (不提交)。
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


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r44 = _load("r44", "script_r44_holder.py")
r22 = r42.r22
DATA = r42.DATA
CACHE = DATA / "research" / "r47_analyst"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "analyst_rating_90d": {"good_high": True, "min_month": 80, "min_cov": 0.70},
    "analyst_revision_90d": {"good_high": True, "min_month": 60, "min_cov": 0.50},
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
RETRIES = 2
RATING_SCORE = {"买入": 4.0, "增持": 3.0, "中性": 2.0, "减持": 1.0, "卖出": 0.0}
CHANGE_SCORE = {"调高": 1.0, "调低": -1.0, "维持": 0.0}


def _code_to_symbol(symbols: set[str]) -> dict[str, str]:
    inst = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    out = {
        str(code).zfill(6): symbol
        for symbol, code in zip(inst["symbol"].to_list(), inst["code"].to_list(), strict=False)
        if symbol in symbols
    }
    for symbol in symbols:
        code = symbol[:6]
        out.setdefault(code, symbol)
    return out


def _normalize(raw, report_date: date, mapping: dict[str, str], symbols: set[str]) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8, "report_date": pl.Date, "institution": pl.Utf8,
        "researcher": pl.Utf8, "rating": pl.Utf8, "prev_rating": pl.Utf8,
        "first_rating": pl.Utf8, "rating_change": pl.Utf8,
        "rating_score": pl.Float64, "rating_change_score": pl.Float64,
        "target_low": pl.Float64, "target_high": pl.Float64,
    }
    if raw is None or len(raw) == 0:
        return pl.DataFrame(schema=schema)
    columns = {
        "证券代码": "code", "发布日期": "publish", "研究机构简称": "institution",
        "研究员名称": "researcher", "投资评级": "rating", "前一次投资评级": "prev_rating",
        "是否首次评级": "first_rating", "评级变化": "rating_change",
        "目标价格-下限": "target_low", "目标价格-上限": "target_high",
    }
    available = [c for c in columns if c in raw.columns]
    if "证券代码" not in available or "投资评级" not in available:
        raise ValueError(f"unexpected columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(columns)
    for col in ("institution", "researcher", "rating", "prev_rating", "first_rating", "rating_change"):
        if col in frame.columns:
            frame = frame.with_columns(pl.col(col).cast(pl.Utf8))
    for col in ("target_low", "target_high"):
        if col in frame.columns:
            frame = frame.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    code = frame["code"].cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6)
    frame = frame.with_columns([
        code.alias("_code6"),
        pl.lit(report_date).alias("report_date"),
    ]).with_columns(
        pl.col("_code6").replace_strict(mapping, default=None, return_dtype=pl.Utf8).alias("symbol")
    ).filter(pl.col("symbol").is_in(symbols))
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    frame = frame.with_columns([
        pl.col("rating").replace_strict(RATING_SCORE, default=None, return_dtype=pl.Float64).alias("rating_score"),
        pl.col("rating_change").replace_strict(CHANGE_SCORE, default=None, return_dtype=pl.Float64).alias("rating_change_score"),
    ])
    for col in schema:
        if col not in frame.columns:
            frame = frame.with_columns(pl.lit(None).cast(schema[col]).alias(col))
    return frame.select(list(schema)).unique().sort(["symbol", "institution", "rating"])


def _fetch_one(day: date, mapping: dict[str, str], symbols: set[str]) -> tuple[date, str, str | None]:
    import akshare as ak

    path = CACHE / f"{day.isoformat()}.parquet"
    if path.exists() and path.stat().st_size > 0:
        return day, "cached", None
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            raw = ak.stock_rank_forecast_cninfo(date=day.strftime("%Y%m%d"))
            frame = _normalize(raw, day, mapping, symbols)
            tmp = path.with_suffix(".tmp")
            frame.write_parquet(tmp)
            tmp.replace(path)
            return day, "empty" if frame.is_empty() else "ok", None
        except Exception as exc:  # noqa: BLE001 - provider failures are recorded and retried
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < RETRIES:
                time.sleep(0.5 * (attempt + 1))
    return day, "failed", last_error


def _fetch_cache(dates: list[date], mapping: dict[str, str], symbols: set[str]) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    counts = {"cached": 0, "ok": 0, "empty": 0, "failed": 0}
    failures = {}
    # AkShare 的该接口底层含 mini_racer, 并发会触发原生崩溃; 保持单线程。
    for index, day in enumerate(dates, start=1):
        _day, status, error = _fetch_one(day, mapping, symbols)
        counts[status] += 1
        if error:
            failures[day.isoformat()] = error
        if index % 50 == 0 or index == len(dates):
            print(f"analyst fetch {index}/{len(dates)} {counts}", flush=True)
    cache_files = sorted(CACHE.glob("*.parquet"))
    nonempty_days = sum(1 for path in cache_files if pl.read_parquet(path).height > 0)
    manifest = {
        "requested_dates": len(dates),
        "cached_or_fetched": len(cache_files),
        "nonempty_days": nonempty_days,
        "empty_days": len(cache_files) - nonempty_days,
        "failure_count": counts["failed"],
        "failure_rate": round(counts["failed"] / len(dates), 6) if dates else 0.0,
        "failures": dict(sorted(failures.items())),
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _reports() -> pl.DataFrame:
    frames = [pl.read_parquet(path) for path in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("分析师报告缓存为空")
    return pl.concat(frames, how="diagonal_relaxed").with_columns(
        pl.col("report_date").cast(pl.Date)
    ).unique().sort(["report_date", "symbol", "institution", "rating"])


def _daily(reports: pl.DataFrame) -> pl.DataFrame:
    return (
        reports.filter(pl.col("rating_score").is_not_null())
        .with_columns(pl.col("report_date").dt.offset_by("1d").alias("available_date"))
        .group_by(["symbol", "available_date"])
        .agg([
            pl.len().alias("report_count"),
            pl.col("rating_score").count().alias("rated_count"),
            pl.col("rating_score").sum().alias("rating_sum"),
        ])
        .sort(["symbol", "available_date"])
        .with_columns([
            pl.col("rated_count").cum_sum().over("symbol").alias("cum_count"),
            pl.col("rating_sum").cum_sum().over("symbol").alias("cum_sum"),
        ])
    )


def _asof_cut(reb: pl.DataFrame, daily: pl.DataFrame, days: int, suffix: str) -> pl.DataFrame:
    left = reb.with_columns((pl.col("date") - pl.duration(days=days)).alias("_cut"))
    left = left.sort(["symbol", "_cut"])
    right = daily.select(["symbol", "available_date", "cum_count", "cum_sum"]).sort(["symbol", "available_date"])
    out = left.join_asof(
        right,
        left_on="_cut",
        right_on="available_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).rename({"cum_count": f"cum_count_{suffix}", "cum_sum": f"cum_sum_{suffix}"})
    return out.drop(["_cut", "available_date"])


def _attach(reb: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    out = reb.sort(["symbol", "date"])
    for days, suffix in ((0, "t"), (90, "t90"), (180, "t180")):
        out = out.join(_asof_cut(out, daily, days, suffix).select(
            ["symbol", "date", f"cum_count_{suffix}", f"cum_sum_{suffix}"]
        ), on=["symbol", "date"], how="left")
    out = out.with_columns([
        pl.col(f"cum_count_{c}").fill_null(0).cast(pl.Int64).alias(f"cum_count_{c}")
        for c in ("t", "t90", "t180")
    ] + [
        pl.col(f"cum_sum_{c}").fill_null(0.0).cast(pl.Float64).alias(f"cum_sum_{c}")
        for c in ("t", "t90", "t180")
    ])
    out = out.with_columns([
        (pl.col("cum_count_t") - pl.col("cum_count_t90")).alias("_count_90"),
        (pl.col("cum_sum_t") - pl.col("cum_sum_t90")).alias("_sum_90"),
        (pl.col("cum_count_t90") - pl.col("cum_count_t180")).alias("_count_prev"),
        (pl.col("cum_sum_t90") - pl.col("cum_sum_t180")).alias("_sum_prev"),
    ]).with_columns([
        pl.when(pl.col("_count_90") >= 3).then(pl.col("_sum_90") / pl.col("_count_90")).otherwise(None).alias("analyst_rating_90d"),
        pl.when((pl.col("_count_90") >= 2) & (pl.col("_count_prev") >= 2))
        .then(pl.col("_sum_90") / pl.col("_count_90") - pl.col("_sum_prev") / pl.col("_count_prev"))
        .otherwise(None).alias("analyst_revision_90d"),
    ])
    return out.drop(["_count_90", "_sum_90", "_count_prev", "_sum_prev"])


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


def _report_summary(reports: pl.DataFrame) -> dict:
    return {
        "hs300_reports": reports.height,
        "symbols": reports["symbol"].n_unique(),
        "report_dates": reports["report_date"].n_unique(),
        "rating_distribution": reports.group_by("rating").len().sort("len", descending=True).to_dicts(),
        "institutions": reports.group_by("institution").len().sort(["len", "institution"], descending=[True, False]).head(12).to_dicts(),
        "target_high_coverage": round(float(reports["target_high"].is_not_null().mean()), 4),
        "rating_change_coverage": round(float(reports["rating_change_score"].is_not_null().mean()), 4),
    }


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = set(reb["symbol"].unique().to_list())
    mapping = _code_to_symbol(symbols)
    dates = sorted(day for day in panel["date"].unique().to_list() if day >= DISCOVERY[0])
    manifest = _fetch_cache(dates, mapping, symbols)
    reports = _reports()
    daily = _daily(reports)
    joined = _attach(reb, daily)
    print("panel", panel.shape, "reb", reb.shape, "reports", reports.height, "daily", daily.shape, "fetch", manifest["failure_count"], flush=True)

    records = {}
    discovery_p = []
    for index, (name, spec) in enumerate(FACTORS.items()):
        min_month = spec["min_month"]
        raw_disc = r42._period_frame(joined, DISCOVERY)
        raw_val = r42._period_frame(joined, VALIDATION)
        disc_cov = _coverage(raw_disc, name, min_month)
        val_cov = _coverage(raw_val, name, min_month)
        disc = _usable(raw_disc, name, min_month)
        val = _usable(raw_val, name, min_month)
        sign = 1.0 if spec["good_high"] else -1.0
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
                value for value in (r22.mean_abs_corr(full, "_signed", control) for control in CONTROLS)
                if value is not None
            ),
            default=None,
        )
        d1 = bool(
            manifest["failure_rate"] <= 0.10
            and disc_cov["mean"] is not None and disc_cov["mean"] >= spec["min_cov"]
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
            "direction": "high" if spec["good_high"] else "low",
            "coverage_discovery": disc_cov,
            "coverage_validation": val_cov,
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing_max": corr,
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
            "rating_windows_days": [90, 180],
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {"fetch": manifest, "reports": _report_summary(reports), "daily_rows": daily.height},
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r48": passed,
        "decision": {
            "D1_count": sum(bool(records[n]["D1"]) for n in FACTORS),
            "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS),
            "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS),
            "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS),
            "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS),
            "all_pass_count": len(passed),
        },
        "recommendation": "R48 portfolio integration" if passed else "negative knowledge; do not deploy",
        "caveats": [
            "分析师评级来自卖方报告, 可能存在选择性披露和覆盖偏差",
            "发布日期按次日可得严格处理, 但供应商可能回填修订",
            "历史成员为月度快照, 当前行业仅用于报告",
            "目标价仅报告, 未进入本轮因子",
        ],
    }
    path = EV / "r47_analyst.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
