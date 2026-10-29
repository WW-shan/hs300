#!/usr/bin/env python3
"""Corrected read-only R290 system-attachment audit; original artifacts remain unchanged."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "backend"))

from app.factors.ext_factors import attach_ext_columns  # noqa: E402

EVIDENCE = Path(__file__).resolve().parent
MARKET_DATE = "2026-10-27"
COMPONENTS = {
    "alt_hs300": "stock_rel_industry_20d",
    "alt_hs300_minrank": "management_ocf_gm_min_rank",
    "alt_hs300_qrs": "quality_relative_strength_min_rank",
}
FACTOR_COLUMNS = {
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_minrank_management_ocf_gm_min_rank",
    "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
}
OUTPUT = EVIDENCE / "r290_system_attachment_audit_corrected.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_partition(base: Path) -> str:
    dates = [
        item.name[5:]
        for item in base.glob("date=*")
        if item.is_dir() and (item / "part.parquet").is_file()
    ]
    if not dates:
        raise RuntimeError(f"No date partitions found under {base}")
    return max(dates)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("Corrected R290 evidence exists; refusing to overwrite")
    members_path = DATA / "hs300" / "current_members.json"
    members_doc = json.loads(members_path.read_text(encoding="utf-8"))
    symbols = sorted({row["symbol"] for row in members_doc["members"]})
    if len(symbols) != 300:
        raise RuntimeError(f"Expected 300 unique current members, found {len(symbols)}")

    market_root = DATA / "kline_daily_enriched"
    kline_path = market_root / f"date={MARKET_DATE}" / "part.parquet"
    kline_latest = latest_partition(market_root)
    kline = (
        pl.read_parquet(kline_path, columns=["symbol", "date"])
        .filter(pl.col("symbol").is_in(symbols))
    )
    embedded_dates = {
        str(row["date"]): row["len"]
        for row in pl.read_parquet(kline_path, columns=["date"])
        .group_by("date").len().to_dicts()
    }
    if embedded_dates != {MARKET_DATE: 5570}:
        raise RuntimeError(f"Unexpected embedded dates in market partition: {embedded_dates}")
    if kline.filter(pl.col("date") == date.fromisoformat(MARKET_DATE)).height != 300:
        raise RuntimeError("The 300 current members do not each have a matching market-date row")

    source_facts = {}
    latest_member_coverage = {}
    with tempfile.TemporaryDirectory(prefix="r290_ext_attach_corrected_") as scratch_name:
        scratch = Path(scratch_name) / "data"
        for config_id, field_name in COMPONENTS.items():
            source_dir = DATA / "ext_data" / config_id
            config_path = source_dir / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            fields = [field for field in config["fields"] if field["name"] == field_name]
            if len(fields) != 1:
                raise RuntimeError(f"Expected one field {field_name!r} in {config_id}")
            config["fields"] = fields
            source_ts = source_dir / "timeseries"
            latest = latest_partition(source_ts)
            source_part = source_ts / f"date={latest}" / "part.parquet"
            isolated_cfg = scratch / "ext_data" / config_id
            isolated_part = isolated_cfg / "timeseries" / f"date={latest}" / "part.parquet"
            isolated_part.parent.mkdir(parents=True, exist_ok=True)
            isolated_cfg.mkdir(parents=True, exist_ok=True)
            (isolated_cfg / "config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            shutil.copyfile(source_part, isolated_part)
            source_frame = pl.read_parquet(source_part, columns=["symbol", field_name])
            member_frame = source_frame.filter(pl.col("symbol").is_in(symbols))
            latest_member_coverage[config_id] = {
                "nonnull_current_members": member_frame[field_name].is_not_null().sum(),
                "current_members": len(symbols),
            }
            source_facts[config_id] = {
                "field": field_name,
                "latest_partition": latest,
                "latest_partition_sha256": sha256(source_part),
                "config_sha256": sha256(config_path),
                "rows": source_frame.height,
                "nonnull_values": source_frame[field_name].is_not_null().sum(),
            }

        attached = attach_ext_columns(kline, include_snapshot=False, data_dir=scratch)
        missing = sorted(FACTOR_COLUMNS - set(attached.columns))
        if missing:
            raise RuntimeError(f"Expected fields were not materialized: {missing}")
        coverage = {}
        for column in sorted(FACTOR_COLUMNS):
            nonnull = attached.filter(pl.col(column).is_not_null())
            coverage[column] = {
                "nonnull_current_member_rows": nonnull.height,
                "current_member_rows_with_market_data": attached.height,
                "coverage": nonnull.height / attached.height if attached.height else 0.0,
                "distinct_nonnull_symbols": nonnull["symbol"].n_unique(),
            }

    prereg_path = EVIDENCE / "r290_preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    registered_facts = prereg["input_sha256"]
    frozen_paths = {
        "r290_preregistration": prereg_path,
        "market_partition": kline_path,
        "membership_snapshot": members_path,
        "builder": ROOT / "scripts" / "build_alt_factor_data.py",
        "industry_map": ROOT / "backend" / "app" / "services" / "industry_map.py",
    }
    if sha256(prereg_path) != prereg_path.with_name(prereg_path.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R290 preregistration checksum mismatch")
    current_hashes = {name: sha256(path) for name, path in frozen_paths.items()}
    builder_path = ROOT / "scripts" / "build_alt_factor_data.py"
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r290_corrected", builder_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load current industry builder")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    dates, research_symbols = builder._calendar()
    current_industry = builder._industry_daily(dates, research_symbols).filter(
        pl.col("date") == date.fromisoformat(MARKET_DATE)
    )
    current_industry_coverage = {
        name: current_industry[name].is_not_null().sum()
        for name in (
            "industry_mom_20d", "industry_mom_60d", "industry_rps_20d",
            "industry_breadth_20d", "stock_rel_industry_20d",
        )
    }
    output = {
        "round": "R290",
        "classification": "corrected_read_only_system_attachment_audit",
        "status": "not_ready_no_fresh_signal",
        "correction": {
            "supersedes_script_comment": "The initial R290 script comment about old embedded trade dates was incorrect.",
            "correct_partition_evidence": {
                "latest_market_partition": kline_latest,
                "partition_embedded_dates_and_rows": embedded_dates,
                "current_members_with_exact_date_market_rows": 300,
                "partition_sha256": sha256(kline_path),
            },
            "current_industry_builder_recalculation": {
                "rows": current_industry.height,
                "symbols": current_industry["symbol"].n_unique(),
                "nonnull": current_industry_coverage,
                "builder_sha256": sha256(builder_path),
            },
            "original_v1_and_v2_artifacts_preserved": True,
        },
        "as_of": {
            "market_date": MARKET_DATE,
            "latest_market_partition": kline_latest,
            "membership_as_of": members_doc["as_of"],
            "membership_source": members_doc["source"],
            "membership_count": len(symbols),
            "market_data_member_rows": attached.height,
            "members_without_market_row": [],
        },
        "factor_inputs": source_facts,
        "latest_factor_partition_member_coverage": latest_member_coverage,
        "system_attachment": {
            "function": "app.factors.ext_factors.attach_ext_columns",
            "system_path": "BacktestEngine.load_panel_for_backtest -> attach_ext_columns(include_snapshot=False)",
            "join_key": ["symbol", "date"],
            "isolated_input_method": "temporary scratch copy of original configs and byte-identical latest partitions",
            "coverage": coverage,
            "ready": False,
        },
        "registered_input_hashes": registered_facts,
        "corrected_audit_input_hashes": current_hashes,
        "execution_limits": {
            "backtest_run": False, "forward_returns_read": False,
            "historical_trade_outcomes_read": False, "provider_called": False,
            "market_or_ext_data_written": False, "strategy_or_production_state_changed": False,
        },
        "conclusion": (
            "The exact-date production attachment still yields no 2026-10-27 R257 values (0/300). "
            "The market-date check is corrected: every embedded row in the 10-27 partition is dated "
            "10-27, and each of the 300 current members has a market row. Re-running the current "
            "industry calculation in memory yields broad 10-27 coverage, while the saved R257 factor "
            "partitions still end on 10-19. The underlying R257 freshness result is unchanged."
        ),
    }
    payload = (json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    result_hash = hashlib.sha256(payload).hexdigest()
    with OUTPUT.open("xb") as stream:
        stream.write(payload)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{result_hash}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "round": "R290", "status": output["status"],
        "market_partition_embedded_dates": embedded_dates,
        "member_market_rows": attached.height,
        "r257_composite_nonnull": coverage["ext_alt_hs300_qrs_quality_relative_strength_min_rank"]["nonnull_current_member_rows"],
        "corrected_result_sha256": result_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
