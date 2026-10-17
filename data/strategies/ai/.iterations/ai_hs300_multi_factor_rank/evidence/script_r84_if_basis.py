# 输入说明: R84 — IF 近月年化基差对 HS300 下一月收益的预测检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r84_if_basis.py
# 原始期货缓存 data/research/r84_if_basis/ (不入库); 结果 JSON 同目录 r84_if_basis.json。
from __future__ import annotations

import calendar
import importlib.util
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
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
DATA = r40.DATA
FUT_DIR = DATA / "research" / "r84_if_basis"
START = date(2021, 1, 1)
END = date(2026, 10, 14)
DISCOVERY = (date(2021, 4, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), END)
SIGNALS = ("basis_ann_level", "basis_ann_z60d", "basis_ann_chg20d")
COST_2X = 0.0028
RNG_SEED = r42.RNG_SEED


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    out = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        last_day = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        out.append((max(cursor, start), min(last_day, end)))
        cursor = (last_day + timedelta(days=1))
    return out


def _ensure_futures() -> tuple[pl.DataFrame, dict]:
    import akshare as ak

    FUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks = _month_chunks(START, END)
    failures = []
    frames = []
    for index, (start, end) in enumerate(chunks, start=1):
        path = FUT_DIR / f"{start:%Y-%m}.parquet"
        if not path.exists():
            try:
                frame = ak.get_futures_daily(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), market="CFFEX")
                frame.to_parquet(path, index=False)
            except Exception as exc:  # noqa: BLE001
                failures.append({"month": f"{start:%Y-%m}", "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                continue
        if path.exists():
            frames.append(pl.read_parquet(path))
        if index % 10 == 0 or index == len(chunks):
            print(f"futures months {index}/{len(chunks)}", flush=True)
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if raw.height:
        raw = raw.filter(pl.col("variety") == "IF").with_columns([
            pl.col("date").cast(pl.Utf8).str.to_date("%Y%m%d", strict=False),
            *[pl.col(c).cast(pl.Float64, strict=False) for c in ("open", "high", "low", "close", "volume", "open_interest", "turnover", "settle", "pre_settle")],
        ]).unique(subset=["symbol", "date"], keep="last").sort(["date", "symbol"])
    audit = {
        "months": len(chunks),
        "files": len(frames),
        "failures": failures,
        "rows": raw.height,
        "contracts": raw["symbol"].n_unique() if raw.height else 0,
        "date_min": str(raw["date"].min()) if raw.height else None,
        "date_max": str(raw["date"].max()) if raw.height else None,
    }
    return raw, audit


def _contract_expiry(symbol: str) -> date | None:
    if len(symbol) < 6 or not symbol.startswith("IF"):
        return None
    try:
        year = 2000 + int(symbol[2:4])
        month = int(symbol[4:6])
    except ValueError:
        return None
    first = date(year, month, 1)
    days_to_friday = (4 - first.weekday()) % 7
    third_friday = first + timedelta(days=days_to_friday + 14)
    return third_friday


def _spot() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= START)
            & (pl.col("date") <= END)
        )
        .select(["date", "close"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
        .sort("date")
    )


def _daily_basis(futures: pl.DataFrame, spot: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    expiry_map = {symbol: _contract_expiry(symbol) for symbol in futures["symbol"].unique().to_list()}
    spot_map = dict(zip(spot["date"].to_list(), spot["close"].to_list(), strict=False))
    rows = []
    by_date = futures.group_by("date", maintain_order=True)
    for keys, group in by_date:
        day = keys[0] if isinstance(keys, tuple) else keys
        spot_close = spot_map.get(day)
        if spot_close is None or spot_close <= 0:
            continue
        candidates = []
        for row in group.iter_rows(named=True):
            expiry = expiry_map.get(row["symbol"])
            if expiry is None:
                continue
            days = (expiry - day).days
            if 1 <= days <= 45 and row["close"] is not None and row["close"] > 0:
                candidates.append((expiry, days, row))
        if not candidates:
            continue
        expiry, days, row = min(candidates, key=lambda item: (item[0], -float(item[2].get("open_interest") or 0.0)))
        ratio = float(row["close"]) / spot_close
        annualized = ratio ** (365.0 / days) - 1.0 if ratio > 0 else None
        rows.append({
            "date": day,
            "near_contract": row["symbol"],
            "expiry": expiry,
            "days_to_expiry": days,
            "futures_close": float(row["close"]),
            "spot_close": spot_close,
            "basis_raw": ratio - 1.0,
            "basis_ann": annualized,
            "near_open_interest": float(row["open_interest"] or 0.0),
        })
    daily = pl.DataFrame(rows).sort("date").with_columns([
        (pl.col("basis_ann") - pl.col("basis_ann").shift(1).rolling_mean(60)).alias("_basis_minus_mean"),
        pl.col("basis_ann").shift(1).rolling_std(60).alias("_basis_std60"),
    ]).with_columns([
        pl.when(pl.col("_basis_std60") > 0)
        .then(pl.col("_basis_minus_mean") / pl.col("_basis_std60"))
        .otherwise(None)
        .alias("basis_ann_z60d"),
        (pl.col("basis_ann") - pl.col("basis_ann").shift(20)).alias("basis_ann_chg20d"),
        pl.col("basis_ann").alias("basis_ann_level"),
    ]).drop(["_basis_minus_mean", "_basis_std60"])
    audit = {
        "spot_days": spot.height,
        "basis_days": daily.height,
        "coverage": round(daily.height / spot.height, 6) if spot.height else None,
        "roll_days": daily.filter(pl.col("near_contract") != pl.col("near_contract").shift(1)).height - 1,
        "contracts": daily["near_contract"].n_unique() if daily.height else 0,
    }
    return daily, audit


def _monthly_frame(spot: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    month_end = (
        spot.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .group_by("ym", maintain_order=True)
        .agg([pl.col("date").max().alias("date"), pl.col("close").last().alias("close")])
        .sort("date")
        .with_columns((pl.col("close").shift(-1) / pl.col("close") - 1.0).alias("fwd"))
    )
    return month_end.join(daily, on="date", how="left").filter(pl.col("date") >= DISCOVERY[0])


def _stats(frame: pl.DataFrame, signal: str, *, one_sided: bool) -> dict:
    d = frame.filter(pl.col(signal).is_not_null() & pl.col("fwd").is_not_null())
    if d.height < 8:
        return {"n": 0, "ic": None, "t": None, "yearly": {}, "perm_p": None, "coverage": d.height}
    x = d[signal].rank().to_numpy()
    y = d["fwd"].rank().to_numpy()
    ic = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 and np.std(y) > 0 else None
    yearly = {}
    for year, group in d.with_columns(pl.col("date").dt.year().alias("_year")).group_by("_year"):
        if group.height < 6:
            continue
        x_year = group[signal].rank().to_numpy()
        y_year = group["fwd"].rank().to_numpy()
        if np.std(x_year) > 0 and np.std(y_year) > 0:
            yearly[str(year[0] if isinstance(year, tuple) else year)] = round(float(np.corrcoef(x_year, y_year)[0, 1]), 4)
    yearly = dict(sorted(yearly.items()))
    # 市场级信号每月只有一个观测, 使用时间序列 Spearman IC:
    # 月度信号秩与下一月收益秩的相关系数。
    rng = np.random.default_rng(RNG_SEED)
    observed = ic or 0.0
    draws = rng.choice(np.array([-1.0, 1.0]), size=(r42.PERMUTATIONS, len(x)))
    perm = np.asarray([np.corrcoef((x * sign), y)[0, 1] for sign in draws])
    if one_sided:
        perm_p = float((1 + np.sum(perm >= observed)) / (r42.PERMUTATIONS + 1))
    else:
        perm_p = float((1 + np.sum(np.abs(perm) >= abs(observed))) / (r42.PERMUTATIONS + 1))
    return {
        "n": d.height,
        "ic": round(ic, 4) if ic is not None else None,
        "t": round(ic * math.sqrt(len(x) - 2) / math.sqrt(max(1e-12, 1 - ic * ic)), 2) if ic is not None and abs(ic) < 1 else None,
        "yearly": yearly,
        "perm_p": perm_p,
        "coverage": d.height,
    }


def _groups(frame: pl.DataFrame, signal: str) -> dict:
    d = frame.filter(pl.col(signal).is_not_null() & pl.col("fwd").is_not_null()).with_columns(
        ((pl.col(signal).rank() - 1.0) * 3.0 / pl.len()).floor().clip(0, 2).cast(pl.Int64).add(1).alias("g")
    )
    means = d.group_by("g").agg(pl.col("fwd").mean().alias("r")).sort("g")
    monthly = means["r"].to_list()
    if len(monthly) < 3:
        return {"group_monthly": None, "monotonic_rho": None, "spread_annual_net2x": None}
    rho = r42.r22.spearman(list(range(3)), monthly) if len(set(monthly)) > 1 else 0.0
    spread = monthly[-1] - monthly[0]
    return {
        "group_monthly": [round(float(v), 6) for v in monthly],
        "monotonic_rho": round(float(rho), 3),
        "spread_annual_net2x": round((spread - COST_2X) * 12.0, 4),
    }


def main() -> int:
    futures, futures_audit = _ensure_futures()
    spot = _spot()
    daily, basis_audit = _daily_basis(futures, spot)
    monthly = _monthly_frame(spot, daily)
    discovery = monthly.filter((pl.col("date") >= DISCOVERY[0]) & (pl.col("date") <= DISCOVERY[1]))
    validation = monthly.filter((pl.col("date") >= VALIDATION[0]) & (pl.col("date") <= VALIDATION[1]))
    records = {}
    pvalues = []
    for signal in SIGNALS:
        disc_stats = _stats(discovery, signal, one_sided=False)
        val_stats = _stats(validation, signal, one_sided=True)
        disc_groups = _groups(discovery, signal)
        val_groups = _groups(validation, signal)
        d1 = bool(
            basis_audit["coverage"] is not None and basis_audit["coverage"] >= 0.90
            and disc_stats["n"] >= 24 and val_stats["n"] >= 24
        )
        overall_sign = 1 if (disc_stats["ic"] or 0) >= 0 else -1
        yearly_same_sign = sum(
            1 for year in ("2022", "2023", "2024", "2025", "2026")
            if disc_stats["yearly"].get(year) is not None
            and ((1 if disc_stats["yearly"][year] >= 0 else -1) == overall_sign)
        )
        f1 = bool(
            disc_stats["ic"] is not None and disc_stats["ic"] >= 0.20
            and val_stats["ic"] is not None and val_stats["ic"] >= 0.15
            and disc_stats["ic"] > 0 and val_stats["ic"] > 0
            and yearly_same_sign >= 4
        )
        f2 = bool(
            disc_groups["monotonic_rho"] is not None and val_groups["monotonic_rho"] is not None
            and disc_groups["monotonic_rho"] >= 0.7 and val_groups["monotonic_rho"] >= 0.7
            and disc_groups["spread_annual_net2x"] > 0 and val_groups["spread_annual_net2x"] > 0
        )
        records[signal] = {
            "discovery": disc_stats,
            "validation": val_stats,
            "discovery_groups": disc_groups,
            "validation_groups": val_groups,
            "D1": d1,
            "F1": f1,
            "F2": f2,
            "yearly_same_sign": yearly_same_sign,
        }
        pvalues.append(disc_stats["perm_p"])
    qvalues = r42._bh_qvalues(pvalues)
    passed = []
    for signal, q in zip(SIGNALS, qvalues, strict=True):
        record = records[signal]
        record["discovery_q"] = q
        record["F3"] = bool(
            q is not None and q <= 0.10
            and record["validation"]["perm_p"] is not None
            and record["validation"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(record["D1"] and record["F1"] and record["F2"] and record["F3"])
        if record["all_pass"]:
            passed.append(signal)
    result = {
        "window": {"start": str(START), "end": str(END), "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "signals": list(SIGNALS)},
        "data": {"futures": futures_audit, "basis": basis_audit},
        "monthly": monthly.select(["date", "fwd", *SIGNALS]).to_dicts(),
        "records": records,
        "passed": passed,
        "decision": {
            "D1_count": sum(bool(v["D1"]) for v in records.values()),
            "F1_count": sum(bool(v["F1"]) for v in records.values()),
            "F2_count": sum(bool(v["F2"]) for v in records.values()),
            "F3_count": sum(bool(v["F3"]) for v in records.values()),
            "all_pass_count": len(passed),
        },
        "recommendation": "R85 timing overlay" if passed else "negative knowledge; IF basis line closed",
        "caveats": [
            "近月合约按到期日最近、剩余期限 1~45 天选择, 未做展期收益调整",
            "基差年化使用 HS300 收盘与 IF 收盘, 未模拟期货成交",
            "月度调仓只做市场级研究, 不是可直接交易的期货策略",
            "期货数据来自 CFFEX/AkShare, 可能与交易所原始字段存在供应商差异",
        ],
    }
    path = EV / "r84_if_basis.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("data", futures_audit, basis_audit, flush=True)
    print("monthly", monthly.height, "decision", result["decision"], flush=True)
    print("records", {k: {"ic": v["discovery"].get("ic"), "val_ic": v["validation"].get("ic"), "rho": [v["discovery_groups"].get("monotonic_rho"), v["validation_groups"].get("monotonic_rho")], "F1": v["F1"], "F2": v["F2"], "F3": v["F3"]} for k, v in records.items()}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
