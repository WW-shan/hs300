"""Point-in-time verification for financial factors.

The audit deliberately recomputes the announcement gate from the raw financial
snapshot instead of trusting the production attachment helper.  A factor is
only promoted when the independently reconstructed values agree with the
attachment path on every observed symbol/date pair.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest.fundamentals import (
    FUNDAMENTAL_FACTORS,
    _latest_effective_snapshot,
    attach_fundamental_factors,
    financial_pit_date,
    load_fundamental_snapshot,
)
from app.factor_research.fingerprints import sha256_file

BUILTIN_FINANCIAL_FACTOR_IDS = tuple(sorted(FUNDAMENTAL_FACTORS))
DIVIDEND_FINANCIAL_FACTOR_IDS = (
    "ext_alt_hs300_dividend_growth_ttm",
    "ext_alt_hs300_dividend_initiation_flag",
    "ext_alt_hs300_dividend_ttm_cash",
    "ext_alt_hs300_dividend_yield",
)

BUILTIN_FINANCIAL_DIRECTIONS = {
    "pb_latest": "low",
    "roe_latest": "high",
    "gross_margin_latest": "high",
    "net_margin_latest": "high",
    "revenue_yoy_latest": "high",
    "net_income_yoy_latest": "high",
    "debt_ratio_latest": "low",
    "ocf_ni": "high",
    "accrual": "low",
    "ocf_debt": "high",
    "core_earnings_ratio": "high",
}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _member_symbols(data_dir: Path, snapshot: pl.DataFrame) -> list[str]:
    available = set(snapshot["symbol"].drop_nulls().cast(pl.Utf8).to_list())
    path = data_dir / "hs300" / "current_members.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            members = payload.get("members", []) if isinstance(payload, Mapping) else []
            selected = sorted(
                str(item["symbol"])
                for item in members
                if isinstance(item, Mapping) and item.get("symbol") in available
            )
            if selected:
                return selected
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return sorted(available)


def _load_daily_panel(
    data_dir: Path,
    *,
    symbols: Sequence[str],
    start: date,
    end: date,
) -> pl.DataFrame:
    paths = sorted((data_dir / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("kline_daily_enriched partitions are missing")
    panel = (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date", "close"])
        .filter(
            pl.col("symbol").is_in(list(symbols))
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .collect()
        .sort(["symbol", "date"])
    )
    if panel.is_empty():
        raise ValueError("financial PIT audit has no daily rows in the requested window")
    return panel


def _expected_factor_panel(
    panel: pl.DataFrame,
    snapshot: pl.DataFrame,
    factor_id: str,
) -> pl.DataFrame:
    spec = FUNDAMENTAL_FACTORS[factor_id]
    announce_column = str(spec["announce_column"])
    source_column = str(spec["column"])
    if announce_column not in snapshot.columns or source_column not in snapshot.columns:
        raise ValueError(f"financial snapshot lacks {factor_id} source columns")

    right = _latest_effective_snapshot(
        snapshot,
        announce_column,
        [source_column],
    ).select(["symbol", announce_column, source_column, "_effective"])
    joined = panel.join_asof(
        right,
        left_on="date",
        right_on="_effective",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    announced = pl.col(announce_column).is_not_null() & (pl.col("date") > pl.col(announce_column))
    if spec["price_ratio"]:
        value = pl.when(pl.col(source_column) > 0).then(
            pl.col("close") / pl.col(source_column)
        ).otherwise(None)
    else:
        value = pl.col(source_column)
    return joined.with_columns(
        pl.when(announced).then(value).otherwise(None).alias("_expected")
    ).select(["symbol", "date", "_expected"])


def _fallback_count(
    snapshot: pl.DataFrame,
    factor_id: str,
) -> int:
    spec = FUNDAMENTAL_FACTORS[factor_id]
    announce_column = str(spec["announce_column"])
    if announce_column not in snapshot.columns or "announce_date" not in snapshot.columns:
        return 0
    count = 0
    for row in snapshot.select(["period_end", "announce_date", announce_column]).iter_rows(named=True):
        period_end = _as_date(row["period_end"])
        raw_announce = _as_date(row["announce_date"])
        effective = _as_date(row[announce_column])
        if period_end is None or effective is None:
            continue
        if raw_announce is None or financial_pit_date(period_end, raw_announce) != raw_announce:
            count += 1
    return count


def _audit_builtin_factor(
    panel: pl.DataFrame,
    snapshot: pl.DataFrame,
    attached: pl.DataFrame,
    factor_id: str,
) -> dict[str, Any]:
    expected = _expected_factor_panel(panel, snapshot, factor_id)
    actual = attached.select(["symbol", "date", factor_id])
    joined = actual.join(expected, on=["symbol", "date"], how="left")
    both = pl.col(factor_id).is_not_null() & pl.col("_expected").is_not_null()
    mismatch = (
        (pl.col(factor_id).is_not_null() & pl.col("_expected").is_null())
        | (pl.col(factor_id).is_null() & pl.col("_expected").is_not_null())
        | (
            both
            & ((pl.col(factor_id) - pl.col("_expected")).abs() > 1e-7)
        )
    )
    bad = joined.filter(mismatch)
    non_null = joined.filter(pl.col(factor_id).is_not_null())
    first_value = non_null["date"].min() if not non_null.is_empty() else None
    symbol_count = non_null["symbol"].n_unique() if not non_null.is_empty() else 0
    mismatch_count = bad.height
    return {
        "factor_id": factor_id,
        "status": "verified" if mismatch_count == 0 and non_null.height > 0 else "failed",
        "verified": mismatch_count == 0 and non_null.height > 0,
        "mismatch_count": mismatch_count,
        "non_null_rows": non_null.height,
        "symbol_count": int(symbol_count),
        "first_value_date": str(first_value) if first_value is not None else None,
        "announcement_fallback_rows": _fallback_count(snapshot, factor_id),
    }


def _load_dividend_panel(
    data_dir: Path,
    *,
    symbols: Sequence[str],
    start: date,
    end: date,
) -> pl.DataFrame:
    paths = sorted(
        (data_dir / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet")
    )
    if not paths:
        raise FileNotFoundError("alt_hs300 extension timeseries is missing")
    fields = [
        "dividend_growth_ttm",
        "dividend_ttm_cash",
        "dividend_yield",
        "dividend_initiation_flag",
    ]
    panel = (
        pl.scan_parquet([str(path) for path in paths], include_file_paths="_source_path")
        .select(["symbol", *fields, "_source_path"])
        .with_columns(
            pl.col("_source_path")
            .str.extract(r"date=(\d{4}-\d{2}-\d{2})")
            .str.to_date(strict=False)
            .alias("date")
        )
        .filter(
            pl.col("symbol").is_in(list(symbols))
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .collect()
        .sort(["symbol", "date"])
    )
    if panel.is_empty():
        raise ValueError("dividend PIT audit has no extension rows in the requested window")
    return panel


def _dividend_events(data_dir: Path) -> pl.DataFrame:
    paths = sorted((data_dir / "research" / "r89_dividend_growth").glob("*.parquet"))
    if not paths:
        raise FileNotFoundError("dividend event cache is missing")
    events = pl.concat(
        [pl.read_parquet(path, columns=["symbol", "announce_date"]) for path in paths],
        how="diagonal_relaxed",
    )
    return (
        events.filter(pl.col("announce_date").is_not_null())
        .unique(subset=["symbol", "announce_date"])
        .sort(["symbol", "announce_date"])
    )


def _audit_dividend_factor(
    panel: pl.DataFrame,
    events: pl.DataFrame,
    factor_id: str,
) -> dict[str, Any]:
    field = factor_id.removeprefix("ext_alt_hs300_")
    non_null = panel.filter(pl.col(field).is_not_null()).select(["symbol", "date", field])
    if non_null.is_empty():
        return {
            "factor_id": factor_id,
            "status": "failed",
            "verified": False,
            "mismatch_count": 0,
            "non_null_rows": 0,
            "symbol_count": 0,
            "first_value_date": None,
            "no_eligible_event_rows": 0,
        }
    joined = non_null.sort(["symbol", "date"]).join_asof(
        events,
        left_on="date",
        right_on="announce_date",
        by="symbol",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    age = (pl.col("date") - pl.col("announce_date")).dt.total_days()
    invalid = (
        pl.col("announce_date").is_null()
        | (age < 0)
        | (age >= 730)
    )
    invalid_rows = joined.filter(invalid)
    first_value = non_null["date"].min()
    return {
        "factor_id": factor_id,
        "status": "verified" if invalid_rows.is_empty() else "failed",
        "verified": invalid_rows.is_empty(),
        "mismatch_count": 0,
        "non_null_rows": non_null.height,
        "symbol_count": int(non_null["symbol"].n_unique()),
        "first_value_date": str(first_value),
        "no_eligible_event_rows": invalid_rows.height,
    }


def _input_fingerprints(data_dir: Path) -> dict[str, str]:
    paths = [
        data_dir / "financials" / table / "part.parquet"
        for table in ("metrics", "income", "balance_sheet", "cash_flow")
    ]
    return {
        path.relative_to(data_dir).as_posix(): sha256_file(path)
        for path in paths
        if path.exists()
    }


def audit_financial_pit(
    data_dir: Path,
    *,
    factor_ids: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    start: date = date(2021, 6, 1),
    end: date = date(2100, 12, 31),
    include_dividend: bool = True,
) -> dict[str, Any]:
    """Audit financial announcement gating and return factor-level evidence."""
    data_dir = Path(data_dir)
    snapshot = load_fundamental_snapshot(data_dir)
    if snapshot is None or snapshot.is_empty():
        raise FileNotFoundError("financial snapshot is empty or unavailable")
    requested = tuple(
        dict.fromkeys(
            factor_ids if factor_ids is not None else BUILTIN_FINANCIAL_FACTOR_IDS
        )
    )
    unknown = sorted(set(requested) - set(FUNDAMENTAL_FACTORS))
    if unknown:
        raise ValueError(f"unsupported financial factors: {', '.join(unknown)}")
    selected_symbols = sorted(set(symbols or _member_symbols(data_dir, snapshot)))
    if not selected_symbols:
        raise ValueError("financial PIT audit has no symbols")
    panel = _load_daily_panel(
        data_dir,
        symbols=selected_symbols,
        start=start,
        end=end,
    )
    attached = attach_fundamental_factors(panel, snapshot, requested)
    factors = {
        factor_id: _audit_builtin_factor(panel, snapshot, attached, factor_id)
        for factor_id in requested
    }
    if include_dividend:
        dividend_panel = _load_dividend_panel(
            data_dir,
            symbols=selected_symbols,
            start=start,
            end=end,
        )
        events = _dividend_events(data_dir)
        factors.update(
            {
                factor_id: _audit_dividend_factor(dividend_panel, events, factor_id)
                for factor_id in DIVIDEND_FINANCIAL_FACTOR_IDS
            }
        )
    verified = sorted(
        factor_id for factor_id, result in factors.items() if result["verified"]
    )
    return {
        "schema_version": 1,
        "method_version": "financial_pit_audit_v1",
        "status": "verified" if len(verified) == len(factors) else "partial",
        "period": {"start": str(start), "end": str(end)},
        "universe": {
            "name": "current_hs300_financial_sample",
            "symbol_count": len(selected_symbols),
            "survivorship_bias": True,
        },
        "inputs": _input_fingerprints(data_dir),
        "factors": factors,
        "verified_factor_ids": verified,
    }


def promote_financial_taxonomy(
    records: list[dict[str, Any]],
    audit: Mapping[str, Any],
    evidence_ref: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Promote only factor ids explicitly present in a completed PIT audit."""
    verified = set(str(item) for item in audit.get("verified_factor_ids", []))
    promoted: list[dict[str, Any]] = []
    for raw_record in records:
        record = dict(raw_record)
        if record.get("factor_id") in verified:
            record["pit_hint"] = "verified"
            record["future_data_status"] = "clear"
            refs = [dict(item) for item in record.get("evidence_refs", [])]
            if not any(
                item.get("path") == evidence_ref.get("path")
                and item.get("sha256") == evidence_ref.get("sha256")
                for item in refs
            ):
                refs.append(dict(evidence_ref))
            record["evidence_refs"] = refs
        promoted.append(record)
    return promoted
