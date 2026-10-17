# 输入说明: R62 — 历史 HS300 重要股东增减持 PIT 因子筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r62_holder_trade.py
# 结果 JSON: 同目录 r62_holder_trade.json。原始全市场公告缓存 data/research/r62_holder_trade/ (不提交)。
from __future__ import annotations

import bisect
import importlib.util
import json
import statistics as st
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
r22 = r42.r22
DATA = r42.DATA
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
CACHE = DATA / "research" / "r62_holder_trade"
WINDOW_DAYS = 90
FACTORS = {"holder_net_buy_ratio_90d": True, "holder_net_buy_count_90d": True}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _events(symbols: set[str]) -> tuple[pl.DataFrame, dict]:
    raw = pl.read_parquet(CACHE / "raw_all.parquet")
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    required = {
        "代码", "股东名称", "持股变动信息-增减", "持股变动信息-变动数量",
        "持股变动信息-占流通股比例", "变动开始日", "变动截止日", "公告日",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"股东增减持字段缺失: {missing}")
    frame = raw.select([
        pl.col("代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
        pl.col("公告日").cast(pl.Date, strict=False).alias("announce_date"),
        pl.col("变动开始日").cast(pl.Date, strict=False).alias("change_start"),
        pl.col("变动截止日").cast(pl.Date, strict=False).alias("change_end"),
        pl.col("股东名称").cast(pl.Utf8).fill_null("UNKNOWN").alias("shareholder"),
        pl.when(pl.col("持股变动信息-增减") == "增持").then(1).when(pl.col("持股变动信息-增减") == "减持").then(-1).otherwise(None).alias("direction"),
        pl.col("持股变动信息-占流通股比例").cast(pl.Float64, strict=False).abs().alias("ratio_pct"),
        pl.col("持股变动信息-变动数量").cast(pl.Float64, strict=False).alias("quantity"),
    ]).join(instruments, on="code", how="inner").filter(pl.col("symbol").is_in(symbols) & pl.col("announce_date").is_not_null() & pl.col("direction").is_not_null())
    dedup = frame.unique(subset=["symbol", "announce_date", "shareholder", "direction", "ratio_pct", "quantity"], keep="last")
    daily = (
        dedup.sort(["symbol", "announce_date", "shareholder", "direction", "ratio_pct", "quantity"])
        .group_by(["symbol", "announce_date"], maintain_order=True).agg([
            (pl.col("ratio_pct") * pl.col("direction")).sum().alias("ratio_signed"),
            pl.col("direction").sum().alias("count_signed"),
            pl.len().alias("events"),
            (pl.col("direction") == 1).sum().alias("buy_events"),
            (pl.col("direction") == -1).sum().alias("sell_events"),
        ])
        .with_columns(pl.col("ratio_signed").round(8))
        .sort(["symbol", "announce_date"])
    )
    lag = (dedup["announce_date"] - dedup["change_end"]).dt.total_days().drop_nulls().to_list()
    audit = {
        "raw_rows": raw.height,
        "historical_rows": frame.height,
        "dedup_events": dedup.height,
        "events": int(daily["events"].sum()) if daily.height else 0,
        "symbols": dedup["symbol"].n_unique(),
        "months": dedup["announce_date"].dt.strftime("%Y-%m").n_unique(),
        "announce_min": str(dedup["announce_date"].min()) if dedup.height else None,
        "announce_max": str(dedup["announce_date"].max()) if dedup.height else None,
        "announce_null_rate": round(float(frame["announce_date"].is_null().mean()), 6) if frame.height else 1.0,
        "direction_nonnull_rate": round(float(frame["direction"].is_not_null().mean()), 6) if frame.height else 0.0,
        "ratio_nonnull_rate": round(float(frame["ratio_pct"].is_not_null().mean()), 6) if frame.height else 0.0,
        "buy_events": int((dedup["direction"] == 1).sum()),
        "sell_events": int((dedup["direction"] == -1).sum()),
        "announce_lag_days": {
            "n": len(lag),
            "median": round(st.median(lag), 2) if lag else None,
            "p90": round(float(sorted(lag)[min(len(lag) - 1, int(0.90 * (len(lag) - 1)))]), 2) if lag else None,
            "max": max(lag) if lag else None,
        },
    }
    return daily, audit


def _factor_panel(reb: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    base = reb.select(["symbol", "date"]).unique().sort(["symbol", "date"])
    grouped_daily = {key[0]: group.sort("announce_date") for key, group in daily.group_by("symbol")}
    rows: list[dict] = []
    for (symbol,), month_frame in base.group_by("symbol", maintain_order=True):
        ev = grouped_daily.get(symbol)
        if ev is None:
            for day in month_frame["date"].to_list():
                rows.append({"symbol": symbol, "date": day, "holder_net_buy_ratio_90d": 0.0, "holder_net_buy_count_90d": 0.0})
            continue
        dates = ev["announce_date"].to_list()
        ratio_values = ev["ratio_signed"].fill_null(0.0).to_list()
        count_values = ev["count_signed"].to_list()
        ratio_cum = [0.0]
        count_cum = [0.0]
        for ratio, count in zip(ratio_values, count_values, strict=True):
            ratio_cum.append(ratio_cum[-1] + float(ratio))
            count_cum.append(count_cum[-1] + float(count))
        for day in month_frame["date"].to_list():
            right = bisect.bisect_right(dates, day)
            left = bisect.bisect_right(dates, day - timedelta(days=WINDOW_DAYS))
            rows.append({
                "symbol": symbol,
                "date": day,
                "holder_net_buy_ratio_90d": ratio_cum[right] - ratio_cum[left],
                "holder_net_buy_count_90d": count_cum[right] - count_cum[left],
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
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
    }


def _distribution(frame: pl.DataFrame, col: str) -> dict:
    values = frame[col].drop_nulls()
    if not values.len():
        return {"n": 0}
    return {
        "n": values.len(),
        "zero_rate": round(float((values == 0).mean()), 6),
        "positive_rate": round(float((values > 0).mean()), 6),
        "negative_rate": round(float((values < 0).mean()), 6),
        "min": round(float(values.min()), 6),
        "median": round(float(values.median()), 6),
        "max": round(float(values.max()), 6),
    }


def main() -> int:
    _panel, reb, memberships = r42.build_panel()
    reb = r42._scores(reb)
    symbols = set(memberships["symbol"].unique().to_list())
    daily, audit = _events(symbols)
    joined = _factor_panel(reb, daily)
    print("reb", reb.shape, "events", audit, flush=True)

    records, discovery_p = {}, []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        raw_disc, raw_val = r42._period_frame(joined, DISCOVERY), r42._period_frame(joined, VALIDATION)
        disc_cov, val_cov = _coverage(raw_disc, name), _coverage(raw_val, name)
        sign = 1.0 if good_high else -1.0
        disc = raw_disc.with_columns((pl.col(name) * sign).alias("_signed"))
        val = raw_val.with_columns((pl.col(name) * sign).alias("_signed"))
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, "_signed"), r42._group_stats(val, "_signed")
        corr = max((v for v in (r22.mean_abs_corr(full, "_signed", c) for c in CONTROLS) if v is not None), default=None)
        d1 = bool(
            audit["events"] >= 1000
            and audit["symbols"] >= 150
            and audit["months"] >= 48
            and audit["direction_nonnull_rate"] >= 0.90
            and audit["ratio_nonnull_rate"] >= 0.90
            and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.70
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
            "coverage_validation": val_cov, "distribution": _distribution(joined, name),
            "discovery": disc_stats, "validation": val_stats, "full": full_stats,
            "discovery_groups": disc_group, "validation_groups": val_group,
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
        "rules": {"window_days": WINDOW_DAYS, "availability": "announce_date", "no_event_value": 0.0, "winsorize": [0.01, 0.99]},
        "data": audit,
        "factors": records,
        "passed": passed,
        "adopted_for_r63": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R63 combo/event integration" if passed else "negative knowledge; holder trade dimension closed",
        "caveats": ["只使用公告日, 不使用变动区间截止日提前泄露", "无事件置 0; 大股东增持/减持不能等同未来计划", "vendor 占流通股比例存在 0.10% 缺失, 缺失按 0 汇总", "历史名单为月度快照"],
    }
    path = EV / "r62_holder_trade.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
