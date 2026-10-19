#!/usr/bin/env python3
"""Backfill historical HS300 member daily bars through the canonical pipeline."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "backend"))

from app.indicators.pipeline import run_pipeline  # noqa: E402
from app.services import kline_sync  # noqa: E402
from app.tickflow.capabilities import CapabilitySet  # noqa: E402
from app.tickflow.repository import DataStore, KlineRepository  # noqa: E402

OUT = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r174_history_backfill.json"
START = datetime(2018, 1, 1)
END = datetime(2026, 10, 19)
TARGET_ROWS = 2000
BATCH = 50


def _historical_members() -> pl.DataFrame:
    monthly = (
        pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
        .select(["query_date", "symbol"])
        .with_columns(pl.col("query_date").cast(pl.Date))
    )
    daily = (
        pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet")
        .select(["query_date", "symbol"])
        .with_columns(pl.col("query_date").cast(pl.Date))
    )
    return (
        pl.concat([monthly, daily])
        .group_by("symbol")
        .agg(
            pl.col("query_date").min().alias("first_member"),
            pl.col("query_date").max().alias("last_member"),
        )
        .sort("symbol")
    )


def _coverage() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "rows": pl.UInt32, "first_bar": pl.Date, "last_bar": pl.Date})
    return (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date"])
        .group_by("symbol")
        .agg(
            pl.len().alias("rows"),
            pl.col("date").min().alias("first_bar"),
            pl.col("date").max().alias("last_bar"),
        )
        .collect()
    )


def _monthly_member_coverage() -> list[dict]:
    bars = _coverage().select(["symbol", "first_bar", "last_bar"])
    monthly = (
        pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
        .select(["query_date", "symbol"])
        .with_columns(pl.col("query_date").cast(pl.Date))
        .unique()
        .filter((pl.col("query_date") >= pl.date(2018, 1, 1)) & (pl.col("query_date") <= pl.date(2020, 12, 31)))
    )
    joined = monthly.with_columns(pl.col("query_date").dt.month_end().alias("_month_end")).join(bars, on="symbol", how="left").filter(
        pl.col("first_bar").is_not_null()
        & (pl.col("first_bar") <= pl.col("_month_end"))
        & (pl.col("last_bar") >= pl.col("query_date"))
    )
    return (
        monthly.group_by("query_date").agg(pl.len().alias("members"))
        .join(joined.group_by("query_date").agg(pl.col("symbol").n_unique().alias("with_bar")), on="query_date", how="left")
        .with_columns(pl.col("with_bar").fill_null(0))
        .with_columns((pl.col("with_bar") / pl.col("members")).round(4).alias("coverage"))
        .sort("query_date")
        .with_columns(pl.col("query_date").cast(pl.Utf8))
        .to_dicts()
    )


def _short_rows(frame: pl.DataFrame) -> list[dict]:
    return [
        {key: (str(value) if hasattr(value, "isoformat") else value) for key, value in row.items()}
        for row in frame.filter(pl.col("rows") < TARGET_ROWS).sort("rows").to_dicts()
    ]


def main() -> int:
    audit_only = "--audit-only" in sys.argv
    members = _historical_members()
    state = members.join(_coverage(), on="symbol", how="left").with_columns(pl.col("rows").fill_null(0))
    selected = [] if audit_only else state.filter((pl.col("rows") < TARGET_ROWS) | (pl.col("first_bar") > pl.date(2018, 1, 2)))["symbol"].to_list()
    repo = KlineRepository(DataStore(DATA))
    capset = CapabilitySet()
    daily_written = 0
    adj_written = 0
    zero_rows: list[str] = []
    errors: list[dict] = []
    successful: list[str] = []
    if not audit_only:
        for index in range(0, len(selected), BATCH):
            batch = selected[index:index + BATCH]
            try:
                zero: list[str] = []
                daily_written += kline_sync.sync_and_persist_daily_batch(
                    batch,
                    repo,
                    capset,
                    start_date=START,
                    end_date=END,
                    provider_name="akshare",
                    zero_row_out=zero,
                )
                zero_rows.extend(zero)
                try:
                    added, _affected = kline_sync.sync_adj_factor(
                        batch, repo, capset, start_time=START, end_time=END
                    )
                    adj_written += added
                except Exception as exc:  # noqa: BLE001
                    errors.append({"stage": "adj_factor", "batch_start": index, "error": f"{type(exc).__name__}: {exc}"})
                successful.extend(batch)
            except Exception as exc:  # noqa: BLE001
                errors.append({"stage": "daily", "batch_start": index, "error": f"{type(exc).__name__}: {exc}"})
            print(f"backfill {min(index + BATCH, len(selected))}/{len(selected)} daily_rows={daily_written} zero={len(zero_rows)}", flush=True)
        if successful:
            try:
                enriched_written = run_pipeline(data_dir=DATA, symbols=successful)
            except Exception as exc:  # noqa: BLE001
                enriched_written = 0
                errors.append({"stage": "enriched", "error": f"{type(exc).__name__}: {exc}"})
        else:
            enriched_written = 0
    else:
        enriched_written = 0
    after = members.join(_coverage(), on="symbol", how="left").with_columns(pl.col("rows").fill_null(0))
    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "audit_only": audit_only,
        "window": [str(START.date()), str(END.date())],
        "historical_members": members.height,
        "selected": len(selected),
        "successful_batches_symbols": len(successful),
        "daily_rows_written": daily_written,
        "adj_rows_written": adj_written,
        "enriched_rows_written": enriched_written,
        "zero_row_symbols": sorted(set(zero_rows)),
        "errors": errors,
        "before_backfill": {"rows_ge_2000": 239, "rows_lt_1000": 273},
        "state_at_audit": {
            "rows_ge_2000": after.filter(pl.col("rows") >= TARGET_ROWS).height,
            "rows_lt_1000": after.filter(pl.col("rows") < 1000).height,
            "rows_zero": after.filter(pl.col("rows") == 0).height,
        },
        "monthly_member_coverage": _monthly_member_coverage(),
        "remaining_short": _short_rows(after),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ["historical_members", "selected", "daily_rows_written", "adj_rows_written", "enriched_rows_written", "before_backfill", "state_at_audit", "zero_row_symbols", "errors"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())