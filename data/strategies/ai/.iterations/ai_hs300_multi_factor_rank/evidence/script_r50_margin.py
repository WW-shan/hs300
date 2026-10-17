# 输入说明: R50 — 历史 HS300 个股两融杠杆拥挤度 PIT 筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r50_margin.py
# 结果 JSON: 同目录 r50_margin.json。原始月末缓存 data/research/r50_margin/ (不提交)。
from __future__ import annotations

import importlib.util
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
r40 = _load("r40", "script_r40_survivorship.py")
r44 = _load("r44", "script_r44_holder.py")
r22 = r42.r22
DATA = r42.DATA
CACHE = DATA / "research" / "r50_margin"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {"margin_chg_1m": False, "margin_balance_ratio": False}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
MIN_MONTH = 60


def _month_ends(panel: pl.DataFrame) -> list:
    return (
        panel.select("date").unique().sort("date")
        .with_columns([pl.col("date").dt.year().alias("y"), pl.col("date").dt.month().alias("m")])
        .group_by(["y", "m"]).agg(pl.col("date").max().alias("date"))
        .filter((pl.col("date") >= pl.date(2021, 4, 1)) & (pl.col("date") <= pl.date(2026, 9, 30)))
        .sort("date")["date"].to_list()
    )


def _code_to_symbol(symbols: set[str]) -> dict[str, str]:
    inst = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    out = {
        str(code).zfill(6): symbol
        for symbol, code in zip(inst["symbol"].to_list(), inst["code"].to_list(), strict=False)
        if symbol in symbols
    }
    for symbol in symbols:
        out.setdefault(symbol[:6], symbol)
    return out


def _norm(raw, day, exchange: str, mapping: dict[str, str]) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8, "snapshot_date": pl.Date, "exchange": pl.Utf8,
        "margin_balance": pl.Float64, "financing_buy": pl.Float64,
        "financing_repay": pl.Float64, "short_qty": pl.Float64,
    }
    if raw is None or len(raw) == 0:
        return pl.DataFrame(schema=schema)
    if exchange == "sse":
        cols = {"标的证券代码": "code", "融资余额": "margin_balance", "融资买入额": "financing_buy", "融资偿还额": "financing_repay", "融券余量": "short_qty"}
    else:
        cols = {"证券代码": "code", "融资余额": "margin_balance", "融资买入额": "financing_buy", "融券余量": "short_qty"}
    available = [c for c in cols if c in raw.columns]
    if "code" not in [cols[c] for c in available]:
        raise ValueError(f"{exchange} unexpected columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(cols)
    code = frame["code"].cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6)
    frame = frame.with_columns([
        code.alias("_code6"),
        pl.lit(day).alias("snapshot_date"),
        pl.lit(exchange).alias("exchange"),
        *[
            pl.col(c).cast(pl.Float64, strict=False)
            for c in ("margin_balance", "financing_buy", "financing_repay", "short_qty")
            if c in frame.columns
        ],
    ]).with_columns(
        pl.col("_code6").replace_strict(mapping, default=None, return_dtype=pl.Utf8).alias("symbol")
    ).filter(pl.col("symbol").is_not_null())
    for col in schema:
        if col not in frame.columns:
            frame = frame.with_columns(pl.lit(None).cast(schema[col]).alias(col))
    return frame.select(list(schema)).unique().sort(["symbol"])


def _fetch_cache(month_ends: list, mapping: dict[str, str]) -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures = {}
    counts = {"cached": 0, "ok": 0, "empty": 0, "failed": 0}
    for index, day in enumerate(month_ends, start=1):
        for exchange, fn in (("sse", ak.stock_margin_detail_sse), ("szse", ak.stock_margin_detail_szse)):
            path = CACHE / f"{exchange}_{day.isoformat()}.parquet"
            if path.exists() and path.stat().st_size > 0:
                counts["cached"] += 1
                continue
            last_error = None
            for attempt in range(3):
                try:
                    raw = fn(date=day.strftime("%Y%m%d"))
                    frame = _norm(raw, day, exchange, mapping)
                    tmp = path.with_suffix(".tmp")
                    frame.write_parquet(tmp)
                    tmp.replace(path)
                    counts["ok" if frame.height else "empty"] += 1
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                    last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                    if attempt < 2:
                        time.sleep(0.7 * (attempt + 1))
            if last_error:
                counts["failed"] += 1
                failures[f"{exchange}_{day.isoformat()}"] = last_error
        if index % 5 == 0 or index == len(month_ends):
            print(f"margin fetch {index}/{len(month_ends)} {counts}", flush=True)
    manifest = {
        "month_ends": len(month_ends),
        "requests": len(month_ends) * 2,
        "cache_files": len(list(CACHE.glob("*.parquet"))),
        "failure_count": counts["failed"],
        "failure_rate": round(counts["failed"] / (len(month_ends) * 2), 6),
        "failures": dict(sorted(failures.items())),
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _snapshots(panel: pl.DataFrame) -> pl.DataFrame:
    frames = [pl.read_parquet(path) for path in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("两融缓存为空")
    margins = pl.concat(frames, how="diagonal_relaxed").group_by(["symbol", "snapshot_date"]).agg([
        pl.col("margin_balance").max().alias("margin_balance"),
        pl.col("financing_buy").sum().alias("financing_buy"),
        pl.col("financing_repay").sum().alias("financing_repay"),
        pl.col("short_qty").sum().alias("short_qty"),
    ]).sort(["symbol", "snapshot_date"])
    mv = panel.select(["symbol", "date", "float_mv"]).with_columns(pl.col("date").alias("snapshot_date"))
    margins = margins.join(mv, on=["symbol", "snapshot_date"], how="left")
    return margins.with_columns([
        pl.col("margin_balance").shift(1).over("symbol").alias("_prev_balance"),
        pl.col("snapshot_date").shift(1).over("symbol").alias("_prev_date"),
    ]).with_columns(
        (pl.col("snapshot_date") - pl.col("_prev_date")).dt.total_days().alias("_gap_days")
    ).with_columns([
        pl.when(
            pl.col("_gap_days").is_between(20, 45, closed="both")
            & (pl.col("_prev_balance") > 0) & (pl.col("margin_balance") > 0)
        ).then(pl.col("margin_balance") / pl.col("_prev_balance") - 1.0).otherwise(None).alias("margin_chg_1m"),
        pl.when((pl.col("float_mv") > 0) & (pl.col("margin_balance") >= 0))
        .then(pl.col("margin_balance") / pl.col("float_mv")).otherwise(None).alias("margin_balance_ratio"),
        pl.col("snapshot_date").dt.offset_by("1d").alias("available_date"),
    ]).drop(["_prev_balance", "_prev_date", "_gap_days"])


def _attach(reb: pl.DataFrame, snaps: pl.DataFrame) -> pl.DataFrame:
    joined = reb.sort(["symbol", "date"]).join_asof(
        snaps.select(["symbol", "available_date", "margin_chg_1m", "margin_balance_ratio"]).sort(["symbol", "available_date"]),
        left_on="date", right_on="available_date", by="symbol", strategy="backward", check_sortedness=False,
    ).with_columns((pl.col("date") - pl.col("available_date")).dt.total_days().alias("margin_age_days"))
    joined = joined.with_columns([
        pl.when(pl.col("margin_age_days").is_between(0, 45, closed="both"))
        .then(pl.col(name)).otherwise(None).alias(name)
        for name in FACTORS
    ])
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"),
            pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)).drop(["_lo", "_hi"])
    return joined


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge_min": 0, "min_month": MIN_MONTH}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge_min": int((grouped["valid"] >= MIN_MONTH).sum()),
        "min_month": MIN_MONTH,
    }


def _usable(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(pl.col("valid") >= MIN_MONTH)["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    memberships, _ = r40.membership_frame()
    symbols = set(memberships["symbol"].unique().to_list())
    months = _month_ends(panel)
    mapping = _code_to_symbol(symbols)
    manifest = _fetch_cache(months, mapping)
    snaps = _snapshots(panel)
    joined = _attach(reb, snaps)
    print("months", len(months), "snapshots", snaps.shape, "reb", reb.shape, "failures", manifest["failure_count"], flush=True)

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc = r42._period_frame(joined, DISCOVERY)
        raw_val = r42._period_frame(joined, VALIDATION)
        disc_cov, val_cov = _coverage(raw_disc, name), _coverage(raw_val, name)
        disc, val = _usable(raw_disc, name), _usable(raw_val, name)
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
            and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.60
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.60
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

    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "data": {"fetch": manifest, "snapshots": snaps.height, "symbols_with_snapshot": snaps["symbol"].n_unique(), "month_ends": len(months)},
        "benchmarks": r42.benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r51": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R51 portfolio/risk integration" if passed else "negative knowledge; do not deploy",
        "caveats": ["两融数据只覆盖可融资融券标的", "当前流通股本用于历史 ratio, 存在股本口径偏差", "月末数据按下一自然日可得", "历史名单为月度快照"],
    }
    path = EV / "r50_margin.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
