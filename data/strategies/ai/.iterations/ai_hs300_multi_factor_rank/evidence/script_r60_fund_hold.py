# 输入说明: R60 — 历史 HS300 基金持仓水平与季度加仓 PIT 筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r60_fund_hold.py
# 结果 JSON: 同目录 r60_fund_hold.json。原始逐季度缓存 data/research/r60_fund_hold/ (不提交)。
from __future__ import annotations

import importlib.util
import json
import math
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
r53 = _load("r53", "script_r53_price_volume_zoo.py")
r55 = _load("r55", "script_r55_limit_structure.py")
r22 = r42.r22
DATA = r42.DATA
CACHE = DATA / "research" / "r60_fund_hold"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {"fund_holding_ratio": True, "fund_holding_chg_1q": True}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
MIN_MONTH = {"fund_holding_ratio": 80, "fund_holding_chg_1q": 60}
QUARTER_ENDS = [
    date(2020, 12, 31), date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30),
    date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30),
    date(2022, 12, 31), date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30),
    date(2023, 12, 31), date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30),
    date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
    date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
]


def _mapping(symbols: set[str]) -> dict[str, str]:
    inst = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code", "float_shares"])
    out = {str(code).zfill(6): symbol for symbol, code in zip(inst["symbol"].to_list(), inst["code"].to_list(), strict=False) if symbol in symbols}
    for symbol in symbols:
        out.setdefault(symbol[:6], symbol)
    return out


def _normalize(raw, quarter: date, mapping: dict[str, str], symbols: set[str]) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8, "quarter_date": pl.Date, "fund_count": pl.Int64,
        "holding_shares": pl.Float64, "holding_value": pl.Float64, "vendor_change_pct": pl.Float64,
    }
    if raw is None or len(raw) == 0:
        return pl.DataFrame(schema=schema)
    cols = {"股票代码": "code", "持有基金家数": "fund_count", "持股总数": "holding_shares", "持股市值": "holding_value", "持股变动比例": "vendor_change_pct"}
    available = [c for c in cols if c in raw.columns]
    if "股票代码" not in available:
        raise ValueError(f"unexpected fund hold columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(cols)
    code = frame["code"].cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6)
    frame = frame.with_columns([
        code.alias("_code6"), pl.lit(quarter).alias("quarter_date"),
        *[pl.col(c).cast(pl.Float64, strict=False) for c in ("holding_shares", "holding_value", "vendor_change_pct") if c in frame.columns],
        pl.col("fund_count").cast(pl.Int64, strict=False) if "fund_count" in frame.columns else pl.lit(None).cast(pl.Int64).alias("fund_count"),
    ]).with_columns(pl.col("_code6").replace_strict(mapping, default=None, return_dtype=pl.Utf8).alias("symbol"))
    frame = frame.filter(pl.col("symbol").is_in(symbols) & (pl.col("holding_shares") > 0))
    for col in schema:
        if col not in frame.columns:
            frame = frame.with_columns(pl.lit(None).cast(schema[col]).alias(col))
    return frame.select(list(schema)).unique().sort(["symbol", "quarter_date"])


def _fetch(symbols: set[str]) -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    mapping = _mapping(symbols)
    failures = {}
    fetched = 0
    cached = 0
    for index, quarter in enumerate(QUARTER_ENDS, start=1):
        path = CACHE / f"{quarter.isoformat()}.parquet"
        if path.exists() and path.stat().st_size > 0:
            cached += 1
            continue
        last_error = None
        for attempt in range(3):
            try:
                raw = ak.stock_report_fund_hold(symbol="基金持仓", date=quarter.strftime("%Y%m%d"))
                frame = _normalize(raw, quarter, mapping, symbols)
                tmp = path.with_suffix(".tmp")
                frame.write_parquet(tmp)
                tmp.replace(path)
                fetched += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        if last_error:
            failures[quarter.isoformat()] = last_error
        print(f"fund fetch {index}/{len(QUARTER_ENDS)} cached={cached} fetched={fetched} failed={len(failures)}", flush=True)
    cache_files = sorted(CACHE.glob("*.parquet"))
    nonempty = sum(1 for p in cache_files if pl.read_parquet(p).height > 0)
    manifest = {
        "quarters": len(QUARTER_ENDS), "cache_files": len(cache_files), "nonempty_quarters": nonempty,
        "fetched_this_run": fetched, "cached_this_run": cached, "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(QUARTER_ENDS), 6), "failures": dict(sorted(failures.items())),
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _quarter_data() -> pl.DataFrame:
    frames = [pl.read_parquet(p) for p in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("基金持仓缓存为空")
    return pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "quarter_date"]).unique(subset=["symbol", "quarter_date"], keep="last")


def _attach(reb: pl.DataFrame, data: pl.DataFrame) -> pl.DataFrame:
    inst = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "float_shares"]).with_columns(pl.col("float_shares").cast(pl.Float64, strict=False))
    data = data.join(inst, on="symbol", how="left").sort(["symbol", "quarter_date"]).with_columns([
        pl.col("holding_shares").shift(1).over("symbol").alias("_prev_shares"),
        pl.col("quarter_date").shift(1).over("symbol").alias("_prev_quarter"),
    ]).with_columns([
        (pl.col("quarter_date") - pl.col("_prev_quarter")).dt.total_days().alias("_gap"),
    ]).with_columns([
        pl.when((pl.col("float_shares") > 0) & (pl.col("holding_shares") >= 0)).then(pl.col("holding_shares") / pl.col("float_shares")).otherwise(None).alias("fund_holding_ratio"),
        pl.when((pl.col("_gap").is_between(80, 100, closed="both")) & (pl.col("_prev_shares") > 0) & (pl.col("holding_shares") > 0))
        .then(pl.col("holding_shares") / pl.col("_prev_shares") - 1.0).otherwise(None).alias("fund_holding_chg_1q"),
        pl.col("quarter_date").dt.offset_by("45d").alias("available_date"),
    ]).drop(["_prev_shares", "_prev_quarter", "_gap"])
    joined = reb.sort(["symbol", "date"]).join_asof(
        data.select(["symbol", "available_date", *FACTORS]).sort(["symbol", "available_date"]),
        left_on="date", right_on="available_date", by="symbol", strategy="backward", check_sortedness=False,
    ).with_columns((pl.col("date") - pl.col("available_date")).dt.total_days().alias("fund_age_days"))
    joined = joined.with_columns([
        pl.when(pl.col("fund_age_days").is_between(0, 180, closed="both")).then(pl.col(name)).otherwise(None).alias(name)
        for name in FACTORS
    ])
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"), pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)).drop(["_lo", "_hi"])
    return joined


def _coverage(frame: pl.DataFrame, col: str, min_month: int) -> dict:
    g = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if g.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge_min": 0, "min_month": min_month}
    return {"mean": round(float((g["valid"] / g["eligible"]).mean()), 6), "min_valid": int(g["valid"].min()), "months": g.height, "months_ge_min": int((g["valid"] >= min_month).sum()), "min_month": min_month}


def _usable(frame: pl.DataFrame, col: str, min_month: int) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(pl.col("valid") >= min_month)["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    _panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = set(memberships["symbol"].unique().to_list())
    manifest = _fetch(symbols)
    data = _quarter_data()
    joined = _attach(reb, data)
    print("reb", reb.shape, "fund rows", data.shape, "fetch", manifest, flush=True)
    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc, raw_val = r42._period_frame(joined, DISCOVERY), r42._period_frame(joined, VALIDATION)
        mm = MIN_MONTH[name]
        disc_cov, val_cov = _coverage(raw_disc, name, mm), _coverage(raw_val, name, mm)
        disc, val = _usable(raw_disc, name, mm), _usable(raw_val, name, mm)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r55._ic_stats_safe(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r55._ic_stats_safe(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r55._ic_stats_safe(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None and math.isfinite(v)), default=None)
        d1 = bool(manifest["failure_rate"] <= 0.10 and disc_cov["mean"] is not None and disc_cov["mean"] >= (0.70 if name == "fund_holding_ratio" else 0.50) and val_cov["mean"] is not None and val_cov["mean"] >= (0.70 if name == "fund_holding_ratio" else 0.50) and disc_cov["months_ge_min"] >= 24 and val_cov["months_ge_min"] >= 24 and disc_cov["months_ge_min"] + val_cov["months_ge_min"] >= 48)
        f1 = bool(disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03 and val_stats["mean"] is not None and val_stats["mean"] >= 0.02 and disc_stats["mean"] > 0 and val_stats["mean"] > 0 and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4)
        f2 = bool(disc_group and val_group and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7 and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0)
        f4 = bool(corr is not None and corr < 0.7)
        records[name] = {"direction": "high" if good_high else "low", "coverage_discovery": disc_cov, "coverage_validation": val_cov, "discovery": disc_stats, "validation": val_stats, "full": full_stats, "discovery_groups": disc_group, "validation_groups": val_group, "corr_existing_max": corr, "D1": d1, "F1": f1, "F2": f2, "F4": f4}
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
    data_audit = {
        "fetch": manifest, "fund_rows": data.height, "symbols_with_fund_rows": data["symbol"].n_unique(),
        "quarters": data["quarter_date"].n_unique(), "vendor_change_nonnull": round(float(data["vendor_change_pct"].is_not_null().mean()), 4),
    }
    result = {"window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED}, "data": data_audit, "factors": records, "passed": passed, "adopted_for_r61": passed, "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)}, "recommendation": "R61 portfolio integration" if passed else "negative knowledge; do not deploy", "caveats": ["季度末 +45 天保守可得", "只使用季度末持股, 不使用目标价/基金名称", "当前流通股本用于历史比率", "历史名单为月度快照"]}
    path = EV / "r60_fund_hold.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
