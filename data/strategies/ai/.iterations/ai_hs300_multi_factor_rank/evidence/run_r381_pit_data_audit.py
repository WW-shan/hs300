"""R381 data-lineage hardening for the frozen hold-30 candidate.

This module intentionally keeps data repair separate from portfolio evaluation.
The membership audit requires an observed snapshot for every session instead of
silently carrying the last available snapshot forward.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
R40 = RESEARCH / "r40_engine_data"
PREREG = EVIDENCE / "r381_preregistration.json"
DAILY_MEMBERSHIP = RESEARCH / "r78_daily_membership/daily_snapshots.parquet"
SW_INDUSTRY = RESEARCH / "r77_sw_industry/StockClassifyUse_stock.xls"
INSTRUMENTS = DATA / "instruments/instruments.parquet"
OUTPUT = EVIDENCE / "r381_pit_data_audit.json"
RESULT = EVIDENCE / "r381_result.md"
INPUT_AUDIT = EVIDENCE / "r381_input_audit.json"
R380_AUDIT = EVIDENCE / "r380_trade_audit_v2.json"
R380_RUNNER = EVIDENCE / "run_r380_trade_audit_v2.py"
PIT_CACHE = RESEARCH / "r381_pit_cache"
R381_MEMBERSHIP = RESEARCH / "r381_membership/daily_snapshots.parquet"
R381_INDUSTRY = RESEARCH / "r381_pit_industry/industry_history.parquet"
R381_EXT_FRAME = RESEARCH / "r381_pit_industry/alt_hs300_required_fields.parquet"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
SEALED_START = date(2025, 5, 6)
RECENT_START = date(2025, 11, 5)
ROLLING_STARTS = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5), date(2025, 5, 5))
COHORT_STARTS = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
INITIAL_CAPITAL = 1_000_000.0
EXPECTED_MEMBERS = 300
INDUSTRY_COVERAGE_MIN = 0.99
MINIMAL_EXT_FIELDS = (
    "holder_top10_free_float_share",
    "equity_risk_premium",
    "credit_spread_5y_low",
    "term_spread_10y_2y",
)
CELL = "fade05_h30_sl20"
CONTROL_CELL = "fade05_h20_sl20"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "r371_industry_fade_5d_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
SCENARIOS = {
    "2x": {
        "stock": {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
        "portfolio_turnover": 0.0052,
        "defensive_turnover": 0.001,
    },
    "3x": {
        "stock": {"fees": 0.0006, "commission": 0.0006, "stamp": 0.001, "slippage": 15.0},
        "portfolio_turnover": 0.0078,
        "defensive_turnover": 0.0015,
    },
}
sys.path.insert(0, str(ROOT / "backend"))


class DataIntegrityGateError(RuntimeError):
    """Raised before any portfolio return is read when data gates fail."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _date_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalize_membership_frame(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "query_date": pl.Date,
                "update_date": pl.Date,
                "symbol": pl.String,
                "name": pl.String,
            }
        )
    date_column = "query_date" if "query_date" in frame.columns else "effective_date"
    if date_column not in frame.columns or "symbol" not in frame.columns:
        raise ValueError("membership frame requires query_date/effective_date and symbol")
    update_expr = (
        pl.col("update_date").cast(pl.Date)
        if "update_date" in frame.columns
        else pl.col(date_column).cast(pl.Date)
    )
    name_expr = pl.col("name").cast(pl.String) if "name" in frame.columns else pl.lit("")
    return (
        frame.select(
            pl.col(date_column).cast(pl.Date).alias("query_date"),
            update_expr.alias("update_date"),
            pl.col("symbol").cast(pl.String),
            name_expr.alias("name"),
        )
        .drop_nulls(["query_date", "symbol"])
        .unique(subset=["query_date", "symbol"], keep="last")
        .sort(["query_date", "symbol"])
    )


def audit_membership_coverage(
    membership: pl.DataFrame,
    sessions: Sequence[date],
    *,
    expected_count: int = EXPECTED_MEMBERS,
) -> dict[str, Any]:
    if membership.is_empty():
        raw_keys = membership
    else:
        date_column = "query_date" if "query_date" in membership.columns else "effective_date"
        if date_column not in membership.columns or "symbol" not in membership.columns:
            raise ValueError("membership frame requires query_date/effective_date and symbol")
        raw_keys = membership.select(
            pl.col(date_column).cast(pl.Date).alias("query_date"),
            pl.col("symbol").cast(pl.String),
        ).drop_nulls(["query_date", "symbol"])
    duplicate_rows = raw_keys.height - raw_keys.unique(subset=["query_date", "symbol"]).height
    frame = _normalize_membership_frame(membership)
    ordered_sessions = sorted(set(_date_value(day) for day in sessions))
    if not ordered_sessions:
        raise ValueError("membership audit requires at least one session")
    counts = frame.group_by("query_date").agg(pl.col("symbol").n_unique().alias("symbols"))
    count_map = {row["query_date"]: int(row["symbols"]) for row in counts.iter_rows(named=True)}
    observed = set(count_map)
    missing = [day for day in ordered_sessions if day not in observed]
    invalid = [
        {"date": day.isoformat(), "symbols": count_map[day]}
        for day in ordered_sessions
        if day in observed and count_map[day] != expected_count
    ]
    last_snapshot = max(observed) if observed else None
    stale_tail_days = sum(day > last_snapshot for day in ordered_sessions) if last_snapshot else len(ordered_sessions)
    future_update_rows = frame.filter(pl.col("update_date") > pl.col("query_date")).height
    return {
        "sessions": len(ordered_sessions),
        "observed_dates": len(observed),
        "rows": frame.height,
        "symbols": frame["symbol"].n_unique(),
        "last_snapshot_date": last_snapshot.isoformat() if last_snapshot else None,
        "missing_dates": [day.isoformat() for day in missing],
        "missing_date_count": len(missing),
        "invalid_count_dates": invalid,
        "stale_tail_days": stale_tail_days,
        "future_update_rows": future_update_rows,
        "duplicate_rows": duplicate_rows,
        "complete": not missing and not invalid and future_update_rows == 0 and duplicate_rows == 0,
    }


def fetch_missing_snapshots(
    existing: pl.DataFrame,
    sessions: Sequence[date],
    fetch_snapshot: Callable[[date], list[dict[str, Any]]],
    *,
    expected_count: int = EXPECTED_MEMBERS,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    base = _normalize_membership_frame(existing)
    ordered_sessions = sorted(set(_date_value(day) for day in sessions))
    observed = set(base["query_date"].to_list())
    missing = [day for day in ordered_sessions if day not in observed]
    new_rows: list[dict[str, Any]] = []
    for day in missing:
        raw_rows = fetch_snapshot(day)
        rows = [
            {
                "query_date": day,
                "update_date": _date_value(row.get("update_date") or day),
                "symbol": str(row.get("symbol") or ""),
                "name": str(row.get("name") or ""),
            }
            for row in raw_rows
        ]
        symbols = {row["symbol"] for row in rows if row["symbol"]}
        if len(symbols) != expected_count:
            raise ValueError(
                f"membership snapshot {day}: expected {expected_count} unique symbols, got {len(symbols)}"
            )
        new_rows.extend(rows)
    if new_rows:
        incoming = pl.DataFrame(new_rows).with_columns(
            pl.col("query_date").cast(pl.Date),
            pl.col("update_date").cast(pl.Date),
        )
        merged = pl.concat([base, incoming], how="diagonal_relaxed")
    else:
        merged = base
    merged = _normalize_membership_frame(merged)
    return merged, audit_membership_coverage(merged, ordered_sessions, expected_count=expected_count)


def normalize_industry_history(raw: pl.DataFrame, instruments: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    required = {"股票代码", "计入日期", "行业代码"}
    if not required <= set(raw.columns):
        raise ValueError(f"industry history missing columns: {sorted(required - set(raw.columns))}")
    if not {"symbol", "code"} <= set(instruments.columns):
        raise ValueError("instruments requires symbol and code")
    codes = (
        raw.select(
            pl.col("股票代码").cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
            pl.col("计入日期").cast(pl.Date).alias("effective_date"),
            pl.col("行业代码").cast(pl.String).alias("industry_code"),
        )
        .filter(
            pl.col("code").is_not_null()
            & pl.col("effective_date").is_not_null()
            & pl.col("industry_code").is_not_null()
        )
        .with_columns(pl.col("industry_code").str.slice(0, 2).alias("industry_pit"))
        .filter(pl.col("industry_pit").str.len_chars() == 2)
    )
    mapping = (
        instruments.select(pl.col("symbol").cast(pl.String), pl.col("code").cast(pl.String).str.zfill(6))
        .unique(subset=["code"], keep="last")
    )
    history = (
        codes.join(mapping, on="code", how="inner")
        .select(["symbol", "effective_date", "industry_pit"])
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )
    audit = {
        "source_rows": raw.height,
        "mapped_rows": history.height,
        "mapped_symbols": history["symbol"].n_unique(),
        "industries": history["industry_pit"].n_unique(),
        "effective_date_min": history["effective_date"].min().isoformat() if history.height else None,
        "effective_date_max": history["effective_date"].max().isoformat() if history.height else None,
    }
    return history, audit


def attach_effective_industry(panel: pl.DataFrame, industry: pl.DataFrame) -> pl.DataFrame:
    if not {"symbol", "date"} <= set(panel.columns):
        raise ValueError("panel requires symbol and date")
    if not {"symbol", "effective_date", "industry_pit"} <= set(industry.columns):
        raise ValueError("industry requires symbol, effective_date and industry_pit")
    left = panel.drop("industry_pit") if "industry_pit" in panel.columns else panel
    left = left.with_columns(pl.col("date").cast(pl.Date)).sort(["symbol", "date"])
    right = industry.select(["symbol", "effective_date", "industry_pit"]).sort(["symbol", "effective_date"])
    return left.join_asof(
        right,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )


def rebuild_industry_fields(
    existing: pl.DataFrame,
    prices: pl.DataFrame,
    industry: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    if not {"symbol", "date"} <= set(existing.columns):
        raise ValueError("existing extension frame requires symbol and date")
    if not {"symbol", "date", "close"} <= set(prices.columns):
        raise ValueError("prices requires symbol, date and close")
    price_panel = (
        prices.select(["symbol", "date", "close"])
        .with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
        .sort(["symbol", "date"])
        .with_columns((pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("_ret20"))
    )
    panel = attach_effective_industry(price_panel, industry)
    grouped = (
        panel.filter(pl.col("industry_pit").is_not_null())
        .group_by(["date", "industry_pit"])
        .agg(pl.col("_ret20").mean().alias("industry_mom_20d"))
    )
    rebuilt = (
        panel.join(grouped, on=["date", "industry_pit"], how="left")
        .with_columns((pl.col("_ret20") - pl.col("industry_mom_20d")).alias("stock_rel_industry_20d"))
        .select(["symbol", "date", "industry_mom_20d", "stock_rel_industry_20d"])
    )
    keep = [column for column in existing.columns if column not in {"industry_mom_20d", "stock_rel_industry_20d"}]
    merged = existing.select(keep).join(rebuilt, on=["symbol", "date"], how="left")
    unique_panel = panel.unique(subset=["symbol", "date"])
    coverage = float(unique_panel["industry_pit"].is_not_null().mean()) if unique_panel.height else 0.0
    audit = {
        "price_rows": panel.height,
        "industry_coverage": round(coverage, 9),
        "industry_mom_non_null": int(rebuilt["industry_mom_20d"].is_not_null().sum()),
        "relative_strength_non_null": int(rebuilt["stock_rel_industry_20d"].is_not_null().sum()),
        "coverage_min": INDUSTRY_COVERAGE_MIN,
    }
    return merged, audit


def build_minimal_extension_frame(
    existing: pl.DataFrame,
    prices: pl.DataFrame,
    industry: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    missing = [field for field in MINIMAL_EXT_FIELDS if field not in existing.columns]
    if missing:
        raise ValueError(f"extension frame missing required fields: {missing}")
    retained = existing.select(["symbol", "date", *MINIMAL_EXT_FIELDS])
    rebuilt, audit = rebuild_industry_fields(retained, prices, industry)
    columns = ["symbol", "date", *MINIMAL_EXT_FIELDS, "industry_mom_20d", "stock_rel_industry_20d"]
    return rebuilt.select(columns), {**audit, "retained_fields": list(MINIMAL_EXT_FIELDS)}


def evaluate_data_integrity(membership: dict[str, Any], industry: dict[str, Any]) -> dict[str, bool]:
    membership_complete = bool(membership.get("complete"))
    industry_coverage = float(industry.get("industry_coverage") or 0.0) >= INDUSTRY_COVERAGE_MIN
    return {
        "membership_complete": membership_complete,
        "industry_coverage": industry_coverage,
        "all_pass": membership_complete and industry_coverage,
    }


def evaluate_performance_gates(
    current: dict[str, Any],
    r380_current: dict[str, Any],
) -> dict[str, Any]:
    center = current["center_2x"]
    control = current["control_2x"]
    center_3x = current["center_3x"]
    checks = {
        "2x_annual": center["full"]["annual"] >= 0.08,
        "2x_sharpe": center["full"]["sharpe"] >= 1.0,
        "2x_mdd": center["full"]["mdd"] >= -0.20,
        "2x_2022": center["yearly"].get("2022", -1.0) >= -0.05,
        "2x_2023": center["yearly"].get("2023", -1.0) >= 0.0,
        "2x_recent": center["recent"]["total_return"] > 0.0,
        "2x_rolling": center["rolling"]["positive_count"] >= 3,
        "3x_annual": center_3x["full"]["annual"] >= 0.05,
        "3x_sharpe": center_3x["full"]["sharpe"] >= 0.80,
        "3x_mdd": center_3x["full"]["mdd"] >= -0.25,
        "sealed_positive": center["sealed"]["total_return"] > 0.0,
        "sealed_sharpe": center["sealed"]["sharpe"] >= 1.0,
        "sealed_mdd": center["sealed"]["mdd"] >= -0.10,
        "sealed_beats_control_return": center["sealed"]["total_return"] > control["sealed"]["total_return"],
        "sealed_beats_control_sharpe": center["sealed"]["sharpe"] >= control["sealed"]["sharpe"],
    }
    old = r380_current["center_2x"]["full"]
    deltas = {
        "2x_annual": center["full"]["annual"] - old["annual"],
        "2x_sharpe": center["full"]["sharpe"] - old["sharpe"],
        "2x_mdd": center["full"]["mdd"] - old["mdd"],
        "3x_annual": center_3x["full"]["annual"] - r380_current["center_3x"]["full"]["annual"],
        "3x_sharpe": center_3x["full"]["sharpe"] - r380_current["center_3x"]["full"]["sharpe"],
        "3x_mdd": center_3x["full"]["mdd"] - r380_current["center_3x"]["full"]["mdd"],
    }
    sensitivity_checks = {
        "2x_annual_delta": abs(deltas["2x_annual"]) <= 0.01,
        "2x_sharpe_delta": abs(deltas["2x_sharpe"]) <= 0.05,
        "2x_mdd_delta": deltas["2x_mdd"] >= -0.01,
    }
    return {
        "checks": checks,
        "all_performance_pass": all(checks.values()),
        "r380_sensitivity_deltas": deltas,
        "sensitivity_checks": sensitivity_checks,
        "sensitivity_pass": all(sensitivity_checks.values()),
        "all_pass": all(checks.values()) and all(sensitivity_checks.values()),
    }


def trading_sessions() -> list[date]:
    partitions = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    sessions = [
        date.fromisoformat(path.parent.name.split("=", 1)[1])
        for path in partitions
        if START <= date.fromisoformat(path.parent.name.split("=", 1)[1]) <= END
    ]
    if not sessions or sessions[0] != START or sessions[-1] != END:
        raise RuntimeError("enriched daily partitions do not cover the frozen R381 window")
    return sessions


def _normalize_baostock_code(raw: str) -> str:
    code = str(raw).strip().lower()
    if code.startswith("sh."):
        return f"{code[3:9]}.SH"
    if code.startswith("sz."):
        return f"{code[3:9]}.SZ"
    if code.startswith("bj."):
        return f"{code[3:9]}.BJ"
    raise ValueError(f"unexpected BaoStock code: {raw}")


def _query_baostock_snapshot(bs: Any, day: date) -> list[dict[str, Any]]:
    query = bs.query_hs300_stocks(date=day.isoformat())
    if query.error_code != "0":
        raise RuntimeError(f"query_hs300_stocks {day}: {query.error_code} {query.error_msg}")
    rows: list[dict[str, Any]] = []
    while query.next():
        update_date, raw_code, name = query.get_row_data()
        rows.append({
            "symbol": _normalize_baostock_code(raw_code),
            "name": str(name or ""),
            "update_date": date.fromisoformat(str(update_date)[:10]),
        })
    return rows


def repair_membership(
    sessions: Sequence[date],
    source_path: Path = DAILY_MEMBERSHIP,
    output_path: Path = R381_MEMBERSHIP,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    source = pl.read_parquet(source_path)
    observed = set(source["query_date"].cast(pl.Date).to_list())
    missing = [day for day in sessions if day not in observed]
    if missing:
        import baostock as bs

        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
        try:
            merged, audit = fetch_missing_snapshots(
                source,
                sessions,
                lambda day: _query_baostock_snapshot(bs, day),
                expected_count=EXPECTED_MEMBERS,
            )
        finally:
            bs.logout()
    else:
        merged = _normalize_membership_frame(source)
        audit = audit_membership_coverage(merged, sessions)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.parquet")
    merged.write_parquet(temporary, compression="zstd")
    temporary.replace(output_path)
    return merged, {
        **audit,
        "source_path": source_path.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(source_path),
        "source_last_snapshot_date": source["query_date"].max().isoformat(),
        "repaired_path": output_path.relative_to(ROOT).as_posix(),
        "repaired_sha256": sha256(output_path),
        "fetched_dates": [day.isoformat() for day in missing],
        "fetched_date_count": len(missing),
    }


def load_industry_history() -> tuple[pl.DataFrame, dict[str, Any]]:
    import pandas as pd

    raw = pl.from_pandas(
        pd.read_excel(SW_INDUSTRY, dtype={"股票代码": "str", "行业代码": "str"})
    )
    instruments = pl.read_parquet(INSTRUMENTS).select(["symbol", "code"])
    history, audit = normalize_industry_history(raw, instruments)
    R381_INDUSTRY.parent.mkdir(parents=True, exist_ok=True)
    history.write_parquet(R381_INDUSTRY, compression="zstd")
    return history, {
        **audit,
        "source_path": SW_INDUSTRY.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(SW_INDUSTRY),
        "output_path": R381_INDUSTRY.relative_to(ROOT).as_posix(),
        "output_sha256": sha256(R381_INDUSTRY),
    }


def load_source_extension_fields() -> pl.DataFrame:
    partitions = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    if not partitions:
        raise RuntimeError("alt_hs300 timeseries is empty")
    frames: list[pl.DataFrame] = []
    for path in partitions:
        day = date.fromisoformat(path.parent.name.split("=", 1)[1])
        part = pl.read_parquet(path, columns=["symbol", *MINIMAL_EXT_FIELDS])
        frames.append(part.with_columns(pl.lit(day, dtype=pl.Date).alias("date")))
    return pl.concat(frames, how="diagonal_relaxed").sort(["date", "symbol"])


def load_price_panel(symbols: Sequence[str], dates: Sequence[date]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol").is_in(list(symbols)) & pl.col("date").is_in(list(dates)))
        .select(["symbol", "date", "close"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
        .sort(["symbol", "date"])
    )


def membership_industry_coverage(membership: pl.DataFrame, industry: pl.DataFrame) -> float:
    panel = membership.select(
        pl.col("query_date").cast(pl.Date).alias("date"),
        pl.col("symbol").cast(pl.String),
    ).unique()
    joined = attach_effective_industry(panel, industry)
    return float(joined["industry_pit"].is_not_null().mean()) if joined.height else 0.0


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n",
        encoding="ascii",
    )


def prepare_inputs() -> dict[str, Any]:
    sessions = trading_sessions()
    membership, membership_audit = repair_membership(sessions)
    industry, industry_audit = load_industry_history()
    source_ext = load_source_extension_fields()
    prices = load_price_panel(source_ext["symbol"].unique().to_list(), source_ext["date"].unique().to_list())
    ext_frame, ext_audit = build_minimal_extension_frame(source_ext, prices, industry)
    ext_frame.write_parquet(R381_EXT_FRAME, compression="zstd")
    membership_coverage = membership_industry_coverage(membership, industry)
    industry_audit["industry_coverage"] = round(membership_coverage, 9)
    industry_audit["ext_union_industry_coverage"] = ext_audit["industry_coverage"]
    data_gate = evaluate_data_integrity(membership_audit, industry_audit)
    payload = {
        "round": "R381",
        "stage": "data_integrity_before_portfolio_evaluation",
        "preregistration_sha256": sha256(PREREG),
        "window": {"start": str(START), "end": str(END), "sessions": len(sessions)},
        "membership": membership_audit,
        "industry": industry_audit,
        "extension_frame": {
            **ext_audit,
            "path": R381_EXT_FRAME.relative_to(ROOT).as_posix(),
            "sha256": sha256(R381_EXT_FRAME),
            "rows": ext_frame.height,
            "dates": ext_frame["date"].n_unique(),
            "symbols": ext_frame["symbol"].n_unique(),
        },
        "data_gate": data_gate,
        "data_integrity_pass": data_gate["all_pass"],
    }
    write_json_with_checksum(INPUT_AUDIT, payload)
    return payload


def write_ext_timeseries(engine_root: Path, ext_frame: pl.DataFrame) -> dict[str, Any]:
    target = engine_root / "ext_data/alt_hs300/timeseries"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    count = 0
    for part in ext_frame.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
        day = part["date"][0]
        path = target / f"date={day.isoformat()}/part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.drop("date").write_parquet(path, compression="zstd")
        count += 1
    return {
        "partitions": count,
        "rows": ext_frame.height,
        "columns": ext_frame.columns,
    }


def parse_day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def curve_frame(values: list[dict[str, Any]]) -> pl.DataFrame:
    rows = [{"date": parse_day(item["date"]), "value": float(item["value"])} for item in values]
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def stats(values: list[float]) -> dict[str, float | int]:
    if len(values) < 2:
        raise ValueError("equity curve requires at least two observations")
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def segment(frame: pl.DataFrame, start: date, end: date = END) -> dict[str, Any]:
    part = frame.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        raise ValueError(f"segment {start}..{end} is too short")
    return {
        **stats([float(value) for value in part["value"].to_list()]),
        "start": str(part["date"][0]),
        "end": str(part["date"][-1]),
    }


def year_returns(frame: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    result: dict[str, float] = {}
    previous: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        base = previous if previous is not None else values[0]
        result[str(year)] = values[-1] / base - 1.0
        previous = values[-1]
    return result


def rolling_positive(frame: pl.DataFrame) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for start in ROLLING_STARTS:
        part = frame.filter(pl.col("date") >= start).sort("date").head(253)
        if part.height != 253:
            raise ValueError(f"rolling window from {start} is incomplete")
        values = part["value"].to_list()
        windows.append({"start": str(start), "return": float(values[-1] / values[0] - 1.0)})
    return {
        "window_count": len(windows),
        "positive_count": sum(window["return"] > 0 for window in windows),
        "windows": windows,
    }


def membership_for_engine(path: Path = R381_MEMBERSHIP) -> pl.DataFrame:
    frame = pl.read_parquet(path, columns=["query_date", "symbol"])
    return (
        frame.select(
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        )
        .drop_nulls()
        .unique()
        .sort(["effective_date", "symbol"])
    )


def combine_c(outputs: dict[str, Any], cost: str) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r381_combine")
    curves = [outputs[f"C_{cost}_{index}"][0] for index in range(3)]
    curve = r359.combine_cohort_curves(curves, list(COHORT_STARTS))
    trades: list[dict[str, Any]] = []
    for index in range(3):
        trades.extend(
            {"sleeve": "C", **trade}
            for trade in r359.annotate_cohort_trades(outputs[f"C_{cost}_{index}"][1], index)
        )
    return curve, trades


def portfolio_bundle(
    ctx: dict[str, Any],
    a: pl.DataFrame,
    b: pl.DataFrame,
    c: pl.DataFrame,
    cost: str,
) -> dict[str, Any]:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    scenario = SCENARIOS[cost]
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, scenario["defensive_turnover"])
    old_112 = r112.ROUND_TRIP_COST
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, sleeve_weights = r344._combine_three((a, b, c), scenario["portfolio_turnover"], 0.10)
        equity = r114._vol_target(risk)["curve"]
        final = r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            treasury,
            dividend,
            round_trip_cost=scenario["defensive_turnover"],
        )
        return {
            "curve": final["curve"],
            "risk_dates": risk["date"].to_list(),
            "sleeve_weights": sleeve_weights,
            "rebalances": final["rebalances"],
        }
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def current_portfolio_stats(
    outputs: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    c2, _ = combine_c(outputs, "2x")
    c3, _ = combine_c(outputs, "3x")
    bundles = {
        "center_2x": portfolio_bundle(ctx, outputs["A_2x"][0], outputs["B30_2x"][0], c2, "2x"),
        "control_2x": portfolio_bundle(ctx, outputs["A_2x"][0], outputs["B20_2x"][0], c2, "2x"),
        "center_3x": portfolio_bundle(ctx, outputs["A_3x"][0], outputs["B30_3x"][0], c3, "3x"),
    }
    result: dict[str, Any] = {}
    for name, bundle in bundles.items():
        curve = bundle["curve"]
        result[name] = {
            "full": stats([float(value) for value in curve["value"].to_list()]),
            "yearly": year_returns(curve),
            "recent": segment(curve, RECENT_START),
            "sealed": segment(curve, SEALED_START),
            "rolling": rolling_positive(curve),
        }
    return result


def _verify_file_checksum(path: Path) -> str:
    checksum_path = path.with_name(path.name + ".sha256")
    if not checksum_path.exists():
        raise RuntimeError(f"missing checksum: {checksum_path}")
    expected = checksum_path.read_text(encoding="ascii").split()[0]
    actual = sha256(path)
    if expected != actual:
        raise RuntimeError(f"checksum mismatch: {path.name}")
    return actual


def run_current_chain(input_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the frozen A/B/C sleeves on repaired membership and PIT industry inputs."""
    if input_audit is None:
        input_audit = prepare_inputs()
    if not input_audit.get("data_integrity_pass"):
        raise DataIntegrityGateError("R381 data integrity gate failed")
    for path in (PREREG, R381_MEMBERSHIP, R381_EXT_FRAME):
        if not path.exists():
            raise RuntimeError(f"missing R381 input: {path}")
    if sha256(R381_MEMBERSHIP) != input_audit["membership"]["repaired_sha256"]:
        raise RuntimeError("repaired membership hash does not match input audit")
    if sha256(R381_EXT_FRAME) != input_audit["extension_frame"]["sha256"]:
        raise RuntimeError("extension frame hash does not match input audit")

    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r371 = module(EVIDENCE / "run_r371_alpha_parameter_grid.py", "r381_r371")
    r371._write_variant_definitions()
    membership = membership_for_engine()
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    variant_paths = {
        "B": r371.VARIANTS / "strategies" / f"{B_STRATEGY}.py",
        "C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
        "A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
    }
    ext_frame = pl.read_parquet(R381_EXT_FRAME).with_columns(pl.col("date").cast(pl.Date))
    engine_hash = sha256(ROOT / "backend/app/backtest/engine.py")
    work = Path(tempfile.mkdtemp(prefix="r381_engine_data_"))
    old_data = settings.data_dir
    outputs: dict[str, Any] = {}
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r381_isolation").prepare_isolated_data_root(engine_root, DATA)
        ext_audit = write_ext_timeseries(engine_root, ext_frame)
        for name in ("custom_factors", "custom_signals"):
            for source in sorted((r371.VARIANTS / name).glob("*")):
                shutil.copy2(source, engine_root / "user_data" / name / source.name)
        settings.data_dir = engine_root
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", r371.VARIANTS / "strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run_one(
            kind: str,
            strategy_id: str,
            start: date,
            cost: str,
            max_positions: int,
            *,
            overrides: dict[str, Any] | None = None,
            hold: int | None = None,
        ) -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
            stock = SCENARIOS[cost]["stock"]
            config = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides=overrides or {},
                matching="open_t+1",
                fees_pct=stock["fees"],
                commission_pct=stock["commission"],
                stamp_tax_pct=stock["stamp"],
                slippage_bps=stock["slippage"],
                holding_days=hold,
                initial_capital=INITIAL_CAPITAL / (3.0 if kind == "C" else 1.0),
                max_positions=max_positions,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            started = time.perf_counter()
            with r371._patched_membership(membership):
                result = service.run(config)
            if result.error:
                raise RuntimeError(f"R381 {kind} {strategy_id} {cost}: {result.error}")
            curve = curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != END:
                raise RuntimeError(f"R381 {kind} {strategy_id} {cost}: date coverage mismatch")
            elapsed = time.perf_counter() - started
            result_stats = {
                "execution": result.stats.get("execution") or {},
                "selection": result.stats.get("selection") or {},
                "stats": {
                    key_name: result.stats.get(key_name)
                    for key_name in ("total_return", "annual_return", "sharpe", "max_drawdown", "n_trades")
                    if key_name in result.stats
                },
            }
            print(
                f"R381 ran {kind} {strategy_id} {cost}: trades={len(result.trades)} elapsed={elapsed:.1f}s",
                flush=True,
            )
            return curve, result.trades, result_stats, {"elapsed_seconds": elapsed, "cache_hit": False}

        for cost in ("2x", "3x"):
            outputs[f"A_{cost}"] = run_one("A", A_STRATEGY, START, cost, 10)
            outputs[f"B30_{cost}"] = run_one(
                "B",
                B_STRATEGY,
                START,
                cost,
                10,
                overrides={"max_hold_days": 30, "stop_loss": -0.20},
                hold=30,
            )
            if cost == "2x":
                outputs[f"B20_{cost}"] = run_one(
                    "B",
                    B_STRATEGY,
                    START,
                    cost,
                    10,
                    overrides={"max_hold_days": 20, "stop_loss": -0.20},
                    hold=20,
                )
            for index, cohort_start in enumerate(COHORT_STARTS):
                outputs[f"C_{cost}_{index}"] = run_one(
                    "C",
                    C_STRATEGY,
                    cohort_start,
                    cost,
                    50,
                )
        return {
            "outputs": outputs,
            "membership_sha256": membership_hash,
            "engine_sha256": engine_hash,
            "variant_sha256": {key: sha256(path) for key, path in variant_paths.items()},
            "ext_timeseries": ext_audit,
        }
    finally:
        settings.data_dir = old_data
        shutil.rmtree(work, ignore_errors=True)


def data_gap_report(input_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "round": "R381",
        "stage": "data_integrity_before_portfolio_evaluation",
        "data_audit": input_audit,
        "data_integrity_pass": False,
        "performance_evaluated": False,
        "decision": {
            "status": "data_gap_no_portfolio_evaluation",
            "performance_evaluated": False,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
    }


def build_report(
    input_audit: dict[str, Any] | None = None,
    *,
    chain_runner: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if input_audit is None:
        input_audit = prepare_inputs()
    if not input_audit.get("data_integrity_pass"):
        raise DataIntegrityGateError("R381 data integrity gate failed; portfolio returns must not be read")
    _verify_file_checksum(PREREG)
    r380_sha = _verify_file_checksum(R380_AUDIT)
    r380 = json.loads(R380_AUDIT.read_text(encoding="utf-8"))
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    expected_r380 = prereg["trigger"]["r380_evidence_sha256"]
    if r380_sha != expected_r380:
        raise RuntimeError("R380 evidence hash does not match R381 preregistration")

    chain = (chain_runner or run_current_chain)()
    outputs = chain["outputs"]
    ctx = {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r381_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r381_r344"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r381_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r381_r114"),
    }
    current = current_portfolio_stats(outputs, ctx)
    performance = evaluate_performance_gates(current, r380["current_portfolio"])
    all_pass = bool(performance["all_pass"])
    failed_checks = [name for name, passed in performance["checks"].items() if not passed]
    failed_sensitivity = [
        name for name, passed in performance["sensitivity_checks"].items() if not passed
    ]
    return {
        "round": "R381",
        "stage": "pit_data_sensitivity_and_frozen_portfolio_evaluation",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "r380_evidence_sha256": r380_sha,
        "data_audit": input_audit,
        "data_integrity_pass": True,
        "performance_evaluated": True,
        "engine_identity": {
            key: chain[key]
            for key in ("membership_sha256", "engine_sha256", "variant_sha256")
        },
        "run_summary": {
            key: {
                "elapsed_seconds": value[3]["elapsed_seconds"],
                "trades": len(value[1]),
            }
            for key, value in outputs.items()
        },
        "current_portfolio": current,
        "performance_checks": performance["checks"],
        "performance_pass": performance["all_performance_pass"],
        "r380_sensitivity_deltas": performance["r380_sensitivity_deltas"],
        "sensitivity_checks": performance["sensitivity_checks"],
        "sensitivity_pass": performance["sensitivity_pass"],
        "all_pass": all_pass,
        "decision": {
            "status": (
                "candidate_for_paper_trading_review"
                if all_pass
                else "research_only_pit_sensitivity_failed"
            ),
            "data_integrity_pass": True,
            "performance_pass": performance["all_performance_pass"],
            "sensitivity_pass": performance["sensitivity_pass"],
            "failed_performance_checks": failed_checks,
            "failed_sensitivity_checks": failed_sensitivity,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    audit = report["data_audit"]
    if not report.get("data_integrity_pass"):
        membership = audit.get("membership", {})
        industry = audit.get("industry", {})
        return "\n".join([
            "# R381 · 数据血缘修复审计",
            "",
            "数据完整性门槛未通过，因此没有读取或解释任何 R381 组合收益。",
            "",
            "| 数据项 | 结果 |",
            "| --- | ---: |",
            f"| 名单缺失交易日 | {membership.get('missing_date_count', 'unknown')} |",
            f"| 名单非 300 只交易日 | {len(membership.get('invalid_count_dates') or [])} |",
            f"| 未来更新日行数 | {membership.get('future_update_rows', 'unknown')} |",
            f"| 行业覆盖率 | {industry.get('industry_coverage', 0.0):.2%} |",
            "",
            "状态：`data_gap_no_portfolio_evaluation`。无杠杆、无纸面目标、无订单。",
            "",
        ])
    current = report["current_portfolio"]
    lines = [
        "# R381 · PIT 数据修复与冻结策略敏感性",
        "",
        "本轮只修复名单尾部与行业历史口径，不调整 fade、hold、stop、仓位或组合参数。",
        "",
        "| 冻结组合 | 年化 | Sharpe | MDD | 近一年 | 封存段 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| hold-30 中心 2× | {current['center_2x']['full']['annual']:.2%} | {current['center_2x']['full']['sharpe']:.3f} | {current['center_2x']['full']['mdd']:.2%} | {current['center_2x']['recent']['total_return']:.2%} | {current['center_2x']['sealed']['total_return']:.2%} |",
        f"| 20 日对照 2× | {current['control_2x']['full']['annual']:.2%} | {current['control_2x']['full']['sharpe']:.3f} | {current['control_2x']['full']['mdd']:.2%} | {current['control_2x']['recent']['total_return']:.2%} | {current['control_2x']['sealed']['total_return']:.2%} |",
        f"| hold-30 中心 3× | {current['center_3x']['full']['annual']:.2%} | {current['center_3x']['full']['sharpe']:.3f} | {current['center_3x']['full']['mdd']:.2%} | {current['center_3x']['recent']['total_return']:.2%} | {current['center_3x']['sealed']['total_return']:.2%} |",
        "",
        "| 数据完整性 | 结果 |",
        "| --- | ---: |",
        f"| 名单覆盖交易日 | {audit['membership']['sessions']} |",
        f"| 缺失交易日 | {audit['membership']['missing_date_count']} |",
        f"| 行业覆盖率 | {audit['industry']['industry_coverage']:.2%} |",
        "",
        "| 与 R380 的敏感性（主口径 2×） | 差值 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 2× 年化 | {report['r380_sensitivity_deltas']['2x_annual']:+.2%} | <= 1.00pp |",
        f"| 2× Sharpe | {report['r380_sensitivity_deltas']['2x_sharpe']:+.3f} | <= 0.05 |",
        f"| 2× MDD 恶化 | {report['r380_sensitivity_deltas']['2x_mdd']:+.2%} | <= 1.00pp |",
        f"| 3× 年化（诊断） | {report['r380_sensitivity_deltas']['3x_annual']:+.2%} | 3× 以绝对门槛为准 |",
        f"| 3× Sharpe（诊断） | {report['r380_sensitivity_deltas']['3x_sharpe']:+.3f} | 3× 以绝对门槛为准 |",
        f"| 3× MDD 恶化（诊断） | {report['r380_sensitivity_deltas']['3x_mdd']:+.2%} | 3× 以绝对门槛为准 |",
        "",
        f"- 绩效门槛：{sum(report['performance_checks'].values())}/{len(report['performance_checks'])} 项通过。",
        f"- 敏感性门槛：{sum(report['sensitivity_checks'].values())}/{len(report['sensitivity_checks'])} 项通过。",
        f"- 综合判定：{'通过' if report['all_pass'] else '未通过'}，状态 `{report['decision']['status']}`。",
        "- 行业数据是 effective-date 分类史，不是完整公告 vintage；该限制保留在证据中。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ]
    return "\n".join(lines)


def _write_report_files(report: dict[str, Any]) -> None:
    write_json_with_checksum(OUTPUT, report)
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha256(RESULT)}  {RESULT.name}\n",
        encoding="ascii",
    )


def main() -> int:
    for path in (
        OUTPUT,
        OUTPUT.with_name(OUTPUT.name + ".sha256"),
        RESULT,
        RESULT.with_name(RESULT.name + ".sha256"),
    ):
        if path.exists():
            raise RuntimeError(f"R381 output exists: {path}")
    input_audit = prepare_inputs()
    if not input_audit.get("data_integrity_pass"):
        report = data_gap_report(input_audit)
    else:
        report = build_report(input_audit)
    _write_report_files(report)
    print(
        json.dumps(
            {
                "data_integrity_pass": report.get("data_integrity_pass"),
                "performance_evaluated": report.get("performance_evaluated"),
                "current_portfolio": report.get("current_portfolio"),
                "performance_checks": report.get("performance_checks"),
                "sensitivity_checks": report.get("sensitivity_checks"),
                "decision": report.get("decision"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
