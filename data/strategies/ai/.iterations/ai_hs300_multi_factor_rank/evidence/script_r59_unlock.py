# 输入说明: R59 — 历史 HS300 已发生限售解禁供给因子。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r59_unlock.py
# 结果 JSON: 同目录 r59_unlock.json。原始逐股缓存 data/research/r59_unlock/ (不提交)。
from __future__ import annotations

import bisect
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
r42 = _load("r42", "script_r42_historical_factor_screen.py")
r50 = _load("r50", "script_r50_margin.py")
r53 = _load("r53", "script_r53_price_volume_zoo.py")
DATA = r42.DATA
CACHE = DATA / "research" / "r59_unlock"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
END = r42.END
FACTORS = {"unlock_recent_60d": False, "unlock_recent_120d": False}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _normalize(raw, symbol: str) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8, "unlock_date": pl.Date, "actual_unlock_qty": pl.Float64,
        "actual_unlock_value": pl.Float64, "total_mv_ratio": pl.Float64,
        "float_mv_ratio": pl.Float64, "unlock_type": pl.Utf8,
    }
    if raw is None or len(raw) == 0:
        return pl.DataFrame(schema=schema)
    cols = {
        "解禁时间": "unlock_date", "实际解禁数量": "actual_unlock_qty",
        "实际解禁数量市值": "actual_unlock_value", "占总市值比例": "total_mv_ratio",
        "占流通市值比例": "float_mv_ratio", "限售股类型": "unlock_type",
    }
    available = [c for c in cols if c in raw.columns]
    if "解禁时间" not in available:
        raise ValueError(f"unexpected unlock columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(cols)
    frame = frame.with_columns([
        pl.lit(symbol).alias("symbol"),
        pl.col("unlock_date").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        *[
            pl.col(c).cast(pl.Float64, strict=False)
            for c in ("actual_unlock_qty", "actual_unlock_value", "total_mv_ratio", "float_mv_ratio")
            if c in frame.columns
        ],
    ])
    for col in schema:
        if col not in frame.columns:
            frame = frame.with_columns(pl.lit(None).cast(schema[col]).alias(col))
    return frame.select(list(schema)).filter(pl.col("unlock_date").is_not_null()).unique().sort("unlock_date")


def _fetch(symbols: list[str]) -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures = {}
    ok = empty = cached = 0
    for index, symbol in enumerate(symbols, start=1):
        path = CACHE / f"{symbol}.parquet"
        if path.exists() and path.stat().st_size > 0:
            cached += 1
            continue
        last_error = None
        for attempt in range(3):
            try:
                raw = ak.stock_restricted_release_queue_em(symbol=symbol[:6])
                frame = _normalize(raw, symbol)
                tmp = path.with_suffix(".tmp")
                frame.write_parquet(tmp)
                tmp.replace(path)
                if frame.height:
                    ok += 1
                else:
                    empty += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if last_error:
            failures[symbol] = last_error
        if index % 25 == 0 or index == len(symbols):
            print(f"unlock fetch {index}/{len(symbols)} ok={ok} empty={empty} cached={cached} failed={len(failures)}", flush=True)
    cache_files = sorted(CACHE.glob("*.parquet"))
    nonempty_cached = sum(1 for path in cache_files if pl.read_parquet(path).height > 0)
    manifest = {
        "symbols": len(symbols),
        "cache_files": len(cache_files),
        "fetched_this_run": ok + empty,
        "cached_this_run": cached,
        "nonempty": nonempty_cached,
        "empty": len(cache_files) - nonempty_cached,
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6),
        "failures": dict(sorted(failures.items())),
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _events() -> tuple[pl.DataFrame, dict]:
    frames = [pl.read_parquet(path) for path in sorted(CACHE.glob("*.parquet"))]
    if not frames:
        raise RuntimeError("解禁缓存为空")
    events = pl.concat(frames, how="diagonal_relaxed").filter(
        (pl.col("unlock_date") >= date(2020, 12, 1)) & (pl.col("unlock_date") <= END)
    ).group_by(["symbol", "unlock_date"]).agg([
        pl.col("float_mv_ratio").sum().alias("float_mv_ratio"),
        pl.col("actual_unlock_value").sum().alias("actual_unlock_value"),
        pl.col("actual_unlock_qty").sum().alias("actual_unlock_qty"),
    ]).sort(["symbol", "unlock_date"])
    audit = {
        "events": events.height,
        "symbols_with_event": events["symbol"].n_unique(),
        "date_min": str(events["unlock_date"].min()) if events.height else None,
        "date_max": str(events["unlock_date"].max()) if events.height else None,
        "events_2021_2026": events.filter((pl.col("unlock_date") >= date(2021, 6, 1)) & (pl.col("unlock_date") <= END)).height,
        "ratio_nonnull": round(float(events["float_mv_ratio"].is_not_null().mean()), 4) if events.height else None,
    }
    return events, audit


def _attach(reb: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    event_map = {}
    for keys, group in events.group_by("symbol"):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        event_map[symbol] = (group["unlock_date"].to_list(), group["float_mv_ratio"].fill_null(0.0).to_list())
    dates = reb["date"].to_list()
    symbols = reb["symbol"].to_list()
    out = []
    for symbol, day in zip(symbols, dates, strict=False):
        ed, ratios = event_map.get(symbol, ([], []))
        vals = {}
        for label, window in (("unlock_recent_60d", 60), ("unlock_recent_120d", 120)):
            lo = bisect.bisect_right(ed, day - __import__("datetime").timedelta(days=window))
            hi = bisect.bisect_right(ed, day)
            vals[label] = sum(ratios[lo:hi]) if hi > lo else 0.0
        out.append(vals)
    return reb.with_columns([
        pl.Series(name, [row[name] for row in out]) for name in FACTORS
    ])


def _coverage_data(reb: pl.DataFrame, events: pl.DataFrame) -> dict:
    nonzero = {}
    for name in FACTORS:
        grouped = reb.group_by("date").agg([
            pl.len().alias("eligible"), (pl.col(name) > 0).sum().alias("nonzero"),
        ])
        nonzero[name] = {
            "nonzero_mean": round(float((grouped["nonzero"] / grouped["eligible"]).mean()), 4),
            "nonzero_months": int((grouped["nonzero"] > 0).sum()),
        }
    return nonzero


def main() -> int:
    _panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = sorted(memberships["symbol"].unique().to_list())
    manifest = _fetch(symbols)
    events, event_audit = _events()
    reb = _attach(reb, events)
    print("reb", reb.shape, "events", event_audit, "fetch", manifest, flush=True)
    data_quality = {"fetch": manifest, "events": event_audit, "coverage": _coverage_data(reb, events)}

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc, raw_val = r42._period_frame(reb, DISCOVERY), r42._period_frame(reb, VALIDATION)
        disc_cov, val_cov = r53._coverage(raw_disc, name), r53._coverage(raw_val, name)
        disc, val = r53._usable(raw_disc, name), r53._usable(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r42.r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None), default=None)
        d1 = bool(
            manifest["failure_rate"] <= 0.10 and event_audit["events_2021_2026"] >= 200
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
    result = {"window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED}, "data": data_quality, "benchmarks": r42.benchmark_metrics(reb), "factors": records, "passed": passed, "adopted_for_r60": passed, "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)}, "recommendation": "R60 portfolio integration" if passed else "negative knowledge; do not deploy", "caveats": ["只使用已发生解禁, 不使用未来日程", "无事件按 0, 接口失败不计 0", "历史名单为月度快照"]}
    path = EV / "r59_unlock.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
