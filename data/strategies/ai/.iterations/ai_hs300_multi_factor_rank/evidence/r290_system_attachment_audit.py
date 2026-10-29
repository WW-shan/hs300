#!/usr/bin/env python3
"""Read-only R290 audit through the production extended-column attachment function."""
from __future__ import annotations

import hashlib
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
    members_path = DATA / "hs300" / "current_members.json"
    members_doc = json.loads(members_path.read_text(encoding="utf-8"))
    symbols = sorted({row["symbol"] for row in members_doc["members"]})
    if len(symbols) != 300:
        raise RuntimeError(f"Expected 300 unique current members, found {len(symbols)}")

    kline_path = DATA / "kline_daily_enriched" / f"date={MARKET_DATE}" / "part.parquet"
    kline_latest = latest_partition(DATA / "kline_daily_enriched")
    # The latest daily partition includes a subset of vendor rows whose
    # embedded trade date is older than its partition key; the system loader
    # normalizes this partition to its directory date before panel assembly.
    kline = (
        pl.read_parquet(kline_path, columns=["symbol"])
        .filter(pl.col("symbol").is_in(symbols))
        .with_columns(pl.lit(date.fromisoformat(MARKET_DATE)).alias("date"))
    )
    quoted_symbols = set(kline.get_column("symbol").to_list())
    no_quote_symbols = sorted(set(symbols) - quoted_symbols)

    source_facts: dict[str, dict] = {}
    latest_member_coverage: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="r290_ext_attach_") as scratch_name:
        scratch = Path(scratch_name) / "data"
        for config_id, field_name in COMPONENTS.items():
            source_dir = DATA / "ext_data" / config_id
            config_path = source_dir / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            matching_fields = [f for f in config["fields"] if f["name"] == field_name]
            if len(matching_fields) != 1:
                raise RuntimeError(f"Expected one {field_name} config field in {config_id}")
            config["fields"] = matching_fields

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
            nonnull = source_frame.filter(pl.col(field_name).is_not_null()).height
            member_source_frame = source_frame.filter(pl.col("symbol").is_in(symbols))
            member_nonnull = member_source_frame.filter(pl.col(field_name).is_not_null()).height
            latest_member_coverage[config_id] = {
                "nonnull_current_members": member_nonnull,
                "current_members": len(symbols),
                "coverage": member_nonnull / len(symbols),
            }
            source_facts[config_id] = {
                "config_sha256": sha256(config_path),
                "field": field_name,
                "latest_partition": latest,
                "latest_partition_sha256": sha256(source_part),
                "latest_partition_bytes": source_part.stat().st_size,
                "rows": source_frame.height,
                "distinct_symbols": source_frame.get_column("symbol").n_unique(),
                "nonnull_values": nonnull,
            }

        # Exercise the same public loader used by BacktestEngine, against a
        # scratch copy of only the three real input partitions above.
        attached = attach_ext_columns(kline, include_snapshot=False, data_dir=scratch)
        absent_columns = sorted(FACTOR_COLUMNS - set(attached.columns))
        if absent_columns:
            raise RuntimeError(f"Attachment did not materialize expected columns: {absent_columns}")

        current_rows = attached.height
        embedded_date_counts = (
            pl.read_parquet(kline_path, columns=["date"])
            .group_by("date").len().sort("date")
            .select([pl.col("date").cast(pl.String), pl.col("len")])
            .to_dicts()
        )
        coverage: dict[str, dict] = {}
        for column in sorted(FACTOR_COLUMNS):
            nonnull = attached.filter(pl.col(column).is_not_null()).height
            coverage[column] = {
                "nonnull_current_member_rows": nonnull,
                "current_member_rows_with_market_data": current_rows,
                "coverage": nonnull / current_rows if current_rows else 0.0,
                "distinct_nonnull_symbols": attached.filter(pl.col(column).is_not_null())
                .get_column("symbol").n_unique(),
            }

    qrs_column = "ext_alt_hs300_qrs_quality_relative_strength_min_rank"
    qrs_coverage = coverage[qrs_column]["coverage"]
    component_dates = {facts["latest_partition"] for facts in source_facts.values()}
    factor_date = max(component_dates) if component_dates else None
    ready = factor_date == MARKET_DATE and qrs_coverage >= 0.99
    status = "ready_current_signal" if ready else "not_ready_no_fresh_signal"

    input_paths = {
        "market_partition": kline_path,
        "membership_snapshot": members_path,
        "strategy": ROOT / "data/strategies/ai/ai_hs300_quality_relative_strength_h20_sl20.py",
        "preregistration": EVIDENCE / "r257_preregistration.json",
        "factor_build_evidence": EVIDENCE / "r257_factor_build.json",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_resolver": ROOT / "backend/app/backtest/strategy.py",
        "ext_factor_loader": ROOT / "backend/app/factors/ext_factors.py",
        "ext_config_store": ROOT / "backend/app/services/ext_data.py",
        "hs300_members_service": ROOT / "backend/app/hs300/current.py",
        "candidate_store": ROOT / "backend/app/backtest/candidates.py",
        "r290_system_attachment_audit.py": Path(__file__),
    }
    inputs = {name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
              for name, path in input_paths.items()}
    inputs["r257_source_partitions"] = {
        config_id: {
            "path": str((DATA / "ext_data" / config_id / "timeseries"
                            / f"date={facts['latest_partition']}" / "part.parquet").relative_to(ROOT)),
            "sha256": facts["latest_partition_sha256"],
        }
        for config_id, facts in source_facts.items()
    }

    result = {
        "round": "R290",
        "classification": "read_only_system_attachment_freshness_audit",
        "status": status,
        "question": "Can the production extended-factor attachment path provide an auditable same-date R257 cross-section for the latest market session and current HS300 members?",
        "as_of": {
            "market_date": MARKET_DATE,
            "latest_market_partition": kline_latest,
            "membership_as_of": members_doc["as_of"],
            "membership_source": members_doc["source"],
            "membership_count": len(symbols),
            "market_data_member_rows": current_rows,
            "market_partition_embedded_date_counts": embedded_date_counts,
            "members_without_market_row": no_quote_symbols,
        },
        "factor_inputs": source_facts,
        "latest_factor_partition_member_coverage": latest_member_coverage,
        "system_attachment": {
            "function": "app.factors.ext_factors.attach_ext_columns",
            "system_path": "BacktestEngine.load_panel_for_backtest -> attach_ext_columns(include_snapshot=False)",
            "join_key": ["symbol", "date"],
            "isolated_input_method": "temporary scratch data_dir containing the original config fields and byte-identical latest partitions for only the three R257 tables; source files were not changed",
            "attached_columns": sorted(FACTOR_COLUMNS),
            "coverage": coverage,
            "ready_threshold": {"same_date_factor_partition": MARKET_DATE, "qrs_nonnull_coverage_minimum": 0.99},
            "ready": ready,
        },
        "input_sha256": inputs,
        "execution_limits": {
            "backtest_run": False,
            "forward_returns_read": False,
            "historical_trade_outcomes_read": False,
            "provider_called": False,
            "market_or_ext_data_written": False,
            "strategy_or_production_state_changed": False,
        },
        "conclusion": (
            "The current R257 factor table does not contain an exact-date partition for the latest market session; the production attachment path therefore yields no same-day R257 signal for the current member cross-section."
            if not ready else
            "The production attachment path produced a same-day R257 cross-section meeting the preregistered coverage threshold."
        ),
    }
    output = EVIDENCE / "r290_system_attachment_audit_v2.json"
    if output.exists() or output.with_name(output.name + ".sha256").exists():
        raise RuntimeError("R290 v2 evidence exists; refusing to overwrite")
    payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with output.open("xb") as stream:
        stream.write(payload)
    output.with_name(output.name + ".sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "round": "R290",
        "status": status,
        "market_date": MARKET_DATE,
        "latest_market_partition": kline_latest,
        "latest_factor_partition": factor_date,
        "member_rows_with_market_data": current_rows,
        "qrs_coverage": qrs_coverage,
        "result_sha256": hashlib.sha256(payload).hexdigest(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
