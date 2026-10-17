# 输入说明: R87 — 前十大流通股东集中度/季度变化 PIT 因子筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r87_holder_concentration_screen.py
# 结果 JSON: 同目录 r87_holder_concentration_screen.json。原始逐股缓存 data/research/r87_sina_holder_full/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import StringIO
from itertools import pairwise
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
r40 = r42.r40
r22 = r42.r22
r30 = r42.r30
r33 = r42.r33
r44 = _load("r44", "script_r44_holder.py")
r45 = _load("r45", "script_r45_holder_1q.py")
r87_probe = _load("r87_probe", "script_r87_holder_concentration_sina_probe.py")
DATA = r42.DATA
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
CACHE = DATA / "research" / "r87_sina_holder_full"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
PERMUTATIONS = r42.PERMUTATIONS
MAX_AGE_DAYS = 200
MIN_VALID_MONTH = 80
MIN_USABLE_MONTHS_PER_SEGMENT = 24
MIN_TOTAL_USABLE_MONTHS = 48
PERIOD_START = date(2021, 3, 31)
PERIOD_END = date(2026, 6, 30)
WORKERS = 4
FACTORS: dict[str, bool] = {
    "holder_top10_free_float_share": True,
    "holder_top10_share_chg_qoq": True,
    "holder_top10_share_chg_yoy": True,
}
CONTROLS = ["pb_latest", "momentum_20d", "annual_vol_20d", "float_mv"]


def _download_raw(symbol: str):
    import pandas as pd
    import requests

    url = (
        "https://vip.stock.finance.sina.com.cn/corp/go.php/"
        f"vCI_CirculateStockHolder/stockid/{symbol[:6]}.phtml"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    if len(tables) <= 13:
        raise RuntimeError(f"holder table missing: tables={len(tables)}")
    temp_df = tables[13].iloc[:, :5].copy()
    temp_df.columns = [*range(5)]
    need_range = temp_df[temp_df.iloc[:, 0].astype(str).str.find("截止日期") == 0].index.tolist() + [len(temp_df)]
    big_df = pd.DataFrame()
    for index in range(len(need_range) - 1):
        truncated_df = temp_df.iloc[need_range[index] : need_range[index + 1], :].dropna(how="all")
        values = truncated_df.iloc[2:, :].reset_index(drop=True)
        concat_df = pd.concat([values, truncated_df.iloc[0, :], truncated_df.iloc[1, :]], axis=1)
        concat_df.columns = concat_df.iloc[0, :]
        concat_df = concat_df.iloc[1:, :]
        concat_df["截止日期"] = concat_df["截止日期"].ffill()
        concat_df["公告日期"] = concat_df["公告日期"].ffill()
        big_df = pd.concat([big_df, concat_df], axis=0, ignore_index=True)
    if big_df.empty:
        return big_df
    big_df = big_df[
        [
            "截止日期",
            "公告日期",
            "编号",
            "股东名称",
            "持股数量(股)",
            "占流通股比例(%)",
            "股本性质",
        ]
    ]
    big_df.columns = [
        "截止日期",
        "公告日期",
        "编号",
        "股东名称",
        "持股数量",
        "占流通股比例",
        "股本性质",
    ]
    big_df["截止日期"] = pd.to_datetime(big_df["截止日期"], errors="coerce").dt.date
    big_df["公告日期"] = pd.to_datetime(big_df["公告日期"], errors="coerce").dt.date
    big_df["编号"] = pd.to_numeric(big_df["编号"], errors="coerce")
    big_df["持股数量"] = pd.to_numeric(big_df["持股数量"], errors="coerce")
    big_df["占流通股比例"] = pd.to_numeric(big_df["占流通股比例"], errors="coerce")
    return big_df.reset_index(drop=True)


def _ensure_symbol(symbol: str) -> dict:
    path = CACHE / f"{symbol}.parquet"
    if path.exists() and path.stat().st_size > 0:
        return {"symbol": symbol, "ok": True, "rows": pl.read_parquet(path).height, "error": "cache"}
    last_error = "unknown"
    for attempt in range(3):
        try:
            frame = r87_probe._normalize(_download_raw(symbol), symbol)
            if frame.height == 0:
                return {"symbol": symbol, "ok": False, "rows": 0, "error": "empty"}
            tmp = path.with_suffix(".tmp")
            frame.write_parquet(tmp)
            tmp.replace(path)
            return {"symbol": symbol, "ok": True, "rows": frame.height, "error": ""}
        except Exception as exc:  # noqa: BLE001 - source fetch records failures and continues
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return {"symbol": symbol, "ok": False, "rows": 0, "error": last_error}


def _ensure_cache(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    missing = [symbol for symbol in symbols if not (CACHE / f"{symbol}.parquet").exists()]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_ensure_symbol, symbol): symbol for symbol in missing}
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            results[row["symbol"]] = row
            if index % 25 == 0 or index == len(futures):
                print(f"holder screen fetch {index}/{len(futures)}", flush=True)
    for symbol in symbols:
        if symbol not in results:
            path = CACHE / f"{symbol}.parquet"
            results[symbol] = {
                "symbol": symbol,
                "ok": path.exists() and path.stat().st_size > 0,
                "rows": pl.read_parquet(path).height if path.exists() and path.stat().st_size > 0 else 0,
                "error": "cache" if path.exists() else "missing_cache",
            }
    frames = [
        pl.read_parquet(CACHE / f"{symbol}.parquet")
        for symbol in symbols
        if (CACHE / f"{symbol}.parquet").exists() and (CACHE / f"{symbol}.parquet").stat().st_size > 0
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    failures = [row for row in results.values() if not row["ok"]]
    manifest = {
        "symbols": len(symbols),
        "success_symbols": len(symbols) - len(failures),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6),
        "failures": sorted(failures, key=lambda row: row["symbol"]),
        "rows": raw.height,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw, manifest


def _last_month_pairs(dates: list[date]) -> list[tuple[date, date]]:
    by_month: dict[str, date] = {}
    for value in sorted(dates):
        by_month[value.strftime("%Y-%m")] = value
    month_ends = sorted(by_month.values())
    return list(pairwise(month_ends))


def _build_last_month_panel(panel: pl.DataFrame) -> pl.DataFrame:
    pairs = _last_month_pairs(panel["date"].unique().to_list())
    reb = panel.filter(pl.col("date").is_in([start for start, _ in pairs]))
    next_values = panel.select([
        pl.col("symbol"),
        pl.col("date").alias("date_next"),
        pl.col("close").alias("close_next"),
    ])
    pair_frame = pl.DataFrame({
        "date": [start for start, _ in pairs],
        "date_next": [end for _, end in pairs],
    })
    return (
        reb.join(pair_frame, on="date", how="inner")
        .join(next_values, on=["symbol", "date_next"], how="left")
        .with_columns((pl.col("close_next") / pl.col("close") - 1.0).alias("fwd"))
    )


def _exact_membership_reb(reb: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    snapshots = pl.read_parquet(DAILY_MEMBERSHIP).select([
        pl.col("query_date").alias("date"),
        pl.col("symbol"),
    ])
    reb_dates = sorted(reb["date"].unique().to_list())
    snapshot_dates = set(snapshots["date"].unique().to_list())
    missing_dates = [value for value in reb_dates if value not in snapshot_dates]
    exact = reb.join(snapshots, on=["date", "symbol"], how="inner")
    per_date = snapshots.group_by("date").agg(pl.col("symbol").n_unique().alias("members"))
    joined_dates = exact.group_by("date").agg([
        pl.len().alias("panel_rows"),
        pl.col("symbol").n_unique().alias("panel_symbols"),
    ])
    audit = {
        "reb_dates": len(reb_dates),
        "missing_snapshot_dates": [str(value) for value in missing_dates],
        "membership_days": per_date.height,
        "min_snapshot_members": int(per_date["members"].min()),
        "max_snapshot_members": int(per_date["members"].max()),
        "reb_rows": exact.height,
        "reb_symbols": exact["symbol"].n_unique(),
        "min_panel_symbols": int(joined_dates["panel_symbols"].min()) if joined_dates.height else 0,
        "median_panel_symbols": float(joined_dates["panel_symbols"].median()) if joined_dates.height else 0.0,
    }
    return exact.sort(["date", "symbol"]), audit


def _shift_period(period: date, years: int = 0, quarters: int = 0) -> date:
    import calendar

    month = period.month + quarters * 3
    year = period.year + years
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _signal_frame(aggregates: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    usable_all = aggregates.filter(
        (pl.col("period_end") >= r87_probe.FETCH_PERIOD_START)
        & (pl.col("period_end") <= PERIOD_END)
        & pl.col("period_end").dt.month().is_in([3, 6, 9, 12])
        & (pl.col("period_end") == pl.col("period_end").dt.month_end())
    ).filter(
        (pl.col("rank_count") == 10)
        & (pl.col("ratio_nonnull") == 10)
        & pl.col("announce_date").is_not_null()
        & (pl.col("ratio_sum") >= 0)
        & (pl.col("ratio_sum") <= 100.01)
    )
    current_pool = usable_all.filter(pl.col("period_end") >= PERIOD_START)
    current = current_pool.select([
        "symbol",
        "period_end",
        pl.col("announce_date").alias("current_announce_date"),
        pl.col("ratio_sum").alias("holder_top10_free_float_share"),
    ])
    prior_q = usable_all.select([
        "symbol",
        pl.col("period_end").alias("prev_q_period"),
        pl.col("announce_date").alias("prev_q_announce_date"),
        pl.col("ratio_sum").alias("prev_q_ratio_sum"),
    ])
    prior_y = usable_all.select([
        "symbol",
        pl.col("period_end").alias("prev_y_period"),
        pl.col("announce_date").alias("prev_y_announce_date"),
        pl.col("ratio_sum").alias("prev_y_ratio_sum"),
    ])
    current = current.with_columns([
        pl.col("period_end").dt.offset_by("-3mo").dt.month_end().alias("_prev_q_key"),
        pl.col("period_end").dt.offset_by("-1y").dt.month_end().alias("_prev_y_key"),
    ])
    joined = (
        current.join(prior_q, left_on=["symbol", "_prev_q_key"], right_on=["symbol", "prev_q_period"], how="left")
        .join(prior_y, left_on=["symbol", "_prev_y_key"], right_on=["symbol", "prev_y_period"], how="left")
        .with_columns([
            pl.when(
                pl.col("prev_q_ratio_sum").is_not_null()
                & (pl.col("prev_q_announce_date") <= pl.col("current_announce_date"))
            )
            .then(pl.col("holder_top10_free_float_share") - pl.col("prev_q_ratio_sum"))
            .otherwise(None)
            .alias("holder_top10_share_chg_qoq"),
            pl.when(
                pl.col("prev_y_ratio_sum").is_not_null()
                & (pl.col("prev_y_announce_date") <= pl.col("current_announce_date"))
            )
            .then(pl.col("holder_top10_free_float_share") - pl.col("prev_y_ratio_sum"))
            .otherwise(None)
            .alias("holder_top10_share_chg_yoy"),
            pl.col("current_announce_date").dt.offset_by("1d").alias("available_date"),
        ])
        .select([
            "symbol",
            "period_end",
            "available_date",
            *FACTORS.keys(),
        ])
        .sort(["symbol", "available_date", "period_end"])
        .unique(subset=["symbol", "available_date"], keep="last")
        .sort(["symbol", "available_date"])
    )
    complete_aggs = aggregates.filter(
        (pl.col("period_end") >= PERIOD_START)
        & (pl.col("period_end") <= PERIOD_END)
        & pl.col("period_end").dt.month().is_in([3, 6, 9, 12])
        & (pl.col("period_end") == pl.col("period_end").dt.month_end())
        & (pl.col("rank_count") == 10)
        & (pl.col("ratio_nonnull") == 10)
    )
    audit = {
        "aggregate_rows": aggregates.height,
        "usable_report_rows": current_pool.height,
        "signal_rows": joined.height,
        "signal_symbols": joined["symbol"].n_unique(),
        "announcement_date_coverage": round(
            complete_aggs["announce_date"].is_not_null().sum() / complete_aggs.height, 6
        ) if complete_aggs.height else None,
        "period_min": str(joined["period_end"].min()) if joined.height else None,
        "period_max": str(joined["period_end"].max()) if joined.height else None,
    }
    return joined, audit


def _attach_signals(reb: pl.DataFrame, signals: pl.DataFrame) -> pl.DataFrame:
    joined = reb.sort(["symbol", "date"]).join_asof(
        signals.sort(["symbol", "available_date"]),
        left_on="date",
        right_on="available_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).with_columns(
        (pl.col("date") - pl.col("available_date")).dt.total_days().alias("holder_report_age_days")
    )
    joined = joined.with_columns([
        pl.when(pl.col("holder_report_age_days").is_between(0, MAX_AGE_DAYS, closed="both"))
        .then(pl.col(name)).otherwise(None).alias(name)
        for name in FACTORS
    ])
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"),
            pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(
            pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)
        ).drop(["_lo", "_hi"])
    return joined


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(col).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge80": 0}
    coverage = grouped["valid"] / grouped["eligible"]
    return {
        "mean": round(float(coverage.mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge80": int((grouped["valid"] >= MIN_VALID_MONTH).sum()),
    }


def _usable_months(frame: pl.DataFrame, col: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    grouped = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).sort("date")
    usable = grouped.filter(pl.col("valid") >= MIN_VALID_MONTH)
    return frame.filter(pl.col("date").is_in(usable["date"].to_list())), usable


def _input_completeness(frame: pl.DataFrame) -> dict:
    return {
        name: round(float(frame[name].is_not_null().mean()), 6)
        for name in FACTORS
    }


def _age_report(frame: pl.DataFrame, col: str) -> dict:
    values = frame.filter(pl.col(col).is_not_null())["holder_report_age_days"].drop_nulls().to_list()
    if not values:
        return {"n": 0}
    values = sorted(values)
    def q(p: float) -> float:
        return float(values[min(len(values) - 1, int(p * (len(values) - 1)))])
    return {
        "n": len(values),
        "median": q(0.50),
        "p90": q(0.90),
        "p99": q(0.99),
        "max": values[-1],
    }


def _digest(signals: pl.DataFrame) -> str:
    digest = hashlib.sha256()
    for row in signals.sort(["symbol", "available_date"]).iter_rows():
        digest.update(repr(row).encode())
    return digest.hexdigest()


def _benchmark_metrics(reb: pl.DataFrame) -> dict:
    phases = r33.phase_dates(r40.PHASE_TARGETS)
    equal_weight = (
        reb.filter(pl.col("fwd").is_not_null())
        .group_by("date").agg(pl.col("fwd").mean().alias("r"))
        .sort("date")
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .select(["ym", "r"])
    )
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in index_paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"]).collect().sort("date")
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
    )
    index_month_end = (
        index.group_by("ym", maintain_order=True).agg(pl.col("close").last()).sort("ym")
        .with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("r"))
        .select(["ym", "r"]).drop_nulls()
    )
    equal_weight_phases = [
        equal_weight.filter(pl.col("ym") >= phase.strftime("%Y-%m")) for phase in phases
    ]
    index_phases = [
        index_month_end.filter(pl.col("ym") >= phase.strftime("%Y-%m")) for phase in phases
    ]
    return {
        "phases": [str(phase) for phase in phases],
        "equal_weight": {
            "metrics": r30.phase_metrics(equal_weight_phases),
            "rolling": r30.rolling_windows(equal_weight_phases),
        },
        "hs300_index": {
            "metrics": r30.phase_metrics(index_phases),
            "rolling": r30.rolling_windows(index_phases),
        },
    }


def main() -> int:
    panel, _monthly_reb, legacy_memberships = r42.build_panel()
    raw_all, fetch_manifest = _ensure_cache(sorted(legacy_memberships["symbol"].unique().to_list()))
    aggregates = r87_probe._aggregate(raw_all)
    signals, signal_audit = _signal_frame(aggregates)
    reb_all = _build_last_month_panel(panel)
    reb_analysis = reb_all.filter(pl.col("date") >= DISCOVERY[0])
    reb, membership_audit = _exact_membership_reb(reb_analysis)
    joined = _attach_signals(reb, signals)
    joined = r45._attach(
        joined,
        r45._signals(r45._raw_records()),
    ).rename({"holder_age_days": "holder_count_age_days"}).select([
        *joined.columns,
        pl.col("holder_chg_1q"),
    ])
    discovery = r42._period_frame(joined, DISCOVERY)
    validation = r42._period_frame(joined, VALIDATION)
    inputs = {
        "discovery": _input_completeness(discovery),
        "validation": _input_completeness(validation),
    }
    source_ok = fetch_manifest["failure_rate"] <= 0.10
    input_ok = bool(
        signal_audit["announcement_date_coverage"] is not None
        and signal_audit["announcement_date_coverage"] >= 0.95
        and min(inputs["discovery"].values()) >= 0.90
        and min(inputs["validation"].values()) >= 0.90
    )
    print("panel", panel.shape, "reb", reb.shape, "raw", raw_all.shape, "signals", signals.shape, flush=True)
    print("fetch", fetch_manifest["failure_rate"], "inputs", inputs, "membership", membership_audit, flush=True)

    records = {}
    discovery_pvalues = []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        sign = 1.0 if good_high else -1.0
        raw_disc = discovery.with_columns((pl.col(name) * sign).alias("_signed"))
        raw_val = validation.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_coverage = _coverage(raw_disc, name)
        val_coverage = _coverage(raw_val, name)
        disc, disc_months = _usable_months(raw_disc, name)
        val, val_months = _usable_months(raw_val, name)
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
        corr_values = {
            control: r22.mean_abs_corr(full, "_signed", control)
            for control in [*CONTROLS, "holder_chg_1q"]
            if control in full.columns
        }
        corr_max = max((value for value in corr_values.values() if value is not None), default=None)
        d1 = bool(
            source_ok
            and input_ok
            and disc_coverage["mean"] is not None and disc_coverage["mean"] >= 0.70
            and val_coverage["mean"] is not None and val_coverage["mean"] >= 0.70
            and disc_coverage["min_valid"] >= MIN_VALID_MONTH
            and val_coverage["min_valid"] >= MIN_VALID_MONTH
            and disc_months.height >= MIN_USABLE_MONTHS_PER_SEGMENT
            and val_months.height >= MIN_USABLE_MONTHS_PER_SEGMENT
            and disc_months.height + val_months.height >= MIN_TOTAL_USABLE_MONTHS
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7
            and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0
            and val_group["spread_annual_net2x"] > 0
        )
        f4 = bool(corr_max is not None and corr_max < 0.70)
        records[name] = {
            "direction": "high" if good_high else "low",
            "coverage_discovery": disc_coverage,
            "coverage_validation": val_coverage,
            "usable_months": {"discovery": disc_months.height, "validation": val_months.height},
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing": corr_values,
            "corr_existing_max": corr_max,
            "age_report": _age_report(joined, name),
            "industry": r44._industry_report(full, name),
            "D1": d1,
            "F1": f1,
            "F2": f2,
            "F4": f4,
        }
        discovery_pvalues.append(disc_stats["perm_p"])
        print(
            f"{name}: D1={d1} cov={disc_coverage['mean']}/{val_coverage['mean']} "
            f"IC={disc_stats['mean']}/{val_stats['mean']} "
            f"rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} "
            f"corr={corr_max} p={disc_stats['perm_p']}", flush=True,
        )

    qvalues = r42._bh_qvalues(discovery_pvalues)
    passed = []
    for (name, _), qvalue in zip(FACTORS.items(), qvalues, strict=True):
        record = records[name]
        record["discovery_q"] = qvalue
        record["F3"] = bool(
            qvalue is not None and qvalue <= 0.10
            and record["validation"]["perm_p"] is not None
            and record["validation"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(
            record["D1"] and record["F1"] and record["F2"] and record["F3"] and record["F4"]
        )
        if record["all_pass"]:
            passed.append(name)

    factor_corr = {}
    for left in FACTORS:
        for right in FACTORS:
            if left >= right:
                continue
            value = r22.mean_abs_corr(joined, left, right)
            factor_corr[f"{left}__{right}"] = value
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "period_start": str(PERIOD_START),
            "period_end": str(PERIOD_END),
            "factors": list(FACTORS),
            "direction": {name: "high" for name in FACTORS},
            "max_age_days": MAX_AGE_DAYS,
            "min_valid_month": MIN_VALID_MONTH,
            "min_usable_months_per_segment": MIN_USABLE_MONTHS_PER_SEGMENT,
            "min_total_usable_months": MIN_TOTAL_USABLE_MONTHS,
            "permutations": PERMUTATIONS,
            "seed": RNG_SEED,
            "rebalance": "last trading snapshot day of each month; close-to-next-month-end close",
            "cost_round_trip_2x": 0.0028,
        },
        "data": {
            "fetch": {
                key: fetch_manifest[key]
                for key in ("symbols", "success_symbols", "failure_count", "failure_rate", "failures", "rows")
            },
            "signals": signal_audit,
            "signal_digest": _digest(signals),
            "input_completeness": inputs,
            "membership": membership_audit,
            "raw_rows": raw_all.height,
            "aggregate_rows": aggregates.height,
        },
        "coverage": {
            name: {
                "discovery": records[name]["coverage_discovery"],
                "validation": records[name]["coverage_validation"],
            }
            for name in FACTORS
        },
        "factors": records,
        "factor_correlation": factor_corr,
        "benchmarks": _benchmark_metrics(reb),
        "passed": passed,
        "adopted_for_r88": passed,
        "decision": {
            "source_ok": source_ok,
            "input_ok": input_ok,
            "D1_count": sum(bool(records[name]["D1"]) for name in FACTORS),
            "F1_count": sum(bool(records[name]["F1"]) for name in FACTORS),
            "F2_count": sum(bool(records[name]["F2"]) for name in FACTORS),
            "F3_count": sum(bool(records[name]["F3"]) for name in FACTORS),
            "F4_count": sum(bool(records[name]["F4"]) for name in FACTORS),
            "all_pass_count": len(passed),
        },
        "production_state": "pause_production",
        "recommendation": "R88 long-only and neutralization validation" if passed else "negative knowledge; holder concentration dimension closed",
        "caveats": [
            "新浪页面为供应商回填数据，公告日期可能不是交易所原始首次披露时点",
            "前十大流通股东只覆盖头部筹码，不等于完整股东结构",
            "前十大合计比例可能受自由流通股本口径调整影响",
            "逐日成员来自 BaoStock 查询，不是中证指数原始调样公告",
            "价格面板缺失的成员无法进入截面；未用当前快照回填",
        ],
    }
    compact = r22.compact(result)
    canonical = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compact["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r87_holder_concentration_screen.json"
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", compact["decision"], flush=True)
    print("passed", passed, "sha", compact["sha256_canonical"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
