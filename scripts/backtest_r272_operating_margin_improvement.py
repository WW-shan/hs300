#!/usr/bin/env python3
"""R272 system-engine test of operating-margin improvement against a matched control."""
from __future__ import annotations

import bisect
import gc
import hashlib
import importlib.util
import json
import math
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r272_preregistration.json"
OUT = EVIDENCE / "r272_operating_margin_improvement_corrected.json"
BUILD_EVIDENCE = EVIDENCE / "r272_factor_build_final.json"
PARTITION_MANIFEST = EVIDENCE / "r272_factor_build_final_partitions.json"
FACTOR_CONFIG_PATH = DATA / "ext_data/alt_hs300_opm/config.json"
IMPLEMENTATION_LOCK = EVIDENCE / "r272_implementation_correction_final_v3.json"
CACHE = DATA / "research/r272_cache_corrected"
START, END = date(2023, 5, 4), date(2026, 10, 16)
SEGMENTS = [(START, date(2024, 12, 31)), (date(2025, 1, 2), END)]
WINDOW = 252
FACTOR_CONFIG = "alt_hs300_opm"
FACTOR_COLUMN = "operating_margin_yoy_change_q"
FACTOR_ID = "ext_alt_hs300_opm_operating_margin_yoy_change_q"
DISCOVERY_KEY = "discovery_2022_2024"
STRATEGIES = {
    "candidate": "ai_hs300_operating_margin_improvement_quality_h20_sl20",
    "control": "ai_hs300_operating_margin_improvement_quality_control",
}
CORRELATION_FACTORS = {
    "ocf_margin_4q": ("alt_hs300", "ocf_margin_4q"),
    "cash_to_assets": ("alt_hs300", "cash_to_assets"),
    "sga_ratio_4q_low": ("alt_hs300", "sga_ratio_4q_low"),
    "gross_margin_yoy_improvement_q": ("alt_hs300", "gross_margin_yoy_improvement_q"),
    "gross_margin_stability_4q_low": ("alt_hs300", "gross_margin_stability_4q_low"),
    "risk_adjusted_momentum_60d": ("alt_hs300", "risk_adjusted_momentum_60d"),
    "trend_efficiency_60d": ("alt_hs300", "trend_efficiency_60d"),
    "idio_vol_60d_low": ("alt_hs300", "idio_vol_60d_low"),
    "stock_rel_industry_20d": ("alt_hs300", "stock_rel_industry_20d"),
    "capex_intensity_low": ("alt_hs300", "capex_intensity_low"),
    "free_cash_flow_assets": ("alt_hs300_fcf", "free_cash_flow_assets"),
    "asset_growth_yoy_low": ("alt_hs300_ag", "asset_growth_yoy_low"),
    "working_capital_change_yoy_low": ("alt_hs300_wc", "working_capital_change_yoy_low"),
    "gross_profitability": ("alt_hs300_gp", "gross_profitability"),
    "piotroski7_score": ("alt_hs300_pf7", "piotroski7_score"),
    "management_ocf_gm_min_rank": ("alt_hs300_minrank", "management_ocf_gm_min_rank"),
    "quality_relative_strength_min_rank": ("alt_hs300_qrs", "quality_relative_strength_min_rank"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _membership(r221) -> tuple[pl.DataFrame, dict]:
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    return membership, r221._universe_audit(all_members)


def _factor_rows() -> pl.DataFrame:
    paths = sorted((DATA / "ext_data" / FACTOR_CONFIG / "timeseries").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("R272 factor partitions are missing; build factor first")
    build = json.loads(BUILD_EVIDENCE.read_text(encoding="utf-8"))
    if not PARTITION_MANIFEST.exists() or _sha256(PARTITION_MANIFEST) != build.get("partition_manifest", {}).get("sha256"):
        raise RuntimeError("R272 factor partition manifest is missing or does not match its build evidence")
    expected_hashes = json.loads(PARTITION_MANIFEST.read_text(encoding="utf-8"))
    actual_hashes = {path.relative_to(ROOT).as_posix(): _sha256(path) for path in paths}
    if not expected_hashes or expected_hashes != actual_hashes:
        raise RuntimeError("R272 factor partition inventory differs from corrected build evidence")
    if build.get("factor_id") != FACTOR_ID or build.get("config_id") != FACTOR_CONFIG or build.get("field") != FACTOR_COLUMN:
        raise RuntimeError("R272 corrected build evidence has an unexpected factor identity")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", FACTOR_COLUMN])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
        .sort(["date", "symbol"])
    )


def _daily_membership(membership: pl.DataFrame, dates: list[date]) -> pl.DataFrame:
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")
    return (
        pl.DataFrame({"date": dates}, schema={"date": pl.Date})
        .sort("date")
        .join_asof(snapshot_dates, left_on="date", right_on="effective_date", strategy="backward")
        .join(membership, on="effective_date", how="inner")
        .select(["date", "symbol"]).unique().sort(["date", "symbol"])
    )


def _coverage(membership: pl.DataFrame, factor: pl.DataFrame, dates: list[date]) -> dict:
    eligible = _daily_membership(membership, dates).join(
        factor.filter(pl.col("date").is_between(START, END, closed="both")),
        on=["date", "symbol"], how="left",
    )
    recent_dates = dates[-WINDOW:]
    recent = eligible.filter(pl.col("date").is_in(recent_dates))
    valid = eligible.filter(pl.col(FACTOR_COLUMN).is_not_null())
    return {
        "eligible_member_rows": eligible.height,
        "valid_member_rows": valid.height,
        "full_coverage": round(valid.height / eligible.height, 6) if eligible.height else 0.0,
        "recent_member_rows": recent.height,
        "recent_valid_rows": int(recent[FACTOR_COLUMN].is_not_null().sum()),
        "recent_252_trading_day_coverage": round(float(recent[FACTOR_COLUMN].is_not_null().mean()), 6) if recent.height else 0.0,
        "first_valid_date": str(valid["date"].min()) if valid.height else None,
        "last_valid_date": str(valid["date"].max()) if valid.height else None,
    }


def _pit_audit(
    factor: pl.DataFrame,
    builder,
    base_builder,
    dates: list[date],
    symbols: list[str],
    membership: pl.DataFrame,
) -> dict:
    member_keys = (
        _daily_membership(membership, dates)
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .select(["symbol", "date"])
    )
    expected = (
        builder._factor_daily(base_builder, dates, symbols)
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .select(["symbol", "date", FACTOR_COLUMN])
        .join(member_keys, on=["symbol", "date"], how="semi")
    )
    actual = (
        factor.filter(pl.col("date").is_between(START, END, closed="both"))
        .join(member_keys, on=["symbol", "date"], how="semi")
    )
    compared = expected.join(actual, on=["symbol", "date"], how="full", suffix="_actual")
    actual_name = FACTOR_COLUMN + "_actual"
    null_mismatch = compared.filter(
        pl.col(FACTOR_COLUMN).is_null() != pl.col(actual_name).is_null()
    ).height
    values = compared.filter(pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_name).is_not_null())
    delta = values.select((pl.col(FACTOR_COLUMN) - pl.col(actual_name)).abs().max()).item() if values.height else None
    missing_columns = set(expected.columns) - set(factor.columns)
    return {
        "historical_member_keys": member_keys.height,
        "rows_expected_on_member_keys": expected.height,
        "rows_written_on_member_keys": actual.height,
        "null_mismatch_rows": null_mismatch,
        "max_absolute_value_difference": float(delta) if delta is not None else None,
        "first_and_last_quarter_pit_rebuilt_from_source": True,
        "row_key_duplicates": factor.height - factor.select(["symbol", "date"]).unique().height,
        "row_keys_missing_from_written": expected.join(actual, on=["symbol", "date"], how="anti").height,
        "row_keys_extra_in_written": actual.join(expected, on=["symbol", "date"], how="anti").height,
        "all_checks_pass": (not missing_columns and expected.height == actual.height and null_mismatch == 0
                               and expected.join(actual, on=["symbol", "date"], how="anti").is_empty()
                               and actual.join(expected, on=["symbol", "date"], how="anti").is_empty()
                               and delta is not None and delta <= 1e-12
                               and actual.height == actual.select(["symbol", "date"]).unique().height),
    }


def _monthly_correlations(factor: pl.DataFrame, membership: pl.DataFrame, r166, dates: list[date]) -> dict:
    month_ends = [day for day in r166._month_ends() if START <= day <= END]
    members_by_day = r253_members_by_day(membership, month_ends)
    factor_month = factor.filter(pl.col("date").is_in(month_ends))
    result = {}
    for name, (config, column) in CORRELATION_FACTORS.items():
        paths = sorted((DATA / "ext_data" / config / "timeseries").glob("date=*/part.parquet"))
        if not paths:
            raise RuntimeError(f"correlation factor partitions missing: {config}.{column}")
        other = (
            pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
            .select(["symbol", "date", column]).collect()
            .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
            .filter(pl.col("date").is_in(month_ends))
        )
        monthly = []
        for day in month_ends:
            left = factor_month.filter((pl.col("date") == day) & pl.col("symbol").is_in(members_by_day.get(day, set())))
            right = other.filter(pl.col("date") == day)
            joint = left.join(right, on=["date", "symbol"], how="inner").drop_nulls([FACTOR_COLUMN, column])
            if joint.height < 30 or joint[FACTOR_COLUMN].n_unique() < 2 or joint[column].n_unique() < 2:
                continue
            corr = joint.select(pl.corr(pl.col(FACTOR_COLUMN).rank(method="average"), pl.col(column).rank(method="average"))).item()
            if corr is not None:
                monthly.append(float(corr))
        result[name] = {
            "months": len(monthly),
            "mean_abs_spearman": round(sum(abs(value) for value in monthly) / len(monthly), 6) if monthly else None,
            "mean_signed_spearman": round(sum(monthly) / len(monthly), 6) if monthly else None,
        }
    values = [value["mean_abs_spearman"] for value in result.values() if value["mean_abs_spearman"] is not None]
    return {"factors": result, "max_mean_abs_spearman": max(values) if values else None}


def r253_members_by_day(membership: pl.DataFrame, days: list[date]) -> dict[date, set[str]]:
    ordered = membership.select("effective_date").unique().sort("effective_date")["effective_date"].to_list()
    grouped = {key[0]: set(frame["symbol"].to_list()) for key, frame in membership.group_by("effective_date", maintain_order=True)}
    return {day: grouped[ordered[bisect.bisect_right(ordered, day) - 1]] for day in days if ordered and bisect.bisect_right(ordered, day)}


def _rank_ic(factor: pl.DataFrame, fwd: pl.DataFrame, membership: pl.DataFrame, r166) -> dict:
    month_ends = [day for day in r166._month_ends() if START <= day <= END]
    member_by_day = r253_members_by_day(membership, month_ends)
    joined = factor.rename({"date": "rebalance_date"}).join(fwd, on=["rebalance_date", "symbol"], how="inner")
    result = {}
    splits = {"discovery": (START, date(2024, 12, 31)), "validation": (date(2025, 1, 2), END)}
    for name, (lo, hi) in splits.items():
        values = []
        for day in month_ends:
            if not lo <= day <= hi:
                continue
            rows = joined.filter((pl.col("rebalance_date") == day) & pl.col("symbol").is_in(member_by_day.get(day, set()))).drop_nulls([FACTOR_COLUMN, "forward_return"])
            if rows.height < 30 or rows[FACTOR_COLUMN].n_unique() < 2 or rows["forward_return"].n_unique() < 2:
                continue
            ic = rows.select(pl.corr(pl.col(FACTOR_COLUMN).rank(method="average"), pl.col("forward_return").rank(method="average"))).item()
            if ic is not None:
                values.append(float(ic))
        result[name] = {"months": len(values), "mean_ic": round(st.mean(values), 6) if values else None,
                        "positive_rate": round(sum(value > 0 for value in values) / len(values), 4) if values else None}
    return result


def _result_module(r112, r229, curves: list[pl.DataFrame]) -> dict:
    stitched = r229._stitch(curves)
    return r229._result(r112, stitched)


def _window_stats(curve: pl.DataFrame, r112, start: date, end: date) -> dict:
    window = curve.filter(pl.col("date").is_between(start, end, closed="both"))
    if window.is_empty():
        raise RuntimeError(f"empty pre-registered evaluation window: {start}..{end}")
    return r112._stats(window)


def _run_segment(service, membership: pl.DataFrame, r112, sid: str, start: date, end: date, cost: float):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{sid}_{cost:g}x_{start}_{end}"
    curve_path, trades_path, manifest_path = (CACHE / f"{key}.parquet", CACHE / f"{key}.trades.json", CACHE / f"{key}.manifest.json")
    if curve_path.exists() and trades_path.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sources = {
            "runner_sha256": _sha256(Path(__file__).resolve()),
            "preregistration_sha256": _sha256(PREREG),
            "implementation_correction_lock_sha256": _sha256(IMPLEMENTATION_LOCK),
            "factor_build_evidence_sha256": _sha256(BUILD_EVIDENCE),
            "strategy_source_sha256": _sha256(DATA / "strategies/ai" / f"{sid}.py"),
            "factor_config_sha256": _sha256(DATA / "ext_data/alt_hs300_opm/config.json"),
        }
        if all(manifest.get(key) == value for key, value in expected_sources.items()) and manifest.get("curve_sha256") == _sha256(curve_path) and manifest.get("trades_sha256") == _sha256(trades_path):
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            return curve, json.loads(trades_path.read_text(encoding="utf-8")), {"cached": True, "sha256": {"curve": _sha256(curve_path), "trades": _sha256(trades_path)}}

    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=start, end=end, params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006 * cost / 3, commission_pct=0.0006 * cost / 3, stamp_tax_pct=0.001 * cost / 3,
        slippage_bps=15.0 * cost / 3, max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{sid} {cost}x {start}..{end}: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [
        {"segment": f"{start}..{end}", **{key: trade.get(key) for key in ("symbol", "name", "entry_date", "exit_date", "pnl_pct", "pnl_amount", "exit_reason")}}
        for trade in result.trades
    ]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "round": "R272", "strategy_id": sid, "cost_multiple": cost, "start": str(start), "end": str(end),
        "runner_sha256": _sha256(Path(__file__).resolve()), "preregistration_sha256": _sha256(PREREG),
        "implementation_correction_lock_sha256": _sha256(IMPLEMENTATION_LOCK),
        "factor_build_evidence_sha256": _sha256(BUILD_EVIDENCE),
        "strategy_source_sha256": _sha256(DATA / "strategies/ai" / f"{sid}.py"),
        "factor_config_sha256": _sha256(FACTOR_CONFIG_PATH),
        "curve_sha256": _sha256(curve_path), "trades_sha256": _sha256(trades_path),
        "stats": result.stats, "factor_attribution": result.factor_attribution,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return curve, trades, {"cached": False, "manifest": manifest_path.as_posix(), "sha256": {"curve": manifest["curve_sha256"], "trades": manifest["trades_sha256"]}, "stats": result.stats}


def _rolling_stats(curve: pl.DataFrame) -> dict:
    values = curve["value"].to_list()
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    rolling = [math.prod(1 + value for value in returns[i:i + WINDOW]) - 1 for i in range(max(0, len(returns) - WINDOW + 1))]
    recent = returns[-WINDOW:]
    return {
        "rolling_positive_rate": sum(value > 0 for value in rolling) / len(rolling) if rolling else 0.0,
        "recent_12m_return": math.prod(1 + value for value in recent) - 1 if recent else 0.0,
        "recent_12m_mdd": _mdd(curve.filter(pl.col("date") >= curve["date"][-WINDOW])),
    }


def _mdd(curve: pl.DataFrame) -> float:
    values = curve["value"].to_list()
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return drawdown


def _concentration(trades: list[dict], industry_map: dict[str, str], r239) -> dict:
    return r239._aggregate(trades, industry_map)


def _curve_metrics(curve: pl.DataFrame, r112) -> dict:
    stats = r112._stats(curve)
    rolling = _rolling_stats(curve)
    return {**stats, **rolling}


def _stitch_segment_results(curves: list[pl.DataFrame], r229) -> pl.DataFrame:
    return r229._stitch(curves)


def _run_strategy(service, membership, r112, r229, sid: str, cost: float):
    curves, infos, trades = [], [], []
    for start, end in SEGMENTS:
        curve, segment_trades, info = _run_segment(service, membership, r112, sid, start, end, cost)
        curves.append(curve)
        trades.extend(segment_trades)
        infos.append({"start": str(start), "end": str(end), **info})
        print(f"{sid} {cost:g}x {start}..{end}: {info.get('stats', info)}", flush=True)
        gc.collect()
    stitched = _stitch_segment_results(curves, r229)
    return stitched, trades, infos


def main() -> int:
    if not PREREG.exists():
        raise RuntimeError("R272 preregistration missing")
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("corrected R272 result evidence already exists; refusing to overwrite proof")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not BUILD_EVIDENCE.exists() or not IMPLEMENTATION_LOCK.exists():
        raise RuntimeError("corrected R272 factor build and implementation lock are required")
    if _sha256(BUILD_EVIDENCE) != BUILD_EVIDENCE.with_suffix(".json.sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("corrected R272 factor build evidence checksum mismatch")
    builder_path = ROOT / "scripts/build_r272_operating_margin_factor.py"
    builder_source = _load(builder_path, "builder_r272_lock")
    lock, lock_hash, canonical_factor_id = builder_source._verify_implementation_lock()
    if lock_hash != _sha256(IMPLEMENTATION_LOCK) or canonical_factor_id != FACTOR_ID:
        raise RuntimeError("R272 execution lock factor identity mismatch")
    if prereg.get("factor", {}).get("id") != lock.get("preregistered_factor_id"):
        raise RuntimeError("R272 preregistration factor identity differs from the locked alias")
    if lock.get("corrected_source_sha256", {}).get("runner") != _sha256(Path(__file__).resolve()):
        raise RuntimeError("R272 runner source hash differs from implementation correction lock")
    if lock.get("corrected_source_sha256", {}).get("builder") != _sha256(builder_path):
        raise RuntimeError("R272 builder source hash differs from implementation correction lock")
    if prereg.get("selection_basis", {}).get("feasibility_sha256") != _sha256(EVIDENCE / "r272_data_feasibility.json"):
        raise RuntimeError("R272 feasibility evidence hash changed")

    r112 = _load(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r272")
    r166 = _load(ROOT / "scripts/backtest_r166_weak_regime_factor_scan.py", "r166_r272")
    r221 = _load(ROOT / "scripts/backtest_r221_sga_nonfin_quality.py", "r221_r272")
    r229 = _load(ROOT / "scripts/backtest_r229_ocf_margin.py", "r229_r272")
    r239 = _load(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r272")
    builder = _load(ROOT / "scripts/build_r272_operating_margin_factor.py", "builder_r272")
    base_builder = builder._load_base_builder()
    membership, universe = _membership(r221)
    service = r112._service()

    calendar = r229._benchmark_dates(START, END)
    symbols = sorted(membership["symbol"].unique().to_list())
    factor_all = _factor_rows()
    factor = factor_all.filter(pl.col("date").is_between(START, END, closed="both"))
    coverage = _coverage(membership, factor, calendar)
    pit = _pit_audit(factor_all, builder, base_builder, calendar, symbols, membership)
    corr = _monthly_correlations(factor, membership, r166, calendar)

    return_cache = DATA / "kline_daily_enriched"
    if not return_cache.exists():
        raise RuntimeError("system enriched daily bars required for forward IC are missing")
    forward = r166._forward_returns(calendar, [day for day in r166._month_ends() if START <= day <= END])
    factor_ic = _rank_ic(factor, forward, membership, r166)

    results, all_trades, segment_info = {}, {}, {}
    for name, sid in STRATEGIES.items():
        curve, trades, infos = _run_strategy(service, membership, r112, r229, sid, 3.0)
        results[name] = {"full": _curve_metrics(curve, r112), "trading_days": curve.height}
        all_trades[name] = trades
        segment_info[name] = infos
    cost_stress = {}
    for cost in (4.0, 5.0):
        curve, _, infos = _run_strategy(service, membership, r112, r229, STRATEGIES["candidate"], cost)
        cost_stress[f"{cost:g}x"] = {"full": _curve_metrics(curve, r112), "segments": infos}

    industry = {row["symbol"]: row["industry_l1"] for row in load_industry_l1(DATA).iter_rows(named=True)}
    full_concentration_raw = _concentration(all_trades["candidate"], industry, r239)
    validation_trades = [row for row in all_trades["candidate"] if row["segment"].startswith("2025-01-02..")]
    validation_raw = _concentration(validation_trades, industry, r239)
    full_concentration = {key: value for key, value in full_concentration_raw.items() if key != "per_symbol"}
    validation_concentration = {key: value for key, value in validation_raw.items() if key != "per_symbol"}
    validation_positive = sum(row["pnl_amount"] > 0 for row in validation_raw.get("per_symbol", []))

    c, ctl = results["candidate"]["full"], results["control"]["full"]
    candidate_curves = r229._stitch([_run_segment(service, membership, r112, STRATEGIES["candidate"], start, end, 3.0)[0] for start, end in SEGMENTS])
    control_curves = r229._stitch([_run_segment(service, membership, r112, STRATEGIES["control"], start, end, 3.0)[0] for start, end in SEGMENTS])
    c_result = r229._result(r112, candidate_curves)
    ctl_result = r229._result(r112, control_curves)
    cdisc = _window_stats(candidate_curves, r112, START, SEGMENTS[0][1])
    ctl_disc = _window_stats(control_curves, r112, START, SEGMENTS[0][1])
    cval, ctlval = c_result["validation_2025_2026"], ctl_result["validation_2025_2026"]

    gates = {
        "D1": coverage["full_coverage"] >= 0.99 and coverage["recent_252_trading_day_coverage"] >= 0.99,
        "D2": pit["all_checks_pass"],
        "D3": corr["max_mean_abs_spearman"] is not None and corr["max_mean_abs_spearman"] <= 0.70
               and all(row["months"] >= 24 for row in corr["factors"].values()),
        "I1": factor_ic["discovery"]["months"] >= 18 and (factor_ic["discovery"]["mean_ic"] or 0) >= 0.02,
        "I2": factor_ic["validation"]["months"] >= 18 and (factor_ic["validation"]["mean_ic"] or 0) >= 0.01,
        "G1": cdisc["total_return"] >= ctl_disc["total_return"] + 0.05,
        "G2": cdisc["mdd"] >= ctl_disc["mdd"] - 0.05,
        "G3": cval["total_return"] >= ctlval["total_return"],
        "G4": c["total_return"] > 0 and c["total_return"] >= ctl["total_return"],
        "G5": c["mdd"] >= -0.40 and c["mdd"] >= ctl["mdd"] - 0.05,
        "G6": c["rolling_positive_rate"] >= 0.60 and c["rolling_positive_rate"] >= ctl["rolling_positive_rate"] - 0.02,
        "G7": c["recent_12m_return"] > 0 and c["recent_12m_mdd"] >= ctl["recent_12m_mdd"] - 0.05,
        "G8": cost_stress["4x"]["full"]["total_return"] > 0,
        "G9": cost_stress["5x"]["full"]["total_return"] > 0,
        "C1": full_concentration.get("distinct_symbols", 0) >= 30,
        "C2": full_concentration.get("total_pnl_amount", 0) > 0 and full_concentration.get("top1_net_share", 1) <= 0.25,
        "C3": full_concentration.get("total_pnl_amount", 0) > 0 and full_concentration.get("top3_net_share", 1) <= 0.55,
        "C4": full_concentration.get("total_pnl_amount", 0) > 0 and full_concentration.get("top5_net_share", 1) <= 0.75,
        "C5": validation_positive >= 8,
        "C6": full_concentration.get("top_industry_gross_share") is not None and full_concentration["top_industry_gross_share"] <= 0.45,
        "C7": full_concentration.get("max_symbol_loss_to_gross") is not None and full_concentration["max_symbol_loss_to_gross"] <= 0.20,
    }
    gates["all_pass"] = all(gates.values())
    payload = {
        "round": "R272", "status": "complete",
        "preregistration": PREREG.relative_to(ROOT).as_posix(),
        "preregistration_sha256": _sha256(PREREG),
        "registered_factor_alias": prereg["factor"]["id"],
        "canonical_system_factor_id": FACTOR_ID,
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "factor_build": BUILD_EVIDENCE.relative_to(ROOT).as_posix(),
        "factor_build_sha256": _sha256(BUILD_EVIDENCE),
        "factor_partition_manifest_sha256": _sha256(PARTITION_MANIFEST),
        "implementation_correction_lock": IMPLEMENTATION_LOCK.relative_to(ROOT).as_posix(),
        "implementation_correction_lock_sha256": lock_hash,
        "frozen_discovery_window": [str(START), str(SEGMENTS[0][1])],
        "window": [str(START), str(END)], "universe": universe,
        "execution": {"engine": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "positions": 10, "sizing": "equal", "score_limit": 30, "holding_days": 20, "stop_loss": -0.2, "costs": ["3x", "4x", "5x"]},
        "coverage": coverage, "pit_audit": pit, "independence": corr, "factor_ic": factor_ic,
        "results": results, "segment_manifests": segment_info, "cost_stress": cost_stress,
        "concentration": {"full": full_concentration, "validation": validation_concentration, "validation_positive_symbols": validation_positive, "per_symbol": full_concentration_raw.get("per_symbol", [])},
        "gates": gates,
        "decision": "research_only_rejected" if not gates["all_pass"] else "eligible_for_separate_paper_review_only",
        "limitations": ["Financial source is a restatable current snapshot.", "Historical industry classification is current, not PIT.", "Segment stitching resets positions at the split.", "A pass is not deployment authorization."],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    OUT.with_suffix(".json.sha256").write_text(_sha256(OUT) + "  " + OUT.name + "\n", encoding="ascii")
    print(json.dumps({"gates": gates, "candidate": c, "control": ctl, "cost_stress": {key: value["full"] for key, value in cost_stress.items()}, "factor_ic": factor_ic, "concentration": full_concentration, "result": OUT.relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0 if gates["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
