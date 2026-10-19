#!/usr/bin/env python3
"""R119: PIT Shenwan industry replaces the current snapshot for the B sleeve.

Steps:
1. Download (or reuse) the SW industry classification history file.
2. Rebuild only the two industry factors (industry_mom_20d,
   stock_rel_industry_20d) from PIT industry membership.
3. Swap the ext_data/alt_hs300 directory, run the B sleeve + 15% vol target
   with daily historical HS300 membership, then restore the original ext_data.

All other factor fields, costs, execution and portfolio rules are identical to
R112/R114/R115.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
OUT = EVIDENCE / "r119_pit_industry_vol_target.json"
SW_CACHE = DATA / "research" / "r77_sw_industry"
SW_FILE = SW_CACHE / "StockClassifyUse_stock.xls"
EXT_ROOT = DATA / "ext_data" / "alt_hs300"
EXT_BACKUP = DATA / "research" / "r119_ext_backup"
EXT_NEW = DATA / "research" / "r119_ext_new"
R119_CACHE = DATA / "research" / "r119_cache"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ensure_sw_file() -> Path:
    import requests

    SW_CACHE.mkdir(parents=True, exist_ok=True)
    if not SW_FILE.exists() or SW_FILE.stat().st_size == 0:
        url = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.swsresearch.com/"},
            verify=False,
            timeout=60,
        )
        response.raise_for_status()
        SW_FILE.write_bytes(response.content)
    return SW_FILE


def _industry_history() -> tuple[pl.DataFrame, dict]:
    import pandas as pd

    path = _ensure_sw_file()
    raw = pd.read_excel(path, dtype={"股票代码": "str", "行业代码": "str"})
    frame = pl.from_pandas(raw).select([
        pl.col("股票代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
        pl.col("计入日期").cast(pl.Date).alias("start_date"),
        pl.col("行业代码").cast(pl.Utf8).alias("industry_code"),
    ]).filter(
        pl.col("code").is_not_null()
        & pl.col("start_date").is_not_null()
        & pl.col("industry_code").is_not_null()
    )
    frame = frame.with_columns(
        pl.col("industry_code").str.slice(0, 2).alias("industry_pit")
    ).filter(pl.col("industry_pit").str.len_chars() == 2)
    frame = frame.unique(subset=["code", "start_date", "industry_code"], keep="last").sort(["code", "start_date"])
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    out = frame.join(instruments, on="code", how="inner").select(["symbol", "start_date", "industry_pit"]).sort(["symbol", "start_date"])
    audit = {
        "rows": out.height,
        "symbols": out["symbol"].n_unique(),
        "industries": out["industry_pit"].n_unique(),
        "start_min": str(out["start_date"].min()),
        "start_max": str(out["start_date"].max()),
    }
    return out, audit


def _rebuild_industry_factors() -> dict:
    """Recompute the two industry factors with PIT industry and swap ext_data."""
    industry, audit = _industry_history()

    partitions = sorted((EXT_ROOT / "timeseries").glob("date=*/part.parquet"))
    if not partitions:
        raise RuntimeError("ext_data/alt_hs300/timeseries is empty")
    existing = pl.concat(
        [pl.read_parquet(p).with_columns(pl.lit(p.parent.name.split("=")[1]).str.to_date().alias("date")) for p in partitions],
        how="diagonal_relaxed",
    )
    dates = sorted(existing["date"].unique().to_list())
    symbols = sorted(existing["symbol"].unique().to_list())
    print(f"ext rows={existing.height} dates={len(dates)} symbols={len(symbols)}", flush=True)

    price_paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    panel = (
        pl.scan_parquet([str(p) for p in price_paths], extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_in(dates))
        .select(["symbol", "date", "close"])
        .collect()
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("_ret20")
        )
    )
    panel = panel.sort(["symbol", "date"]).join_asof(
        industry.sort(["symbol", "start_date"]),
        left_on="date",
        right_on="start_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    grouped = (
        panel.filter(pl.col("industry_pit").is_not_null())
        .group_by(["date", "industry_pit"])
        .agg(pl.col("_ret20").mean().alias("industry_mom_20d"))
    )
    rebuilt = (
        panel.join(grouped, on=["date", "industry_pit"], how="left")
        .with_columns(
            (pl.col("_ret20") - pl.col("industry_mom_20d")).alias("stock_rel_industry_20d")
        )
        .select(["symbol", "date", "industry_mom_20d", "stock_rel_industry_20d"])
    )
    coverage = round(float(panel["industry_pit"].is_not_null().mean()), 6)
    print(f"PIT industry coverage={coverage}", flush=True)

    merged = (
        existing.drop(["industry_mom_20d", "stock_rel_industry_20d"])
        .join(rebuilt, on=["symbol", "date"], how="left")
        .select([
            "symbol", "date",
            "holder_top10_free_float_share",
            "equity_risk_premium",
            "credit_spread_5y_low",
            "term_spread_10y_2y",
            "industry_mom_20d",
            "stock_rel_industry_20d",
        ])
    )

    # Swap directories: keep the original for restore.
    if EXT_BACKUP.exists():
        shutil.rmtree(EXT_BACKUP)
    shutil.move(str(EXT_ROOT), str(EXT_BACKUP))
    try:
        out_root = EXT_NEW / "timeseries"
        if out_root.exists():
            shutil.rmtree(out_root)
        out_root.mkdir(parents=True, exist_ok=True)
        # Preserve the ext config.json: factor registry sync requires it.
        config_src = EXT_BACKUP / "config.json"
        if config_src.exists():
            shutil.copy2(config_src, EXT_NEW / "config.json")
        count = 0
        for part in merged.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
            day = part["date"][0]
            target = out_root / f"date={day.isoformat()}" / "part.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            part.drop("date").write_parquet(target)
            count += 1
        shutil.move(str(EXT_NEW), str(EXT_ROOT))
        print(f"swapped ext_data partitions={count}", flush=True)
    except Exception:
        if EXT_ROOT.exists():
            shutil.rmtree(EXT_ROOT)
        shutil.move(str(EXT_BACKUP), str(EXT_ROOT))
        raise

    # Sync factor registry caches in this process.
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    return {"industry": audit, "industry_coverage": coverage, "partitions": len(partitions)}


def _restore_ext() -> None:
    if EXT_BACKUP.exists():
        if EXT_ROOT.exists():
            shutil.rmtree(EXT_ROOT)
        shutil.move(str(EXT_BACKUP), str(EXT_ROOT))
        from app.factors import ext_factors

        ext_factors.invalidate_ext_caches(DATA)
        ext_factors.ensure_synced(DATA)
        print("restored ext_data", flush=True)


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15
    r112.CACHE = R119_CACHE

    swap_info = _rebuild_industry_factors()
    try:
        membership = r112._historical_membership()
        service = r112._service()
        strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]

        phase_outputs = {}
        recent_outputs = {}
        for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
            key = phase.isoformat()
            b = r112._run_sleeve(service, membership, strategy_id=strategy_id, start=phase, end=r112.END)
            overlay = r114._vol_target(b)
            rec = {
                "phase": key,
                "B_pit_industry": r112._stats(b),
                "B_pit_industry_vol_target_15pct": r112._stats(overlay["curve"]),
                "annual_turnover": round(overlay["annual_turnover"], 4),
                "weight_mean": round(st.mean(row["weight"] for row in overlay["weight_records"]), 4),
                "weight_min": round(min(row["weight"] for row in overlay["weight_records"]), 4),
                "weight_max": round(max(row["weight"] for row in overlay["weight_records"]), 4),
            }
            if phase in r112.PHASE_STARTS:
                phase_outputs[key] = rec
            else:
                recent_outputs[key] = rec
            print(
                f"[{key}] B={rec['B_pit_industry']['total_return']:+.3f} "
                f"vol15={rec['B_pit_industry_vol_target_15pct']['total_return']:+.3f} "
                f"turnover={rec['annual_turnover']:.2f}",
                flush=True,
            )

        names = ("B_pit_industry", "B_pit_industry_vol_target_15pct")
        long = {
            name: r112._aggregate([phase_outputs[key][name] for key in sorted(phase_outputs)])
            for name in names
        }
        recent = {
            name: r112._aggregate([recent_outputs[key][name] for key in sorted(recent_outputs)])
            for name in names
        }
        annual_turnover_mean = round(
            st.mean(phase_outputs[key]["annual_turnover"] for key in phase_outputs), 4
        )
        weight_mean = round(st.mean(phase_outputs[key]["weight_mean"] for key in phase_outputs), 4)
        weight_min = round(min(phase_outputs[key]["weight_min"] for key in phase_outputs), 4)
        weight_max = round(max(phase_outputs[key]["weight_max"] for key in phase_outputs), 4)

        overlay = long["B_pit_industry_vol_target_15pct"]
        base = long["B_pit_industry"]
        d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
        e1 = bool(
            overlay["annual_mean"] >= 0.15
            and overlay["sharpe_mean"] >= 0.90
            and overlay["mdd_mean"] >= -0.225
            and overlay["mdd_worst"] >= -0.30
        )
        e2 = overlay["positive_phases"] == 10
        e3 = bool(
            recent["B_pit_industry_vol_target_15pct"]["total_return_mean"] > 0
            and recent["B_pit_industry_vol_target_15pct"]["positive_phases"] >= 4
        )
        e4 = bool(
            overlay["sharpe_mean"] >= base["sharpe_mean"] + 0.15
            and overlay["annual_mean"] >= base["annual_mean"] * 0.60
            and overlay["mdd_mean"] >= base["mdd_mean"] + 0.15
        )
        e5 = bool(0.30 <= weight_mean <= 1.00)
        e6 = annual_turnover_mean <= 1.5
        decision = {"D1": d1, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6}
        decision["all_pass"] = all(decision.values())

        result = {
            "purpose": "R119 PIT Shenwan industry replaces the current snapshot for the B sleeve",
            "universe": "daily historical HS300 membership snapshots",
            "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
            "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
            "sleeve": strategy_id,
            "industry": swap_info,
            "rule": {
                "lookback_days": 60,
                "target_annual_volatility": 0.15,
                "weight_bounds": [0.2, 1.0],
                "rebalance": "monthly",
                "round_trip_cost": 0.0052,
            },
            "long": long,
            "recent": recent,
            "phase_records": phase_outputs,
            "recent_records": recent_outputs,
            "annual_turnover_mean": annual_turnover_mean,
            "weight": {"mean": weight_mean, "min": weight_min, "max": weight_max},
            "decision": decision,
            "final_recommendation": (
                "PIT-industry volatility target candidate for forward paper trading"
                if decision["all_pass"]
                else None
            ),
            "caveats": [
                "SW industry code first 2 digits is a proxy for L1, not official name.",
                "Industry start_date is used as the effective date; update_time is not used.",
                "Membership is daily historical HS300 snapshots.",
                "The 3x cost convention is a stress test, not an execution-cost estimate.",
            ],
        }
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved", OUT, flush=True)
        print("decision", decision, flush=True)
        print("long overlay", long["B_pit_industry_vol_target_15pct"], flush=True)
        print("recent overlay", recent["B_pit_industry_vol_target_15pct"], flush=True)
    finally:
        _restore_ext()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())