from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.fundamentals import financial_pit_date  # noqa: E402
from app.services.industry_map import load_industry_l1  # noqa: E402

PRE_PATH = EVIDENCE / "r301_preregistration.json"
AMENDMENT_PATH = EVIDENCE / "r301_preregistration_amendment.json"
COMPARATOR_AMENDMENT_PATH = EVIDENCE / "r301_preregistration_amendment_A2.json"
MANIFEST_PATH = EVIDENCE / "r301_input_manifest.jsonl"
MANIFEST_AMENDMENT_PATHS = [
    EVIDENCE / "r301_manifest_amendment.json",
    EVIDENCE / "r301_manifest_amendment_A2.json",
    EVIDENCE / "r301_manifest_amendment_A3.json",
    EVIDENCE / "r301_manifest_amendment_A4.json",
    EVIDENCE / "r301_manifest_amendment_A5.json",
    EVIDENCE / "r301_manifest_amendment_A6.json",
]
DEVIATION_PATH = EVIDENCE / "r301_preanalysis_deviation.json"
PANEL_PATH = EVIDENCE / "r301_panel_readback.json"
OUTPUT_PATH = EVIDENCE / "r301_feasibility_result.json"
START = date(2022, 7, 1)
DISCOVERY_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 2)
END = date(2026, 10, 14)
MAX_AGE_DAYS = 180
MIN_PAIRED = 30
CORRELATION_LIMIT = 0.70

COMPARATOR_CONFIGS = {
    "working_capital_change_yoy_low": "alt_hs300_wc",
    "asset_growth_yoy_low": "alt_hs300_ag",
}
BASE_COMPARATORS = {
    "ocf_margin_4q",
    "cash_to_assets",
    "sga_ratio_4q_low",
    "gross_margin_yoy_improvement_q",
}
ALL_COMPARATORS = (
    "receivable_growth_yoy_low",
    "working_capital_change_yoy_low",
    "asset_growth_yoy_low",
    "ocf_margin_4q",
    "cash_to_assets",
    "sga_ratio_4q_low",
    "gross_margin_yoy_improvement_q",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_frozen_inputs() -> tuple[dict, list[dict], dict]:
    def checked_bytes(path: Path) -> tuple[bytes, str]:
        raw = path.read_bytes()
        digest = _sha256_bytes(raw)
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != digest:
            raise RuntimeError(f"R301 checksum mismatch: {path.name}")
        return raw, digest

    prereg_bytes, prereg_hash = checked_bytes(PRE_PATH)
    amendment_bytes, amendment_hash = checked_bytes(AMENDMENT_PATH)
    comparator_bytes, comparator_hash = checked_bytes(COMPARATOR_AMENDMENT_PATH)
    prereg = json.loads(prereg_bytes)
    amendment = json.loads(amendment_bytes)
    comparator_amendment = json.loads(comparator_bytes)
    if amendment.get("parent_preregistration_sha256") != prereg_hash:
        raise RuntimeError("R301 A1 parent preregistration hash mismatch")
    if comparator_amendment.get("parent_preregistration_sha256") != prereg_hash:
        raise RuntimeError("R301 A2 parent preregistration hash mismatch")
    if comparator_amendment.get("parent_amendment_sha256") != amendment_hash:
        raise RuntimeError("R301 A2 parent A1 hash mismatch")

    amendment_docs = []
    for path in MANIFEST_AMENDMENT_PATHS:
        raw, digest = checked_bytes(path)
        amendment_docs.append((json.loads(raw), digest))
    m1, m1_hash = amendment_docs[0]
    m2, m2_hash = amendment_docs[1]
    m3, m3_hash = amendment_docs[2]
    m4, m4_hash = amendment_docs[3]
    m5, m5_hash = amendment_docs[4]
    m6, m6_hash = amendment_docs[5]
    if m1.get("parent_manifest_sha256") != m2.get("parent_manifest_sha256"):
        raise RuntimeError("R301 manifest amendments do not share the frozen parent manifest")
    if m2.get("parent_manifest_amendment_sha256") != m1_hash:
        raise RuntimeError("R301 M2 parent M1 hash mismatch")
    if m3.get("parent_manifest_amendment_M1_sha256") != m1_hash or m3.get("parent_manifest_amendment_M2_sha256") != m2_hash:
        raise RuntimeError("R301 M3 does not bind M1 and M2")
    if m3.get("comparator_preregistration_amendment_A2_sha256") != comparator_hash:
        raise RuntimeError("R301 M3 A2 hash mismatch")
    stale = m3.get("superseded_parent_hash_reference", {})
    if stale.get("recorded_value") != m2.get("parent_comparator_amendment_sha256") or stale.get("correct_value") != comparator_hash:
        raise RuntimeError("R301 M3 does not accurately document the superseded M2 hash")
    if m4.get("parent_manifest_amendment_M3_current_sha256") != m3_hash:
        raise RuntimeError("R301 M4 current M3 hash mismatch")
    if m4.get("parent_manifest_amendment_M1_sha256") != m1_hash or m4.get("parent_manifest_amendment_M2_sha256") != m2_hash:
        raise RuntimeError("R301 M4 parent amendment hash mismatch")
    if m5.get("parent_manifest_amendment_M4_sha256") != m4_hash:
        raise RuntimeError("R301 M5 parent M4 hash mismatch")
    if m6.get("parent_manifest_amendment_M5_sha256") != m5_hash:
        raise RuntimeError("R301 M6 parent M5 hash mismatch")

    deviation_bytes, deviation_hash = checked_bytes(DEVIATION_PATH)
    deviation = json.loads(deviation_bytes)
    if deviation.get("parent_preregistration_sha256") != prereg_hash or deviation.get("parent_amendment_A1_sha256") != amendment_hash:
        raise RuntimeError("R301 pre-analysis deviation is not bound to the frozen preregistration")
    if deviation.get("parent_amendment_A2_sha256") != comparator_hash:
        raise RuntimeError("R301 pre-analysis deviation has the wrong A2 hash")
    if any(deviation.get("analysis_state_at_freeze", {}).values()):
        raise RuntimeError("R301 deviation record does not establish a pre-analysis freeze")

    manifest_bytes, manifest_hash = checked_bytes(MANIFEST_PATH)
    raw_lines = manifest_bytes.splitlines(keepends=True)
    if not raw_lines:
        raise RuntimeError("R301 input manifest is empty")
    header = json.loads(raw_lines[0])
    parent_count = header.get("files")
    if not isinstance(parent_count, int) or parent_count < 1:
        raise RuntimeError("R301 original manifest file count is invalid")
    parent_line_count = parent_count + 1
    if len(raw_lines) > parent_line_count:
        parent_manifest_amendment = EVIDENCE / "r301_manifest_amendment.json"
        recorded_parent = json.loads(parent_manifest_amendment.read_text(encoding="utf-8")).get("parent_manifest_sha256")
        if recorded_parent is not None and _sha256_bytes(b"".join(raw_lines[:parent_line_count])) != recorded_parent:
            raise RuntimeError("R301 original manifest bytes differ from the pre-existing M1 parent hash")
    parent_bytes = b"".join(raw_lines[:parent_line_count])
    parent_hash = _sha256_bytes(parent_bytes)
    if parent_hash != m3.get("parent_input_manifest_sha256") or parent_hash != m1.get("parent_manifest_sha256"):
        raise RuntimeError("R301 original input manifest hash differs from the frozen amendment parent")
    if header["preregistration_sha256"] != prereg_hash or header["amendment_sha256"] != amendment_hash:
        raise RuntimeError("R301 original manifest is not bound to the frozen parent registration")
    entries = [json.loads(line) for line in raw_lines[1:parent_line_count]]
    if len(entries) != parent_count or header["files"] != len(entries):
        raise RuntimeError("R301 original manifest file count mismatch")
    if len(raw_lines) != parent_line_count + 4:
        raise RuntimeError("R301 manifest must contain the original freeze and three append-only repair records")
    m1_path, m2_path, m3_path, m4_path, m5_path, _m6_path = MANIFEST_AMENDMENT_PATHS
    first_record_bytes = raw_lines[parent_line_count]
    first_record = json.loads(first_record_bytes)
    if _sha256_bytes(first_record_bytes) != m4.get("parent_manifest_addendum_record_sha256"):
        raise RuntimeError("R301 M4 does not bind the historical first append record")
    recorded_m3_hash = m4.get("parent_manifest_amendment_M3_actual_at_initial_append_sha256")
    expected_first_amendments = {m1_path.name: m1_hash, m2_path.name: m2_hash, m3_path.name: recorded_m3_hash}
    if first_record.get("manifest_amendments") != expected_first_amendments:
        raise RuntimeError("R301 historical addendum does not match the disclosed original M3 hash")
    if first_record.get("deviation_sha256") != deviation_hash:
        raise RuntimeError("R301 historical addendum does not bind the exposure disclosure")
    before_repair_hash = _sha256_bytes(b"".join(raw_lines[:parent_line_count + 1]))
    if before_repair_hash != m4.get("parent_input_manifest_sha256"):
        raise RuntimeError("R301 M4 parent manifest hash mismatch")
    m4_record_bytes = raw_lines[parent_line_count + 1]
    m4_record = json.loads(m4_record_bytes)
    if m4_record.get("manifest_amendment_M4_sha256") != m4_hash or m4_record.get("parent_manifest_sha256") != before_repair_hash:
        raise RuntimeError("R301 M4 repair append is not bound to its parent")
    repair_bytes = raw_lines[parent_line_count + 2]
    repair = json.loads(repair_bytes)
    if repair.get("manifest_amendment_M5_sha256") != m5_hash or repair.get("parent_manifest_sha256") != _sha256_bytes(b"".join(raw_lines[:parent_line_count + 2])):
        raise RuntimeError("R301 M5 repair append is not bound to its parent")
    m6_record_bytes = raw_lines[parent_line_count + 3]
    m6_record = json.loads(m6_record_bytes)
    if (
        m6_record.get("manifest_amendment_M6_sha256") != m6_hash
        or m6_record.get("parent_manifest_sha256")
        != _sha256_bytes(b"".join(raw_lines[:parent_line_count + 3]))
    ):
        raise RuntimeError("R301 M6 repair append is not bound to its parent")
    if not m4.get("analysis_has_not_started"):
        raise RuntimeError("R301 M4 does not establish a pre-analysis freeze")
    if repair.get("supersedes") != m5.get("supersedes"):
        raise RuntimeError("R301 repair supersession list differs from M5")

    first_files = first_record.get("files", [])
    repair_files = m4_record.get("files", [])
    latest_files = repair.get("files", [])
    m6_files = m6_record.get("files", [])
    if set(item["path"] for item in repair_files) != set(m4.get("additional_inputs", [])):
        raise RuntimeError("R301 M4 appended inputs do not match its amendment")
    if {item["path"]: item["sha256"] for item in repair_files} != m4.get("input_sha256"):
        raise RuntimeError("R301 M4 input hashes differ from its appended records")
    if set(item["path"] for item in repair_files) != set(m4.get("additional_inputs", [])):
        raise RuntimeError("R301 M4 input records do not match M4 input list")
    repair_by_path = {item["path"]: item for item in repair_files}
    if {path: repair_by_path[path]["sha256"] for path in m4.get("input_sha256", {})} != m4["input_sha256"]:
        raise RuntimeError("R301 M4 input hash map differs from appended M4 records")
    latest_by_path = {item["path"]: item for item in [*entries, *first_files, *repair_files]}
    latest_by_path.update({item["path"]: item for item in latest_files})
    m5_replacements = {item["path"]: item for item in m5.get("supersedes", [])}
    if {item["path"] for item in latest_files} != set(m5.get("additional_inputs", [])):
        raise RuntimeError("R301 M5 latest input list mismatch")
    m5_hash_map = {item["path"]: item["sha256"] for item in latest_files}
    if m5_hash_map != m5.get("input_sha256"):
        raise RuntimeError("R301 M5 input hash map differs from appended repair records")
    for item in m5_replacements.values():
        if item["path"] not in repair_by_path or repair_by_path[item["path"]]["sha256"] != item["previous_sha256"]:
            raise RuntimeError(f"R301 M5 previous hash mismatch: {item['path']}")
        if item["path"] not in {f["path"] for f in latest_files} or latest_by_path[item["path"]]["sha256"] != item["replacement_sha256"]:
            raise RuntimeError(f"R301 M5 replacement hash mismatch: {item['path']}")
    m6_path = MANIFEST_AMENDMENT_PATHS[-1]
    m6_document_paths = {
        str(m6_path.relative_to(ROOT)).replace("\\", "/"),
        str(m6_path.with_name(m6_path.name + ".sha256").relative_to(ROOT)).replace("\\", "/"),
    }
    if {item["path"] for item in m6_files} != set(m6.get("additional_inputs", [])) | m6_document_paths:
        raise RuntimeError("R301 M6 input list mismatch")
    m6_hash_map = {item["path"]: item["sha256"] for item in m6_files}
    m6_documents = {
        str(m6_path.relative_to(ROOT)).replace("\\", "/"): m6_hash,
        str(m6_path.with_name(m6_path.name + ".sha256").relative_to(ROOT)).replace("\\", "/"): _sha256_file(
            m6_path.with_name(m6_path.name + ".sha256")
        ),
    }
    if m6_hash_map != {**m6.get("input_sha256", {}), **m6_documents}:
        raise RuntimeError("R301 M6 input hash map differs from its amendment and appended documents")
    m6_replacements = {item["path"]: item for item in m6.get("supersedes", [])}
    for item in m6_replacements.values():
        previous = latest_by_path.get(item["path"])
        replacement = m6_hash_map.get(item["path"])
        if previous is None or previous["sha256"] != item["previous_sha256"]:
            raise RuntimeError(f"R301 M6 previous hash mismatch: {item['path']}")
        if replacement != item["replacement_sha256"]:
            raise RuntimeError(f"R301 M6 replacement hash mismatch: {item['path']}")
    latest_by_path.update({item["path"]: item for item in m6_files})
    for item in latest_by_path.values():
        path = ROOT / Path(item["path"])
        if not path.is_file() or path.stat().st_size != item["size_bytes"] or _sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"Frozen input changed: {item['path']}")

    panel_bytes, panel_hash = checked_bytes(PANEL_PATH)
    panel = json.loads(panel_bytes)
    if not panel.get("registration_pass") or len(panel.get("r301_matches", [])) != 1:
        raise RuntimeError("R301 panel registration/readback proof failed")
    match = panel["r301_matches"][0]
    if match.get("status") != "pending" or match.get("metrics") != {}:
        raise RuntimeError("R301 panel item is not an empty-metric pending research record")
    if panel.get("candidate_count_before") != 16 or panel.get("candidate_count_after") != 17 or not panel.get("prior_candidate_statuses_unchanged"):
        raise RuntimeError("R301 panel readback does not prove a single unchanged pending registration")
    return prereg, list(latest_by_path.values()), {
        "preregistration_sha256": prereg_hash,
        "amendment_sha256": amendment_hash,
        "comparator_amendment_A2_sha256": comparator_hash,
        "manifest_amendment_M1_sha256": m1_hash,
        "manifest_amendment_M2_sha256": m2_hash,
        "manifest_amendment_M3_sha256": m3_hash,
        "manifest_amendment_M4_sha256": m4_hash,
        "manifest_amendment_M5_sha256": m5_hash,
        "manifest_amendment_M6_sha256": m6_hash,
        "preanalysis_deviation_sha256": deviation_hash,
        "original_input_manifest_sha256": parent_hash,
        "input_manifest_sha256": manifest_hash,
        "panel_readback_sha256": panel_hash,
        "panel_candidate_id": match["id"],
    }


def _parse_financial(path: Path, value_column: str, pit_column: str) -> pl.DataFrame:
    frame = pl.read_parquet(path, columns=["symbol", "period_end", "announce_date", value_column])
    frame = frame.with_columns([
        pl.col("period_end").cast(pl.String).str.slice(0, 10).str.to_date(strict=False),
        pl.col("announce_date").cast(pl.String).str.slice(0, 10).str.to_date(strict=False),
        pl.col(value_column).cast(pl.Float64, strict=False),
    ])
    if frame.filter(pl.col("period_end").is_null()).height:
        raise RuntimeError(f"Invalid period_end values in {path}")
    duplicates = frame.group_by(["symbol", "period_end"]).len().filter(pl.col("len") != 1)
    if duplicates.height:
        raise RuntimeError(f"Duplicate (symbol, period_end) source keys in {path}: {duplicates.height}")
    frame = frame.with_columns(
        (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
        pl.struct(["period_end", "announce_date"]).map_elements(
            lambda row: financial_pit_date(row["period_end"], row["announce_date"]),
            return_dtype=pl.Date,
        ).alias(pit_column),
    )
    return frame


def _financial_period_signals() -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    income_path = ROOT / "data/financials/income/part.parquet"
    balance_path = ROOT / "data/financials/balance_sheet/part.parquet"
    income = _parse_financial(income_path, "revenue", "_income_pit")
    balance = _parse_financial(balance_path, "accounts_receivable", "_balance_pit")
    if income.columns.count("revenue") != 1 or balance.columns.count("accounts_receivable") != 1:
        raise RuntimeError("Selected financial source columns are not unique")

    income_current = income.select([
        "symbol", "_period_index", "period_end", "revenue", "_income_pit",
    ]).rename({
        "period_end": "_income_period_end",
        "revenue": "_revenue_current",
        "_income_pit": "_income_current_pit",
    })
    income_lag = income.select([
        "symbol", (pl.col("_period_index") + 4).alias("_period_index"),
        "revenue", "_income_pit",
    ]).rename({"revenue": "_revenue_lag", "_income_pit": "_income_lag_pit"})
    balance_current = balance.select([
        "symbol", "_period_index", "period_end", "accounts_receivable", "_balance_pit",
    ]).rename({
        "period_end": "_balance_period_end",
        "accounts_receivable": "_receivables_current",
        "_balance_pit": "_balance_current_pit",
    })
    balance_lag = balance.select([
        "symbol", (pl.col("_period_index") + 4).alias("_period_index"),
        "accounts_receivable", "_balance_pit",
    ]).rename({"accounts_receivable": "_receivables_lag", "_balance_pit": "_balance_lag_pit"})

    current_periods = income_current.join(
        balance_current,
        on=["symbol", "_period_index"],
        how="full",
        coalesce=True,
    )
    periods = (
        current_periods
        .join(income_lag, on=["symbol", "_period_index"], how="left")
        .join(balance_lag, on=["symbol", "_period_index"], how="left")
        .with_columns([
            pl.coalesce(["_income_period_end", "_balance_period_end"]).alias("period_end"),
            (
                pl.col("_income_period_end").is_not_null()
                & pl.col("_balance_period_end").is_not_null()
                & (pl.col("_income_period_end") == pl.col("_balance_period_end"))
            ).alias("_current_periods_aligned"),
            pl.max_horizontal(["_income_current_pit", "_balance_current_pit"]).alias("_current_report_pit"),
            pl.max_horizontal([
                "_income_current_pit", "_income_lag_pit",
                "_balance_current_pit", "_balance_lag_pit",
            ]).alias("_latest_dependency_pit"),
            pl.col("_period_index").alias("_quarter_index"),
        ])
        .with_columns([
            # New report rows remain present as null barriers when an exact lag or value is missing.
            pl.coalesce(["_latest_dependency_pit", "_current_report_pit"])
            .dt.offset_by("1d").alias("_candidate_effective"),
            pl.coalesce(["_latest_dependency_pit", "_current_report_pit"])
            .dt.offset_by("1d").alias("_receivable_effective"),
            pl.when(
                pl.col("_receivables_lag").is_not_null()
                & pl.col("_receivables_lag").is_finite()
                & pl.col("_receivables_current").is_not_null()
                & pl.col("_receivables_current").is_finite()
                & (pl.col("_receivables_lag") > 0)
                & (pl.col("_receivables_current") >= 0)
            )
            .then(pl.col("_receivables_current") / pl.col("_receivables_lag") - 1.0)
            .otherwise(None)
            .alias("_receivable_growth_raw"),
            pl.when(
                pl.col("_revenue_lag").is_not_null()
                & pl.col("_revenue_lag").is_finite()
                & pl.col("_revenue_current").is_not_null()
                & pl.col("_revenue_current").is_finite()
                & (pl.col("_revenue_lag") > 0)
                & (pl.col("_revenue_current") > 0)
            )
            .then(pl.col("_revenue_current") / pl.col("_revenue_lag") - 1.0)
            .otherwise(None)
            .alias("_revenue_growth_raw"),
        ])
        .with_columns(
            pl.when(
                pl.col("_revenue_lag").is_not_null()
                & pl.col("_revenue_current").is_not_null()
                & pl.col("_receivables_lag").is_not_null()
                & pl.col("_receivables_current").is_not_null()
                & pl.col("_revenue_lag").is_finite()
                & pl.col("_revenue_current").is_finite()
                & pl.col("_receivables_lag").is_finite()
                & pl.col("_receivables_current").is_finite()
                & (pl.col("_revenue_lag") > 0)
                & (pl.col("_revenue_current") > 0)
                & (pl.col("_receivables_lag") > 0)
                & (pl.col("_receivables_current") >= 0)
                & pl.col("_current_periods_aligned")
                & pl.col("_latest_dependency_pit").is_not_null()
            )
            .then(
                (pl.col("_revenue_current") / pl.col("_revenue_lag") - 1.0)
                - (pl.col("_receivables_current") / pl.col("_receivables_lag") - 1.0)
            )
            .otherwise(None)
            .alias("collection_quality_spread")
        )
        .filter(pl.col("_current_report_pit").is_not_null())
        .sort(["symbol", "_quarter_index"])
    )
    if len(periods.columns) != len(set(periods.columns)):
        raise RuntimeError("Duplicate output columns after explicit source projection")

    candidate = periods.select([
        "symbol", "period_end", "_candidate_effective", "collection_quality_spread",
    ]).sort(["symbol", "_candidate_effective", "period_end"]).unique(
        subset=["symbol", "_candidate_effective"], keep="last",
    ).sort(["symbol", "_candidate_effective"])
    receivable = periods.select([
        "symbol", "period_end", "_receivable_effective", "_receivable_growth_raw",
    ]).sort(["symbol", "_receivable_effective", "period_end"]).unique(
        subset=["symbol", "_receivable_effective"], keep="last",
    ).sort(["symbol", "_receivable_effective"])
    audit = {
        "income_source_rows": income.height,
        "balance_sheet_source_rows": balance.height,
        "income_duplicate_keys": 0,
        "balance_sheet_duplicate_keys": 0,
        "exact_four_quarter_join_rows": periods.height,
        "candidate_valid_period_rows": candidate.height,
        "receivable_comparator_valid_period_rows": receivable.height,
        "candidate_effective_date_max_of_four_pit_dependencies_plus_one_day": True,
        "receivable_comparator_uses_its_own_two_balance_sheet_pit_dates": True,
    }
    return candidate, receivable, audit


def _membership_universe() -> tuple[pl.DataFrame, list[date], dict]:
    path = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    members = (
        pl.read_parquet(path)
        .filter(pl.col("query_date").is_between(START, END, closed="both"))
        .select([pl.col("query_date").alias("date"), "symbol"])
        .unique(subset=["date", "symbol"])
    )
    industry = load_industry_l1(ROOT / "data")
    all_rows = members.join(industry, on="symbol", how="left")
    first_industry = pl.col("industry_l1").str.split("-").list.first()
    eligible = all_rows.filter(
        pl.col("industry_l1").is_not_null()
        & ~first_industry.is_in(["\u94f6\u884c", "\u975e\u94f6\u91d1\u878d"])
    ).select(["date", "symbol"]).sort(["symbol", "date"])
    dates = sorted(members["date"].unique().to_list())
    audit = {
        "source": "R78 exact daily snapshots",
        "all_member_rows": all_rows.height,
        "eligible_nonfinancial_member_rows": eligible.height,
        "excluded_financial_member_rows": all_rows.filter(first_industry.is_in(["\u94f6\u884c", "\u975e\u94f6\u91d1\u878d"])).height,
        "missing_industry_member_rows": all_rows.filter(pl.col("industry_l1").is_null()).height,
        "industry_source": "current ext_hy_ths first-level industry; not PIT",
        "trading_dates": len(dates),
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
    }
    return eligible, dates, audit


def _attach_asof(
    members: pl.DataFrame,
    values: pl.DataFrame,
    *,
    effective_column: str,
    value_column: str,
    age_limit: int | None,
) -> pl.DataFrame:
    left = members.sort(["symbol", "date"])
    right = values.sort(["symbol", effective_column])
    joined = left.join_asof(
        right,
        left_on="date",
        right_on=effective_column,
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    # A report is usable only from the calendar day after its PIT date.
    joined = joined.with_columns(
        pl.when(pl.col(effective_column).is_not_null() & (pl.col("date") >= pl.col(effective_column)))
        .then(pl.col(value_column))
        .otherwise(None)
        .alias(value_column)
    )
    if age_limit is not None:
        joined = joined.with_columns((pl.col("date") - pl.col("period_end")).dt.total_days().alias("_signal_age"))
        joined = joined.with_columns(
            pl.when(pl.col("_signal_age").is_between(0, age_limit, closed="both"))
            .then(pl.col(value_column))
            .otherwise(None)
            .alias(value_column)
        ).drop("_signal_age")
    return joined


def _load_comparator_month(
    day: date,
    candidates: list[str],
    entry_by_path: dict[str, dict],
) -> dict[str, pl.DataFrame]:
    frames: dict[str, pl.DataFrame] = {}
    for field in candidates:
        config_id = COMPARATOR_CONFIGS.get(field, "alt_hs300")
        path = ROOT / "data" / "ext_data" / config_id / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
        rel = path.relative_to(ROOT).as_posix()
        if rel not in entry_by_path:
            raise RuntimeError(f"Comparator partition not present in frozen manifest: {rel}")
        source_column = field
        if field == "receivable_growth_yoy_low":
            continue
        frame = pl.read_parquet(path, columns=["symbol", source_column]).rename({source_column: field})
        if frame.group_by("symbol").len().filter(pl.col("len") != 1).height:
            raise RuntimeError(f"Duplicate symbols in comparator partition {rel}")
        frames[field] = frame
    return frames


def _build_receivable_comparator_periods(balance: pl.DataFrame) -> pl.DataFrame:
    current = balance.select([
        "symbol", "_period_index", "period_end", "accounts_receivable", "_balance_pit",
    ]).rename({
        "period_end": "_current_period_end",
        "accounts_receivable": "_receivable_current",
        "_balance_pit": "_balance_current_pit",
    })
    lag = balance.select([
        "symbol", (pl.col("_period_index") + 4).alias("_period_index"),
        pl.col("_period_index").alias("_lag_period_index"),
        "period_end", "accounts_receivable", "_balance_pit",
    ]).rename({
        "period_end": "_lag_period_end",
        "accounts_receivable": "_receivable_lag",
        "_balance_pit": "_balance_lag_pit",
    })
    periods = current.join(lag, on=["symbol", "_period_index"], how="left").with_columns(
        pl.max_horizontal(["_balance_current_pit", "_balance_lag_pit"])
        .dt.offset_by("1d").alias("_effective")
    ).with_columns(
        pl.when(
            pl.col("_lag_period_end").is_not_null()
            & (pl.col("_period_index") - pl.col("_lag_period_index") == 4)
            & pl.col("_receivable_current").is_not_null()
            & pl.col("_receivable_current").is_finite()
            & (pl.col("_receivable_current") >= 0)
            & pl.col("_receivable_lag").is_not_null()
            & pl.col("_receivable_lag").is_finite()
            & (pl.col("_receivable_lag") > 0)
        )
        .then(-(pl.col("_receivable_current") / pl.col("_receivable_lag") - 1.0))
        .otherwise(None)
        .alias("receivable_growth_yoy_low")
    ).filter(pl.col("_balance_current_pit").is_not_null())
    if periods.filter(
        pl.col("receivable_growth_yoy_low").is_not_null() & pl.col("_balance_lag_pit").is_null()
    ).height:
        raise RuntimeError("A valid receivables proxy is missing its lag report PIT date")
    return (
        periods.select(["symbol", "_current_period_end", "_effective", "receivable_growth_yoy_low"])
        .rename({"_current_period_end": "period_end"})
        .sort(["symbol", "_effective", "period_end"])
        .unique(subset=["symbol", "_effective"], keep="last")
        .sort(["symbol", "_effective"])
    )


def _attach_receivable_comparator(members: pl.DataFrame, periods: pl.DataFrame) -> pl.DataFrame:
    attached = _attach_asof(
        members,
        periods.rename({"_effective": "_receivable_available"}),
        effective_column="_receivable_available",
        value_column="receivable_growth_yoy_low",
        age_limit=None,
    )
    return attached.select(["date", "symbol", "receivable_growth_yoy_low"])


def _month_end_dates(dates: list[date]) -> list[date]:
    return (
        pl.DataFrame({"date": dates})
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("_month"))
        .group_by("_month").agg(pl.col("date").max())
        .sort("_month")["date"].to_list()
    )


def _finite_pairs(frame: pl.DataFrame, right: str) -> pl.DataFrame:
    return frame.filter(
        pl.col("collection_quality_spread").is_finite()
        & pl.col(right).is_finite()
    )


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 2:
        return None
    left_rank = _rankdata_average(left)
    right_rank = _rankdata_average(right)
    if np.std(left_rank) == 0 or np.std(right_rank) == 0:
        return None
    value = float(np.corrcoef(left_rank, right_rank)[0, 1])
    return value if math.isfinite(value) else None


def _rankdata_average(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    return ranks


def _coverage_audit(panel: pl.DataFrame, eligible: pl.DataFrame, dates: list[date]) -> dict:
    valid = panel.filter(pl.col("collection_quality_spread").is_finite()).select(["date", "symbol"])
    date_rows = valid.group_by("date").len().rename({"len": "valid_names"})
    trading_dates = pl.DataFrame({"date": dates})
    windows = {
        "full": (START, END, dates),
        "discovery": (START, DISCOVERY_END, [day for day in dates if day <= DISCOVERY_END]),
        "validation": (VALIDATION_START, END, [day for day in dates if day >= VALIDATION_START]),
        "recent_252": (dates[-252], END, dates[-252:]),
    }
    result = {}
    for label, (start, end, window_dates) in windows.items():
        base = eligible.filter(pl.col("date").is_between(start, end, closed="both"))
        observed = valid.filter(pl.col("date").is_between(start, end, closed="both"))
        name_counts = (
            trading_dates.filter(pl.col("date").is_between(start, end, closed="both"))
            .join(date_rows, on="date", how="left")
            .with_columns(pl.col("valid_names").fill_null(0))
        )
        result[label] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trading_dates": len(window_dates),
            "eligible_member_rows": base.height,
            "valid_member_rows": observed.height,
            "member_row_coverage": observed.height / base.height if base.height else None,
            "dates_with_at_least_one_valid_name": name_counts.filter(pl.col("valid_names") > 0).height,
            "date_coverage": name_counts.filter(pl.col("valid_names") > 0).height / name_counts.height if name_counts.height else None,
            "minimum_valid_names_on_covered_date": name_counts.filter(pl.col("valid_names") > 0)["valid_names"].min() if name_counts.filter(pl.col("valid_names") > 0).height else 0,
        }
    return result


def _synthetic_checks() -> dict:
    def period_index(day: date) -> int:
        return day.year * 4 + (day.month - 1) // 3 + 1

    current = date(2024, 6, 30)
    prior = date(2023, 6, 30)
    missing = date(2023, 3, 31)
    check_exact_quarter = period_index(current) - period_index(prior) == 4
    check_missing_quarter_rejected = period_index(current) - period_index(missing) != 4

    source_income = pl.DataFrame({"symbol": ["A"], "period_end": [current], "revenue": [20.0]})
    source_balance = pl.DataFrame({"symbol": ["A"], "period_end": [current], "revenue": [99.0], "accounts_receivable": [10.0]})
    selected_income = source_income.select(["symbol", "period_end", pl.col("revenue").alias("_revenue")])
    selected_balance = source_balance.select(["symbol", "period_end", pl.col("accounts_receivable").alias("_receivables")])
    merged = selected_income.join(selected_balance, on=["symbol", "period_end"], how="inner")
    check_duplicate_output_column_avoidance = len(merged.columns) == len(set(merged.columns)) and "revenue" not in merged.columns

    pit_day = date(2024, 8, 20)
    available = financial_pit_date(date(2024, 6, 30), pit_day) + timedelta(days=1)
    daily = pl.DataFrame({
        "symbol": ["A", "A", "A", "A"],
        "date": [pit_day, available, date(2024, 9, 5), date(2024, 9, 6)],
    })
    reports = pl.DataFrame({
        "symbol": ["A", "A"],
        "period_end": [date(2024, 6, 30), date(2024, 9, 30)],
        "_available": [available, date(2024, 9, 5)],
        "value": [0.25, None],
    })
    attached = _attach_asof(
        daily,
        reports,
        effective_column="_available",
        value_column="value",
        age_limit=None,
    )
    attached_values = attached["value"].to_list()
    check_same_day_excluded = attached_values[0] is None
    check_next_day_included = attached_values[1] == 0.25
    check_new_missing_report_is_null_barrier = attached_values[2] is None and attached_values[3] is None
    expiry_members = pl.DataFrame({
        "symbol": ["A", "A"],
        "date": [current + timedelta(days=MAX_AGE_DAYS), current + timedelta(days=MAX_AGE_DAYS + 1)],
    })
    expiry_values = pl.DataFrame({
        "symbol": ["A"],
        "period_end": [current],
        "_available": [current],
        "value": [0.5],
    })
    expiry_attached = _attach_asof(
        expiry_members,
        expiry_values,
        effective_column="_available",
        value_column="value",
        age_limit=MAX_AGE_DAYS,
    )
    expiry_values_attached = expiry_attached["value"].to_list()
    check_expiry_180_valid = expiry_values_attached[0] == 0.5
    check_expiry_181_invalid = expiry_values_attached[1] is None

    def source_key_guard(frame: pl.DataFrame) -> bool:
        duplicates = frame.group_by(["symbol", "period_end"]).len().filter(pl.col("len") != 1)
        return duplicates.height > 0

    duplicate_source = pl.DataFrame({"symbol": ["A", "A"], "period_end": [current, current]})
    check_duplicate_key_detected = source_key_guard(duplicate_source)
    checks = {
        "exact_four_quarter_pair_accepted": check_exact_quarter,
        "missing_quarter_gap_rejected": check_missing_quarter_rejected,
        "duplicate_source_key_detected": check_duplicate_key_detected,
        "duplicate_output_column_avoidance": check_duplicate_output_column_avoidance,
        "same_pit_date_excluded": check_same_day_excluded,
        "following_calendar_day_available": check_next_day_included,
        "new_missing_report_null_barrier": check_new_missing_report_is_null_barrier,
        "age_180_days_valid": check_expiry_180_valid,
        "age_181_days_expired": check_expiry_181_invalid,
    }
    return {"checks": checks, "all_pass": all(checks.values())}


def _correlation_audit(
    month_ends: list[date],
    member_values: pl.DataFrame,
    candidate_column: str,
    receivable_column: str,
    entry_by_path: dict[str, dict],
) -> tuple[dict, dict]:
    candidate = member_values.select(["date", "symbol", candidate_column, receivable_column]).rename({
        candidate_column: "collection_quality_spread",
        receivable_column: "receivable_growth_yoy_low",
    })
    comparators = {name: [] for name in ALL_COMPARATORS}
    incremental = []
    month_names = []
    for day in month_ends:
        base = candidate.filter(pl.col("date") == day)
        by_month = _load_comparator_month(day, list(ALL_COMPARATORS), entry_by_path)
        for field in ALL_COMPARATORS:
            if field == "receivable_growth_yoy_low":
                paired = _finite_pairs(base, field)
            else:
                paired = _finite_pairs(base.join(by_month[field], on="symbol", how="left"), field)
            n = paired.height
            corr = None
            if n >= MIN_PAIRED:
                corr = _spearman(
                    paired["collection_quality_spread"].to_numpy(),
                    paired[field].to_numpy(),
                )
            comparators[field].append({
                "date": day.isoformat(),
                "paired_names": n,
                "spearman": corr,
                "abs_spearman": abs(corr) if corr is not None else None,
            })
        pair = _finite_pairs(base, "receivable_growth_yoy_low")
        n = pair.height
        if n >= MIN_PAIRED:
            candidate_values = pair["collection_quality_spread"].to_numpy()
            old_values = pair["receivable_growth_yoy_low"].to_numpy()
            candidate_rank = _rankdata_average(candidate_values)
            old_rank = _rankdata_average(old_values)
            design = np.column_stack([np.ones(n), old_rank])
            coefficients, *_ = np.linalg.lstsq(design, candidate_rank, rcond=None)
            residual = candidate_rank - design @ coefficients
            rank_std = float(np.std(candidate_rank, ddof=1))
            residual_std = float(np.std(residual, ddof=1))
            monthly = {
                "date": day.isoformat(),
                "paired_names": n,
                "candidate_old_spearman": _spearman(candidate_rank, old_rank),
                "candidate_rank_std": rank_std,
                "residual_rank_std": residual_std,
                "residual_to_candidate_rank_std": residual_std / rank_std if rank_std > 0 else None,
            }
        else:
            monthly = {
                "date": day.isoformat(),
                "paired_names": n,
                "candidate_old_spearman": None,
                "candidate_rank_std": None,
                "residual_rank_std": None,
                "residual_to_candidate_rank_std": None,
            }
        incremental.append(monthly)
        month_names.append(day.isoformat())

    summary = {}
    for field, rows in comparators.items():
        usable = [row for row in rows if row["abs_spearman"] is not None]
        summary[field] = {
            "eligible_months": len(usable),
            "all_months": len(rows),
            "mean_abs_spearman": float(np.mean([row["abs_spearman"] for row in usable])) if usable else None,
            "mean_signed_spearman": float(np.mean([row["spearman"] for row in usable])) if usable else None,
            "monthly": rows,
        }
    sufficient = [item["mean_abs_spearman"] for item in summary.values() if item["mean_abs_spearman"] is not None]
    f2_pass = (
        len(sufficient) == len(ALL_COMPARATORS)
        and all(summary[name]["eligible_months"] >= 30 for name in ALL_COMPARATORS)
        and max(sufficient) <= CORRELATION_LIMIT
    )
    incremental_usable = [
        item for item in incremental
        if item["paired_names"] >= MIN_PAIRED
        and item["residual_rank_std"] is not None
        and item["residual_rank_std"] > 0
    ]
    f3_pass = len(incremental_usable) / len(month_ends) >= 0.80 if month_ends else False
    f3 = {
        "month_ends": len(month_ends),
        "usable_month_ends": len(incremental_usable),
        "usable_rate": len(incremental_usable) / len(month_ends) if month_ends else 0.0,
        "passes_nonzero_incremental_rank_gate": f3_pass,
        "monthly": incremental,
    }
    return {
        "comparators": summary,
        "max_mean_absolute_monthly_spearman": max(sufficient) if sufficient else None,
        "F2_pass": f2_pass,
    }, f3


def main() -> None:
    if OUTPUT_PATH.exists() or OUTPUT_PATH.with_name(OUTPUT_PATH.name + ".sha256").exists():
        raise RuntimeError("R301 feasibility result already exists; refusing overwrite")
    prereg, manifest, frozen_hashes = _read_frozen_inputs()
    entry_by_path = {item["path"]: item for item in manifest}

    synthetic = _synthetic_checks()
    if not synthetic["all_pass"]:
        raise RuntimeError("R301 deterministic synthetic boundary checks failed")

    candidate_periods, _, financial_audit = _financial_period_signals()
    balance = _parse_financial(
        ROOT / "data/financials/balance_sheet/part.parquet",
        "accounts_receivable",
        "_balance_pit",
    )
    receivable_periods = _build_receivable_comparator_periods(balance)
    eligible_members, trading_dates, universe_audit = _membership_universe()
    if len(trading_dates) != 1038 or trading_dates[-1] != END:
        raise RuntimeError("R301 sample trading dates differ from the frozen input manifest")

    candidate_daily = _attach_asof(
        eligible_members,
        candidate_periods.rename({"_candidate_effective": "_candidate_available"}),
        effective_column="_candidate_available",
        value_column="collection_quality_spread",
        age_limit=MAX_AGE_DAYS,
    )
    member_values = candidate_daily.select(["date", "symbol", "collection_quality_spread"]).join(
        _attach_receivable_comparator(eligible_members, receivable_periods),
        on=["date", "symbol"], how="left",
    ).sort(["date", "symbol"])
    month_ends = _month_end_dates(trading_dates)
    correlations, incremental = _correlation_audit(
        month_ends,
        member_values,
        "collection_quality_spread",
        "receivable_growth_yoy_low",
        entry_by_path,
    )

    coverage = _coverage_audit(member_values, eligible_members, trading_dates)
    coverage_pass = all(
        coverage[name]["member_row_coverage"] is not None
        and coverage[name]["member_row_coverage"] >= 0.90
        for name in ("full", "discovery", "validation", "recent_252")
    ) and all(
        coverage[name]["date_coverage"] is not None and coverage[name]["date_coverage"] >= 0.95
        for name in ("full", "discovery", "validation", "recent_252")
    )
    gate_results = {
        "F1_coverage": coverage_pass,
        "F2_nonduplicate_correlations": correlations["F2_pass"],
        "F3_incremental_rank_information": incremental["passes_nonzero_incremental_rank_gate"],
        "F4_boundary_logic": synthetic["all_pass"],
    }
    result = {
        "round": "R301",
        "classification": "feasibility_audit_with_prior_negative_result_context_disclosed_no_R301_outcomes_read",
        "status": "complete",
        "factor": "collection_quality_spread",
        "formula": "current cumulative revenue / same fiscal quarter one year earlier cumulative revenue - 1, minus current accounts receivable / accounts receivable exactly four fiscal quarters earlier - 1",
        "direction": "high",
        "pit_rule": "financial_pit_date independently applied to current and lag income and balance-sheet records; availability starts max(four PIT dates) + 1 calendar day",
        "stale_rule": "candidate value is null when signal_date - current period_end exceeds 180 calendar days",
        "sample": {
            "full": [START.isoformat(), END.isoformat()],
            "discovery": [START.isoformat(), DISCOVERY_END.isoformat()],
            "validation": [VALIDATION_START.isoformat(), END.isoformat()],
            "trading_dates": len(trading_dates),
            "month_end_dates": [day.isoformat() for day in month_ends],
        },
        "universe": universe_audit,
        "financial_construction_audit": financial_audit,
        "receivable_proxy_audit": {
            "method": "R82_formula_proxy_using_current_and_exact_four-quarter-lag balance-sheet rows only",
            "pit_rule": "max(current balance-sheet PIT, exact-lag balance-sheet PIT) + one calendar day",
            "staleness": "No 180-day expiry; attach until superseded by the next balance-sheet report, matching archived R82 attachment behavior",
            "valid_period_rows": receivable_periods.filter(pl.col("receivable_growth_yoy_low").is_not_null()).height,
            "paired_daily_rows": member_values.filter(pl.col("receivable_growth_yoy_low").is_finite()).height,
            "not_original_R82_series": True,
        },
        "coverage": coverage,
        "independence": correlations,
        "incremental_rank_information": incremental,
        "synthetic_boundary_checks": synthetic,
        "gates": gate_results,
        "all_feasibility_gates_pass": all(gate_results.values()),
        "outcome_data_loaded": False,
        "data_scope": "income and balance-sheet snapshots, R78 daily membership, current industry map, frozen monthly factor partitions, prior negative research decisions; no forward returns, strategy caches, or trade outcomes were loaded by this audit",
        "input_manifest_file_count": len(manifest),
        "frozen_hashes": frozen_hashes,
        "limitations": [
            "Financial data are current snapshots and may contain restatements; statutory fallback dates do not recreate first-publication vintages.",
            "Industry exclusions use the current classification map, not point-in-time historical classifications.",
            "This is not a blind feasibility audit: prior R82 result summaries and selected R242/R248 outcome material had been read before R301 analysis; see the deviation disclosure. These prior negative predictive conclusions remain binding.",
            "A feasibility pass would not establish predictive power or alpha; an independent frozen outcome test would still be required.",
        ],
        "panel_candidate_id": frozen_hashes["panel_candidate_id"],
        "panel_status": "pending",
        "preregistration": PRE_PATH.name,
        "amendment": AMENDMENT_PATH.name,
        "comparator_amendment": COMPARATOR_AMENDMENT_PATH.name,
        "preanalysis_deviation": DEVIATION_PATH.name,
    }
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT_PATH.open("xb") as stream:
        stream.write(encoded)
    digest = _sha256_bytes(encoded)
    with OUTPUT_PATH.with_name(OUTPUT_PATH.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {OUTPUT_PATH.name}\n")
    print(json.dumps({"gates": gate_results, "all_feasibility_gates_pass": result["all_feasibility_gates_pass"], "coverage": {key: value["member_row_coverage"] for key, value in coverage.items()}, "max_mean_absolute_monthly_spearman": correlations["max_mean_absolute_monthly_spearman"], "incremental_rank_usable_rate": incremental["usable_rate"], "result_sha256": digest}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
