"""Recompute frozen R283 gate masks from read-only system-owned partitions."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
PRE = EVIDENCE / "r284_preregistration.json"
PANEL = EVIDENCE / "r284_panel_registration.json"
OUT = EVIDENCE / "r284_gate_mask_diagnostic.json"
START = date(2026, 4, 20)
END = date(2026, 10, 16)
FIELD_MAP = {
    "holder_top10_free_float_share": "ext_alt_hs300_holder_top10_free_float_share",
    "equity_risk_premium": "ext_alt_hs300_equity_risk_premium",
    "credit_spread_5y_low": "ext_alt_hs300_credit_spread_5y_low",
    "term_spread_10y_2y": "ext_alt_hs300_term_spread_10y_2y",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def partitions(root: Path, start: date, end: date) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for part in root.glob("date=*/part.parquet"):
        try:
            day = date.fromisoformat(part.parent.name.removeprefix("date="))
        except ValueError:
            continue
        if start <= day <= end:
            items.append((day.isoformat(), part))
    return sorted(items)


def load_partition_series(parts: list[tuple[str, Path]], columns: list[str]) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    for day, path in parts:
        available = set(pl.read_parquet_schema(path).names())
        selected = [column for column in columns if column in available]
        if "symbol" not in selected:
            continue
        frame = pl.read_parquet(path, columns=selected)
        frame = frame.with_columns(pl.lit(date.fromisoformat(day)).cast(pl.Date).alias("date"))
        frames.append(frame)
    if not frames:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    return pl.concat(frames, how="diagonal_relaxed")


def summary(values: pl.Series) -> dict:
    nonnull = values.drop_nulls()
    return {
        "n": len(values),
        "nonnull": len(nonnull),
        "null": len(values) - len(nonnull),
        "min": nonnull.min() if len(nonnull) else None,
        "max": nonnull.max() if len(nonnull) else None,
        "unique_nonnull": nonnull.n_unique() if len(nonnull) else 0,
    }


def main() -> int:
    if OUT.exists() or OUT.with_name(OUT.name + ".sha256").exists():
        raise RuntimeError("R284 diagnostic output exists; refusing overwrite")
    pre_sha = sha(PRE)
    if PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0] != pre_sha:
        raise RuntimeError("R284 preregistration sidecar mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if not panel.get("registration_pass"):
        raise RuntimeError("system-panel registration proof has not passed")
    frozen_hashes = prereg["source_hashes"]
    for item in frozen_hashes.values():
        path = ROOT / item["path"]
        if sha(path) != item["sha256"]:
            raise RuntimeError(f"frozen input changed: {item['path']}")

    membership_path = ROOT / "data/hs300/current_members.json"
    membership_doc = json.loads(membership_path.read_text(encoding="utf-8"))
    symbols = sorted({str(item["symbol"]) for item in membership_doc["members"]})
    if len(symbols) != 300:
        raise RuntimeError(f"expected 300 current HS300 symbols, found {len(symbols)}")
    members = pl.DataFrame({"symbol": symbols})

    root = ROOT / "data/ext_data/alt_hs300/timeseries"
    parts = partitions(root, START, END)
    rows: list[dict] = []
    source_files: list[dict] = []
    date_coverage: list[dict] = []
    for day_text, path in parts:
        day = date.fromisoformat(day_text)
        source_files.append({"date": day_text, "path": path.relative_to(ROOT).as_posix(), "sha256": sha(path), "bytes": path.stat().st_size})
        schema = set(pl.read_parquet_schema(path).names())
        needed = ["symbol", *FIELD_MAP.keys()]
        available = [column for column in needed if column in schema]
        frame = pl.read_parquet(path, columns=available).join(members, on="symbol", how="semi")
        if "symbol" not in available or len(available) != len(needed):
            date_coverage.append({"date": day_text, "partition_rows": None, "current_member_rows": frame.height, "all_fields_present": available})
            continue
        frame = frame.with_columns(pl.lit(day).cast(pl.Date).alias("date"))
        ext = {source: target for source, target in FIELD_MAP.items() if source in frame.columns}
        frame = frame.rename(ext)
        frame = frame.with_columns(
            pl.col(FIELD_MAP["equity_risk_premium"]).is_not_null().alias("_erp_ok"),
            pl.col(FIELD_MAP["credit_spread_5y_low"]).is_not_null().alias("_credit_ok"),
            pl.col(FIELD_MAP["term_spread_10y_2y"]).is_not_null().alias("_term_ok"),
            pl.col(FIELD_MAP["holder_top10_free_float_share"]).is_not_null().alias("_holder_ok"),
        ).with_columns(
            (pl.col(FIELD_MAP["equity_risk_premium"]) >= 5.5).fill_null(False).alias("_erp_pass"),
            (pl.col(FIELD_MAP["credit_spread_5y_low"]) >= -0.8).fill_null(False).alias("_credit_pass"),
            (pl.col(FIELD_MAP["term_spread_10y_2y"]) >= 0.3).fill_null(False).alias("_term_pass"),
        ).with_columns(
            (pl.col("_holder_ok") & pl.col("_erp_pass") & pl.col("_credit_pass")).alias("_candidate_pass"),
        ).with_columns(
            (pl.col("_candidate_pass") & pl.col("_term_pass")).alias("_control_pass"),
        )
        agg = frame.select(
            pl.len().alias("current_member_rows"),
            pl.col("_holder_ok").sum().alias("holder_nonnull"),
            pl.col("_erp_ok").sum().alias("erp_nonnull"),
            pl.col("_credit_ok").sum().alias("credit_nonnull"),
            pl.col("_term_ok").sum().alias("term_nonnull"),
            pl.col("_candidate_pass").sum().alias("candidate_pass"),
            pl.col("_control_pass").sum().alias("control_pass"),
            (pl.col("_candidate_pass") & ~pl.col("_term_pass")).sum().alias("candidate_only"),
            (~pl.col("_candidate_pass") & pl.col("_control_pass")).sum().alias("control_only"),
            (~pl.col("_candidate_pass") & ~pl.col("_control_pass")).sum().alias("both_fail"),
            pl.col(FIELD_MAP["equity_risk_premium"]).min().alias("erp_min"),
            pl.col(FIELD_MAP["equity_risk_premium"]).max().alias("erp_max"),
            pl.col(FIELD_MAP["credit_spread_5y_low"]).min().alias("credit_min"),
            pl.col(FIELD_MAP["credit_spread_5y_low"]).max().alias("credit_max"),
            pl.col(FIELD_MAP["term_spread_10y_2y"]).min().alias("term_min"),
            pl.col(FIELD_MAP["term_spread_10y_2y"]).max().alias("term_max"),
        ).row(0, named=True)
        agg["date"] = day_text
        rows.append(agg)
        date_coverage.append({"date": day_text, "partition_rows": frame.height, **agg})

    if not rows:
        raise RuntimeError("no matching system-owned alt_hs300 partitions in frozen R283 window")
    daily = pl.DataFrame(rows).sort("date")
    totals = {
        key: int(daily.get_column(key).sum())
        for key in ["current_member_rows", "holder_nonnull", "erp_nonnull", "credit_nonnull", "term_nonnull", "candidate_pass", "control_pass", "candidate_only", "control_only", "both_fail"]
    }
    different_dates = daily.filter(pl.col("candidate_only") > 0).select("date", "candidate_only", "erp_min", "erp_max", "credit_min", "credit_max", "term_min", "term_max").to_dicts()
    report = {
        "round": "R284",
        "classification": "read_only_local_gate_mask_recomputation_on_R283_window_not_alpha_test",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": pre_sha,
        "panel_registration_sha256": sha(PANEL),
        "universe": {"as_of": membership_doc.get("as_of"), "source": membership_doc.get("source"), "count": len(symbols), "symbols_sha256": hashlib.sha256(("\n".join(symbols) + "\n").encode()).hexdigest(), "membership_file_sha256": sha(membership_path), "survivorship_bias": True},
        "window": {"start": START.isoformat(), "end": END.isoformat(), "partition_count_with_data": len(parts), "data_dates": [day for day, _ in parts]},
        "formula": {"candidate": "holder non-null AND ERP >= 5.5 AND credit spread >= -0.8", "control": "candidate AND term spread >= 0.3", "missing_comparisons": "false / fail-closed"},
        "totals": totals,
        "gate_difference_date_count": len(different_dates),
        "gate_difference_dates": different_dates,
        "daily": daily.to_dicts(),
        "coverage": {"expected_trading_days_from_local_partitions": len(parts), "dates_missing_required_fields": [x for x in date_coverage if len(x.get("all_fields_present", FIELD_MAP)) != len(FIELD_MAP)], "member_rows_per_day_min": min(x["current_member_rows"] for x in rows), "member_rows_per_day_max": max(x["current_member_rows"] for x in rows), "partition_manifest": source_files, "date_coverage": date_coverage},
        "input_hashes": {"r283_candidate_strategy": frozen_hashes["candidate_strategy"], "r283_control_strategy": frozen_hashes["control_strategy"], "alt_hs300_config": {"path": "data/ext_data/alt_hs300/config.json", "sha256": sha(ROOT / "data/ext_data/alt_hs300/config.json")}, "current_members": frozen_hashes["current_members"]},
        "interpretation": None,
        "limitations": [
            "The study interval was previously examined in R275/R283 and is not independent.",
            "Current 2026-10-27 official members are applied retroactively to the historical interval, creating survivorship bias.",
            "Macro observed date is not vendor publication/collection time; historical point-in-time availability and revisions remain unverified.",
            "This counts eligible rows, not selected top-10 positions; rankings/basic filters/portfolio constraints can leave actual executed trades identical even when some rows differ.",
        ],
        "no_effects": {"backtest_api_called": False, "provider_fetched": False, "cache_or_market_data_written": False, "paper_account_or_order_created": False, "strategy_source_changed": False},
        "production_status": "pause_production",
    }
    if totals["candidate_only"] == 0:
        report["interpretation"] = "The term-spread condition excluded no rows passing the other frozen conditions in the aligned local source partitions; R283 identical outputs are explained by this gate being inactive on the tested rows, subject to the listed coverage/PIT limits."
    elif totals["control_only"] == 0:
        report["interpretation"] = "The term-spread condition removed candidate-eligible rows, but R283 executed trades remained the same; identical portfolios can arise because the changed rows did not alter selected entries/positions. This diagnostic does not identify which portfolio constraint dominates."
    else:
        report["interpretation"] = "Candidate and control gate masks differ in both directions in the source partitions; the exact row-level masks must be reviewed before inferring why R283 trades stayed the same."

    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    with OUT.open("xb") as stream:
        stream.write(payload)
    digest = sha(OUT)
    with OUT.with_name(OUT.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {OUT.name}\n")
    print(json.dumps({"diagnostic": OUT.name, "sha256": digest, "window": report["window"], "totals": totals, "gate_difference_date_count": len(different_dates), "interpretation": report["interpretation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
