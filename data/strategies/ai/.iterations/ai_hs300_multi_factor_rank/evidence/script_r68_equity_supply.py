# 输入说明: R68 — 历史 HS300 股权融资供给 (增发/配股/可转债转股) PIT 因子。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r68_equity_supply.py
# 结果 JSON: 同目录 r68_equity_supply.json。逐股原始缓存 data/research/r68_equity_supply/ (不提交)。
from __future__ import annotations

import bisect
import importlib.util
import json
import math
import time
from datetime import timedelta
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
r22 = r42.r22
DATA = r42.DATA
CACHE = DATA / "research" / "r68_equity_supply"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
WINDOW_DAYS = 180
FACTORS = {"equity_supply_ratio_180d": False, "equity_supply_count_180d": False}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]
ISSUANCE_KEYWORDS = ("增发新股上市", "配股上市", "可转债转股")


def _fetch(symbols: list[str]) -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    empty: list[str] = []
    fetched = cached = 0
    for index, symbol in enumerate(symbols, start=1):
        path = CACHE / f"{symbol}.parquet"
        if path.exists() and path.stat().st_size > 0:
            cached += 1
            continue
        last_error = None
        for attempt in range(3):
            try:
                raw = ak.stock_share_change_cninfo(
                    symbol=symbol[:6], start_date="20200101", end_date="20261017"
                )
                frame = pl.from_pandas(raw)
                if frame.height == 0:
                    empty.append(symbol)
                tmp = path.with_suffix(".tmp")
                frame.write_parquet(tmp)
                tmp.replace(path)
                fetched += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001 - provider failures are recorded
                last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
        if last_error:
            failures[symbol] = last_error
        if index % 25 == 0 or index == len(symbols):
            print(f"equity supply fetch {index}/{len(symbols)} cached={cached} fetched={fetched} failed={len(failures)}", flush=True)
    manifest = {
        "symbols": len(symbols), "cached_this_run": cached, "fetched_this_run": fetched,
        "empty_symbols": empty, "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6), "failures": failures,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _events(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    for symbol in symbols:
        path = CACHE / f"{symbol}.parquet"
        if not path.exists() or path.stat().st_size == 0:
            continue
        frame = pl.read_parquet(path)
        if frame.height:
            frames.append(frame.with_columns(pl.lit(symbol).alias("symbol")))
    if not frames:
        raise RuntimeError("股本变动缓存为空")
    raw = pl.concat(frames, how="diagonal_relaxed")
    required = {"symbol", "公告日期", "变动日期", "变动原因", "总股本"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"股本变动字段缺失: {missing}")
    frame = raw.select([
        "symbol", pl.col("公告日期").cast(pl.Date, strict=False).alias("announce_date"),
        pl.col("变动日期").cast(pl.Date, strict=False).alias("change_date"),
        pl.col("变动原因").cast(pl.Utf8).alias("reason"),
        pl.col("总股本").cast(pl.Float64, strict=False).alias("total_shares"),
    ]).filter(
        pl.col("announce_date").is_not_null() & pl.col("reason").is_not_null() & pl.col("total_shares").is_not_null() & (pl.col("total_shares") > 0)
    ).unique(subset=["symbol", "announce_date", "change_date", "reason", "total_shares"], keep="last").sort(["symbol", "announce_date", "change_date", "reason"])
    frame = frame.with_columns(pl.col("total_shares").shift(1).over("symbol").alias("_prev_total"))
    frame = frame.with_columns((pl.col("total_shares") / pl.col("_prev_total") - 1.0).alias("_dilution"))
    issuance = frame.filter(
        pl.col("_dilution").is_not_null() & (pl.col("_dilution") > 0)
        & pl.col("reason").str.contains("|".join(ISSUANCE_KEYWORDS))
    )
    daily = issuance.group_by(["symbol", "announce_date"]).agg([
        ((pl.col("_dilution") + 1.0).product() - 1.0).alias("dilution"),
        pl.len().alias("events"),
        pl.col("reason").n_unique().alias("reason_count"),
    ]).sort(["symbol", "announce_date"])
    lag = (issuance["announce_date"] - issuance["change_date"]).dt.total_days().drop_nulls().to_list()
    audit = {
        "raw_rows": raw.height, "normalized_rows": frame.height,
        "historical_rows": frame.height, "issuance_rows": issuance.height,
        "events": int(daily["events"].sum()) if daily.height else 0,
        "symbols_with_events": daily["symbol"].n_unique(), "event_months": daily["announce_date"].dt.strftime("%Y-%m").n_unique(),
        "announce_null_rate": round(float(frame["announce_date"].is_null().mean()), 6) if frame.height else 1.0,
        "reason_null_rate": round(float(frame["reason"].is_null().mean()), 6) if frame.height else 1.0,
        "total_shares_null_rate": round(float(frame["total_shares"].is_null().mean()), 6) if frame.height else 1.0,
        "dilution_distribution": {
            "min": round(float(daily["dilution"].min()), 8) if daily.height else None,
            "median": round(float(daily["dilution"].median()), 8) if daily.height else None,
            "p90": round(float(daily["dilution"].quantile(0.90)), 8) if daily.height else None,
            "max": round(float(daily["dilution"].max()), 8) if daily.height else None,
        },
        "announce_lag_days": {
            "n": len(lag), "median": round(float(sorted(lag)[len(lag)//2]), 2) if lag else None,
            "p90": round(float(sorted(lag)[min(len(lag) - 1, int(0.90 * (len(lag) - 1)))]), 2) if lag else None,
            "max": max(lag) if lag else None,
        },
        "reasons": issuance["reason"].value_counts().sort(["count", "reason"], descending=[True, False]).head(20).to_dicts() if issuance.height else [],
    }
    return daily, audit


def _factor_panel(reb: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    base = reb.select(["symbol", "date"]).unique().sort(["symbol", "date"])
    grouped = {key[0]: group.sort("announce_date") for key, group in daily.group_by("symbol")}
    rows: list[dict] = []
    for (symbol,), month_frame in base.group_by("symbol", maintain_order=True):
        event = grouped.get(symbol)
        if event is None:
            for day in month_frame["date"].to_list():
                rows.append({"symbol": symbol, "date": day, "equity_supply_ratio_180d": 0.0, "equity_supply_count_180d": 0.0})
            continue
        event_dates = event["announce_date"].to_list()
        ratios = event["dilution"].to_list()
        counts = event["events"].to_list()
        log_cum = [0.0]
        count_cum = [0.0]
        for ratio, count in zip(ratios, counts, strict=True):
            log_cum.append(log_cum[-1] + math.log1p(max(float(ratio), -0.999999)))
            count_cum.append(count_cum[-1] + float(count))
        for day in month_frame["date"].to_list():
            right = bisect.bisect_left(event_dates, day)
            left = bisect.bisect_right(event_dates, day - timedelta(days=WINDOW_DAYS))
            rows.append({
                "symbol": symbol, "date": day,
                "equity_supply_ratio_180d": math.expm1(log_cum[right] - log_cum[left]),
                "equity_supply_count_180d": count_cum[right] - count_cum[left],
            })
    factors = pl.DataFrame(rows).sort(["symbol", "date"])
    joined = reb.join(factors, on=["symbol", "date"], how="left")
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"),
            pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)).drop(["_lo", "_hi"])
    return joined


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0}
    return {"mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6), "min_valid": int(grouped["valid"].min()), "months": grouped.height}


def main() -> int:
    _panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = sorted(memberships["symbol"].unique().to_list())
    manifest = _fetch(symbols)
    daily, audit = _events(symbols)
    joined = _factor_panel(reb, daily)
    print("reb", reb.shape, "daily", daily.shape, "manifest", manifest, "audit", audit, flush=True)

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc, raw_val = r42._period_frame(joined, DISCOVERY), r42._period_frame(joined, VALIDATION)
        disc_cov, val_cov = _coverage(raw_disc, name), _coverage(raw_val, name)
        disc, val = raw_disc, raw_val
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
            manifest["failure_rate"] <= 0.10 and audit["events"] >= 200 and audit["symbols_with_events"] >= 80
            and audit["event_months"] >= 24 and audit["announce_null_rate"] <= 0.10
            and audit["reason_null_rate"] <= 0.10 and audit["total_shares_null_rate"] <= 0.10
            and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.70
            and disc_cov["min_valid"] >= 80 and val_cov["min_valid"] >= 80
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
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
    persistent = {k: v for k, v in manifest.items() if k not in {"cached_this_run", "fetched_this_run"}}
    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "rules": {"window_days": WINDOW_DAYS, "issuance_keywords": list(ISSUANCE_KEYWORDS), "announcement_strict_before": True, "winsorize": [0.01, 0.99]},
        "data": {"fetch": persistent, "events": audit},
        "factors": records, "passed": passed, "adopted_for_r69": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R69 portfolio integration" if passed else "negative knowledge; equity supply dimension closed",
        "caveats": ["只用公告日, 不使用变动日提前泄露", "增发/配股/可转债转股才计供给", "总股本变化包含同日其它股本事件, 可能高估", "历史名单为月度快照"],
    }
    path = EV / "r68_equity_supply.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
