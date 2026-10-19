#!/usr/bin/env python3
"""R125: audit the holder-factor misalignment bug and rerun the A sleeve.

The R97-era `_holder_daily` builder sorted the period table and then called
`unique()`, which in Polars 1.44 does not preserve row order by default.  The
following `join_asof` silently assumed a sorted right frame, producing a
non-deterministic, time-shifted holder factor.  This script:

1. proves the fixed builder is deterministic and correctly aligned,
2. re-verifies the on-disk ext_data,
3. reruns the affected A sleeve (macro gate quality) end to end,
4. recomputes the R112 dual-sleeve portfolio with the fixed A curve while
   reusing the (unaffected) B curves,
5. spot-checks one B phase against the R112 cache.

No strategy parameters are changed here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r125_holder_data_audit.json"
CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
BUILD_SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"

FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
PHASE_STARTS = [
    date(2021, 6, 1),
    date(2022, 1, 4),
    date(2022, 7, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
    date(2024, 7, 1),
    date(2025, 1, 2),
    date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT_STARTS = [
    date(2025, 10, 14),
    date(2025, 12, 15),
    date(2026, 2, 13),
    date(2026, 4, 15),
    date(2026, 6, 15),
]
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
COST_2X = (0.0004, 0.0004, 0.0005, 10.0)
COST_3X = (0.0006, 0.0006, 0.001, 15.0)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


r112 = _load_module("r112_script", R112_SCRIPT)
builder = _load_module("build_alt_factor_data", BUILD_SCRIPT)


def _hash_holder(frame: pl.DataFrame) -> str:
    ordered = frame.sort(["symbol", "date"]).select(["symbol", "date", "holder_top10_free_float_share"])
    values = [
        (row[0], row[1].isoformat(), None if row[2] is None else round(float(row[2]), 10))
        for row in ordered.iter_rows()
    ]
    return hashlib.sha256(repr(values).encode()).hexdigest()


def _periods_from_cache() -> pl.DataFrame:
    """Rebuild the holder period table exactly like the fixed builder does."""
    paths = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    frames = [
        pl.read_parquet(
            path,
            columns=["symbol", "period_end", "announce_date", "holder_rank", "free_float_ratio"],
        ).filter(pl.col("holder_rank") <= 10)
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed")
    raw = raw.filter(
        pl.col("announce_date").is_not_null()
        & pl.col("period_end").is_not_null()
        & pl.col("free_float_ratio").is_not_null()
    )
    grouped = (
        raw.group_by(["symbol", "period_end", "announce_date"])
        .agg(pl.col("free_float_ratio").sum().alias("value"))
        .sort(["symbol", "period_end"])
    )
    rows = []
    for (symbol,), part in grouped.group_by("symbol", maintain_order=True):
        for row in part.sort("period_end").to_dicts():
            rows.append(
                {
                    "symbol": symbol,
                    "effective_date": row["announce_date"] + __import__("datetime").timedelta(days=1),
                    "value": float(row["value"]),
                }
            )
    return (
        pl.DataFrame(
            rows,
            schema={"symbol": pl.String, "effective_date": pl.Date, "value": pl.Float64},
        )
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )


def _manual_asof(periods: pl.DataFrame, symbol: str, day: date) -> float | None:
    part = periods.filter((pl.col("symbol") == symbol) & (pl.col("effective_date") <= day))
    if part.height == 0:
        return None
    return float(part.sort("effective_date")["value"][-1])


def data_checks() -> dict:
    dates, symbols = builder._calendar()
    h1 = builder._holder_daily(dates, symbols)
    h2 = builder._holder_daily(dates, symbols)
    hash1, hash2 = _hash_holder(h1), _hash_holder(h2)
    deterministic = hash1 == hash2

    periods = _periods_from_cache()
    dup = periods.height - periods.unique(subset=["symbol", "effective_date"]).height
    ordered = periods.equals(periods.sort(["symbol", "effective_date"]))

    sample_symbols = ["000001.SZ", "600000.SH", "600519.SH", "000002.SZ", "600036.SH"]
    manual_mismatch = 0
    manual_checked = 0
    for symbol in sample_symbols:
        sub = (
            h1.filter(pl.col("symbol") == symbol)
            .sort("date")
            .filter(pl.col("holder_top10_free_float_share").is_not_null())
        )
        for row in sub.iter_rows(named=True):
            expected = _manual_asof(periods, symbol, row["date"])
            manual_checked += 1
            if expected is None or abs(expected - float(row["holder_top10_free_float_share"])) > 1e-9:
                manual_mismatch += 1
                if manual_mismatch <= 3:
                    print(
                        f"  mismatch {symbol} {row['date']}: got "
                        f"{row['holder_top10_free_float_share']} expected {expected}",
                        flush=True,
                    )

    disk = pl.scan_parquet(
        str(DATA / "ext_data" / "alt_hs300" / "timeseries" / "*" / "part.parquet"),
        hive_partitioning=True,
    ).select(["date", "symbol", "holder_top10_free_float_share"]).collect()
    first = (
        disk.filter(pl.col("holder_top10_free_float_share").is_not_null())
        .group_by("symbol")
        .agg(pl.col("date").min().alias("first_date"))
    )
    coverage_rows = []
    for row in first.iter_rows(named=True):
        symbol = row["symbol"]
        part = disk.filter(pl.col("symbol") == symbol).sort("date")
        after = part.filter(pl.col("date") >= row["first_date"])
        nn = after["holder_top10_free_float_share"].is_not_null().sum()
        coverage_rows.append(nn / after.height)
    coverage_min = min(coverage_rows)
    coverage_mean = st.mean(coverage_rows)

    stock = disk.filter(pl.col("symbol") == "000001.SZ").sort("date")
    stock_nn = stock.filter(pl.col("holder_top10_free_float_share").is_not_null())
    first_row = stock_nn.row(0, named=True)

    return {
        "D1_deterministic": deterministic,
        "D1_hash": hash1[:16],
        "D2_periods_duplicates": dup,
        "D2_periods_sorted": bool(ordered),
        "D2_manual_checked": manual_checked,
        "D2_manual_mismatch": manual_mismatch,
        "D3_first_nonnull_date": str(first_row["date"]),
        "D3_first_value": round(float(first_row["holder_top10_free_float_share"]), 6),
        "D4_coverage_min": round(coverage_min, 6),
        "D4_coverage_mean": round(coverage_mean, 6),
        "D4_symbols": len(coverage_rows),
    }


def _run_curve(
    service,
    membership: pl.DataFrame,
    *,
    strategy_id: str,
    start: date,
    end: date,
    cost: tuple[float, float, float, float],
    cache_dir: Path,
) -> pl.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cost_tag = "2x" if cost == COST_2X else "3x"
    path = cache_dir / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}_{cost_tag}.parquet"
    if path.exists():
        return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
    fees, comm, stamp, slip = cost
    cfg = r112.StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides={},
        matching="open_t+1",
        fees_pct=fees,
        commission_pct=comm,
        stamp_tax_pct=stamp,
        slippage_bps=slip,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id} {start}: {result.error}")
    frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    print(
        f"{strategy_id} {start} cost={fees}: days={frame.height} "
        f"ret={result.stats.get('total_return')} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return frame


def _load_r112_curve(strategy_id: str, start: date, end: date) -> pl.DataFrame:
    path = R112_CACHE / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}.parquet"
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))


def main() -> int:
    print("== data checks ==", flush=True)
    checks = data_checks()
    print(json.dumps(checks, ensure_ascii=False, indent=1), flush=True)

    membership = r112._historical_membership()
    service = r112._service()

    print("== A sleeve full window ==", flush=True)
    a_2x = _run_curve(
        service, membership, strategy_id=A_SLEEVE, start=FULL_START, end=FULL_END,
        cost=COST_2X, cache_dir=CACHE,
    )
    a_3x = _run_curve(
        service, membership, strategy_id=A_SLEEVE, start=FULL_START, end=FULL_END,
        cost=COST_3X, cache_dir=CACHE,
    )
    a_full = {
        "2x_cost": r112._stats(a_2x),
        "3x_cost": r112._stats(a_3x),
    }

    print("== A sleeve phases ==", flush=True)
    phase_records = {}
    for phase in PHASE_STARTS + RECENT_STARTS:
        curve = _run_curve(
            service, membership, strategy_id=A_SLEEVE, start=phase, end=FULL_END,
            cost=COST_3X, cache_dir=CACHE,
        )
        old_path = R112_CACHE / f"{A_SLEEVE}_{phase.isoformat()}_{FULL_END.isoformat()}.parquet"
        old_stats = None
        if old_path.exists():
            old_curve = pl.read_parquet(old_path).with_columns(pl.col("date").cast(pl.Date))
            old_stats = r112._stats(old_curve)
        phase_records[phase.isoformat()] = {
            "new": r112._stats(curve),
            "old_r112": old_stats,
        }

    def _phase_agg(key: str) -> dict:
        records = [phase_records[p.isoformat()][key] for p in PHASE_STARTS]
        return {
            "total_return_mean": round(st.mean(r["total_return"] for r in records), 6),
            "sharpe_mean": round(st.mean(r["sharpe"] for r in records), 4),
            "mdd_mean": round(st.mean(r["mdd"] for r in records), 6),
            "mdd_worst": round(min(r["mdd"] for r in records), 6),
            "positive": sum(r["total_return"] > 0 for r in records),
            "n": len(records),
        }

    recent_records = [phase_records[p.isoformat()]["new"] for p in RECENT_STARTS]
    a_phase = {
        "new": _phase_agg("new"),
        "old_r112": _phase_agg("old_r112"),
        "recent_new": {
            "total_return_mean": round(st.mean(r["total_return"] for r in recent_records), 6),
            "positive": sum(r["total_return"] > 0 for r in recent_records),
            "n": len(recent_records),
        },
    }

    print("== dual sleeve rerun (new A + cached B) ==", flush=True)
    portfolio_long = {}
    portfolio_recent = {}
    portfolio_phases = {}
    for phase in PHASE_STARTS + RECENT_STARTS:
        key = phase.isoformat()
        a_curve = pl.read_parquet(
            CACHE / f"{A_SLEEVE}_{key}_{FULL_END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b_curve = _load_r112_curve(B_SLEEVE, phase, FULL_END)
        fixed, _ = r112._combine(a_curve, b_curve, "fixed")
        risk, weights = r112._combine(a_curve, b_curve, "risk")
        portfolio_phases[key] = {
            "A_macro_gate_quality": r112._stats(a_curve),
            "B_industry_relative_strength_h20_sl20": r112._stats(b_curve),
            "fixed_50_50": r112._stats(fixed),
            "risk_parity": r112._stats(risk),
            "risk_parity_weight_a_mean": round(st.mean(w["weight_a"] for w in weights), 4),
        }
        target = portfolio_long if phase in PHASE_STARTS else portfolio_recent
        target[key] = portfolio_phases[key]
        print(
            f"[{key}] A={portfolio_phases[key]['A_macro_gate_quality']['total_return']:+.3f} "
            f"fixed={portfolio_phases[key]['fixed_50_50']['total_return']:+.3f} "
            f"risk={portfolio_phases[key]['risk_parity']['total_return']:+.3f}",
            flush=True,
        )

    def _agg(records: dict, name: str) -> dict:
        return r112._aggregate([records[key][name] for key in sorted(records)])

    portfolio = {
        "long": {
            name: _agg(portfolio_long, name)
            for name in ("A_macro_gate_quality", "B_industry_relative_strength_h20_sl20", "fixed_50_50", "risk_parity")
        },
        "recent": {
            name: _agg(portfolio_recent, name)
            for name in ("A_macro_gate_quality", "B_industry_relative_strength_h20_sl20", "fixed_50_50", "risk_parity")
        },
        "phase_records": portfolio_phases,
    }

    print("== B sleeve spot check ==", flush=True)
    spot_phase = date(2023, 7, 3)
    b_new = _run_curve(
        service, membership, strategy_id=B_SLEEVE, start=spot_phase, end=FULL_END,
        cost=COST_3X, cache_dir=CACHE,
    )
    b_old = _load_r112_curve(B_SLEEVE, spot_phase, FULL_END)
    joined = (
        b_new.rename({"value": "new"})
        .join(b_old.rename({"value": "old"}), on="date", how="inner")
        .sort("date")
    )
    max_diff = (joined["new"] - joined["old"]).abs().max()
    b_check = {
        "phase": str(spot_phase),
        "rows": joined.height,
        "max_abs_diff": None if max_diff is None else float(max_diff),
        "identical": bool(max_diff is not None and float(max_diff) < 1e-9),
    }

    r112_evidence = json.loads(
        (EVIDENCE / "r112_dual_sleeve_risk_parity.json").read_text(encoding="utf-8")
    )

    result = {
        "purpose": "R125 holder misalignment audit and A-sleeve rerun",
        "root_cause": {
            "file": "scripts/build_alt_factor_data.py",
            "function": "_holder_daily",
            "issue": (
                "periods frame was sorted before unique(); Polars unique() defaults to "
                "maintain_order=False, shuffling rows; the following join_asof assumed a "
                "sorted right frame and silently produced a non-deterministic time shift"
            ),
            "introduced_in_commit": "e4cf78d6",
            "fix": "call unique() first, then sort()",
        },
        "data_checks": checks,
        "a_sleeve_full": a_full,
        "a_sleeve_phases": a_phase,
        "a_sleeve_phase_records": phase_records,
        "portfolio_rerun": portfolio,
        "b_sleeve_check": b_check,
        "r112_reference": {
            "long": r112_evidence.get("long"),
            "recent": r112_evidence.get("recent"),
        },
        "decision": {
            "D1": checks["D1_deterministic"],
            "D2": checks["D2_periods_duplicates"] == 0 and checks["D2_periods_sorted"] and checks["D2_manual_mismatch"] == 0,
            "D3": checks["D3_first_nonnull_date"] == "2020-04-22" and abs(checks["D3_first_value"] - 72.048) < 1e-6,
            "D4": checks["D4_coverage_min"] >= 0.99,
            "A4": b_check["identical"],
        },
    }
    result["decision"]["all_data_checks_pass"] = all(result["decision"].values())

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("data checks:", json.dumps(result["decision"], ensure_ascii=False), flush=True)
    print("A full 2x:", a_full["2x_cost"], flush=True)
    print("A full 3x:", a_full["3x_cost"], flush=True)
    print("A phases new:", a_phase["new"], flush=True)
    print("A phases old:", a_phase["old_r112"], flush=True)
    print("portfolio long fixed:", portfolio["long"]["fixed_50_50"], flush=True)
    print("portfolio long risk:", portfolio["long"]["risk_parity"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
