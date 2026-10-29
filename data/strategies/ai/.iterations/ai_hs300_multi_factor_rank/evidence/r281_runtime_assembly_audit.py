"""Read-only R281 runtime assembly and PIT financial audit."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
DATA = ROOT / "data"
BASE_URL = "http://127.0.0.1:3018"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"
CONTROL_ID = "ai_hs300_macro_gate_quality"
FROZEN_SOURCE_SHA256 = "c707832dda70fa51f461814d623d88eba94dd32a45e1dbede61da7b18bcdd2d8"
EXT_FIELDS = [
    "holder_top10_free_float_share",
    "equity_risk_premium",
    "credit_spread_5y_low",
]
FINANCIAL_FILES = {
    "metrics": "financials/metrics/part.parquet",
    "income": "financials/income/part.parquet",
    "balance_sheet": "financials/balance_sheet/part.parquet",
    "cash_flow": "financials/cash_flow/part.parquet",
    "shares": "financials/shares/part.parquet",
}
SOURCE_SPANS = {
    "backend/app/indicators/pipeline.py": [(111, 121), (1419, 1465), (2179, 2189)],
    "backend/app/factors/ext_factors.py": [(206, 241), (268, 326), (329, 354)],
    "backend/app/tickflow/repository.py": [(657, 713), (734, 747), (830, 852)],
    "backend/app/services/screener.py": [(79, 131), (196, 255), (257, 355), (436, 491)],
    "backend/app/strategy/engine.py": [(666, 686), (893, 955), (1035, 1066), (1880, 1948)],
    "backend/app/strategy/scoring.py": [(250, 274), (438, 482)],
    "backend/app/factors/dsl.py": [(710, 738)],
    "backend/app/backtest/fundamentals.py": [(283, 340), (343, 412)],
    "backend/app/backtest/engine.py": [(430, 460), (568, 590)],
    "backend/app/backtest/factor.py": [(495, 515)],
    "backend/app/backtest/mining_runtime.py": [(356, 374)],
    "backend/app/api/screener.py": [(376, 417), (757, 818), (892, 897)],
    "backend/app/api/strategy.py": [(47, 55), (365, 410), (413, 457)],
}
SNAPSHOT_CACHE_FILES = [
    "data/user_data/strategy_cache.json",
    "data/user_data/factor_cache.json",
    "data/user_data/fundamental_cache.json",
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(name: str, value: dict) -> str:
    path = EVIDENCE / name
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
    digest = sha256_bytes(path.read_bytes())
    (EVIDENCE / f"{name}.sha256").write_text(
        f"{digest}  {name}\n", encoding="ascii"
    )
    return digest


def get_json(path: str) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(
        BASE_URL + path, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read()
        return response.status, body, json.loads(body)


def file_record(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    data = path.read_bytes()
    return {
        "exists": True,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def partition_record(relative_dir: str) -> dict:
    parent = DATA / relative_dir
    dates = sorted(
        path.name[5:]
        for path in parent.glob("date=*")
        if path.is_dir() and (path / "part.parquet").is_file()
    ) if parent.exists() else []
    if not dates:
        return {"partition_count": 0, "latest_date": None}
    latest_path = parent / f"date={dates[-1]}" / "part.parquet"
    frame = pl.scan_parquet(latest_path)
    schema = frame.collect_schema()
    summary = frame.select(pl.len().alias("rows")).collect().row(0, named=True)
    symbols = (
        frame.select(pl.col("symbol").n_unique().alias("symbols"))
        .collect().item()
        if "symbol" in schema.names() else None
    )
    physical_fields = [
        field for field in EXT_FIELDS if field in schema.names()
    ] if "alt_hs300" in relative_dir else []
    non_null = {}
    for field in physical_fields:
        non_null[field] = frame.select(pl.col(field).is_not_null().sum()).collect().item()
    return {
        "relative_dir": relative_dir,
        "partition_count": len(dates),
        "first_date": dates[0],
        "latest_date": dates[-1],
        "latest_partition_file": str(latest_path.relative_to(ROOT)).replace("\\", "/"),
        "latest_partition_sha256": file_record(latest_path)["sha256"],
        "rows": summary["rows"],
        "distinct_symbols": symbols,
        "schema": {name: str(dtype) for name, dtype in schema.items()},
        "candidate_ext_physical_fields": physical_fields,
        "candidate_ext_non_null_counts": non_null,
    }


def financial_record(name: str, relative_file: str) -> dict:
    path = DATA / relative_file
    base = {"relative_file": f"data/{relative_file}", **file_record(path)}
    if not path.exists():
        return base
    frame = pl.scan_parquet(path)
    schema = frame.collect_schema()
    aggregate: list[pl.Expr] = [pl.len().alias("rows")]
    if "symbol" in schema.names():
        aggregate.append(pl.col("symbol").n_unique().alias("distinct_symbols"))
    for column in ("period_end", "announce_date"):
        if column in schema.names():
            aggregate.extend(
                [
                    pl.col(column).cast(pl.Utf8).min().alias(f"{column}_min"),
                    pl.col(column).cast(pl.Utf8).max().alias(f"{column}_max"),
                ]
            )
    base.update(
        {
            "dataset": name,
            "summary": frame.select(aggregate).collect().row(0, named=True),
            "schema": {column: str(dtype) for column, dtype in schema.items()},
        }
    )
    return base


def source_record(relative_path: str, spans: list[tuple[int, int]]) -> dict:
    path = ROOT / relative_path
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        "relative_path": relative_path,
        "sha256": file_record(path)["sha256"],
        "line_spans": [
            {
                "start": start,
                "end": min(end, len(lines)),
                "excerpt": [
                    f"{index}: {lines[index - 1]}"
                    for index in range(start, min(end, len(lines)) + 1)
                ],
            }
            for start, end in spans
        ],
    }


def main() -> None:
    captured_at = datetime.now(timezone.utc).isoformat()
    shared_cache_before = {
        relative: file_record(ROOT / relative)
        for relative in SNAPSHOT_CACHE_FILES
    }
    endpoint_paths = {
        "strategies": "/api/strategies?include_research=true",
        "factors": "/api/factors?asset_type=stock",
        "capabilities": "/api/settings/capability-matrix",
        "data_sources": "/api/settings/data-sources",
    }
    responses: dict[str, dict] = {}
    payloads: dict[str, dict] = {}
    for key, endpoint in endpoint_paths.items():
        status, body, decoded = get_json(endpoint)
        responses[key] = {
            "path": endpoint,
            "http_status": status,
            "response_sha256": sha256_bytes(body),
            "response_bytes": len(body),
        }
        payloads[key] = decoded

    strategies = payloads["strategies"].get("strategies", [])
    by_id = {item.get("id"): item for item in strategies}
    factors = payloads["factors"].get("factors", [])
    factor_ids = {item.get("id") for item in factors}
    required_ids = [
        "ext_alt_hs300_holder_top10_free_float_share",
        "ext_alt_hs300_equity_risk_premium",
        "ext_alt_hs300_credit_spread_5y_low",
        "uf_quality_value_defensive",
        "uf_low_vol",
        "uf_cash_quality",
    ]
    candidate_path = DATA / "strategies/ai/ai_hs300_macro_gate_quality_r275_remove_term.py"
    candidate_sha = file_record(candidate_path).get("sha256")
    candidate = by_id.get(CANDIDATE_ID, {})
    control = by_id.get(CONTROL_ID, {})
    panel = {
        "captured_at_utc": captured_at,
        "api_base": BASE_URL,
        "classification": "read_only_live_panel_registration_proof_R281",
        "response_proofs": responses,
        "strategy_count_including_research": len(strategies),
        "strategy_load_errors": payloads["strategies"].get("load_errors", []),
        "factor_count": len(factors),
        "candidate": {
            "id": CANDIDATE_ID,
            "registered": bool(candidate),
            "research_only": candidate.get("research_only"),
            "scoring": candidate.get("scoring"),
            "source_path": str(candidate_path.relative_to(ROOT)).replace("\\", "/"),
            "source_sha256": candidate_sha,
            "source_matches_frozen_R275": candidate_sha == FROZEN_SOURCE_SHA256,
        },
        "control": {
            "id": CONTROL_ID,
            "registered": bool(control),
            "research_only": control.get("research_only"),
        },
        "required_factor_ids_registered": {name: name in factor_ids for name in required_ids},
        "capabilities": [
            {
                key: item.get(key)
                for key in ("id", "field", "effective", "usable")
            }
            for item in payloads["capabilities"].get("capabilities", [])
        ],
        "data_source_status": {
            "errors": payloads["data_sources"].get("errors", []),
            "plugins": [
                {key: item.get(key) for key in ("name", "available", "status", "datasets")}
                for item in payloads["data_sources"].get("plugins", [])
            ],
        },
    }
    panel_sha = write_json("r281_panel_registration.json", panel)

    membership_path = DATA / "hs300/current_members.json"
    membership = json.loads(membership_path.read_text(encoding="utf-8"))
    partitions = {
        "enriched_daily": partition_record("kline_daily_enriched"),
        "alt_hs300_timeseries": partition_record("ext_data/alt_hs300/timeseries"),
    }
    financials = {
        name: financial_record(name, relative)
        for name, relative in FINANCIAL_FILES.items()
    }
    member_symbols = {str(item["symbol"]) for item in membership.get("members", [])}
    enriched_latest_path = (
        ROOT / partitions["enriched_daily"]["latest_partition_file"]
        if partitions["enriched_daily"].get("latest_partition_file")
        else None
    )
    alt_latest_path = (
        ROOT / partitions["alt_hs300_timeseries"]["latest_partition_file"]
        if partitions["alt_hs300_timeseries"].get("latest_partition_file")
        else None
    )
    enriched_symbols = (
        set(pl.read_parquet(enriched_latest_path)["symbol"].cast(pl.Utf8).to_list())
        if enriched_latest_path is not None else set()
    )
    alt_latest = pl.read_parquet(alt_latest_path) if alt_latest_path is not None else pl.DataFrame()
    alt_member_rows = alt_latest.filter(pl.col("symbol").cast(pl.Utf8).is_in(member_symbols)) if not alt_latest.is_empty() else alt_latest
    complete_ext = (
        alt_member_rows.select(
            pl.all_horizontal([pl.col(field).is_not_null() for field in EXT_FIELDS])
            .sum().alias("complete_rows")
        ).item()
        if set(EXT_FIELDS).issubset(alt_member_rows.columns) else 0
    )
    extension_config_path = DATA / "ext_data/alt_hs300/config.json"
    extension_config = json.loads(extension_config_path.read_text(encoding="utf-8")) if extension_config_path.exists() else {}
    pull_config = extension_config.get("pull") or {}
    financial_snapshot_check = {
        "method": "read-only in-process call to existing backtest PIT snapshot loader and attachment helper on the current local enriched/current-members intersection; no API execution, cache write, or strategy call",
        "as_of_date": partitions["enriched_daily"].get("latest_date"),
        "member_market_rows": len(enriched_symbols & member_symbols),
        "pit_inputs": {},
    }
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    pit_market = (
        pl.read_parquet(enriched_latest_path).filter(
            pl.col("symbol").cast(pl.Utf8).is_in(member_symbols)
        )
        if enriched_latest_path is not None else pl.DataFrame()
    )
    pit_snapshot = load_fundamental_snapshot(DATA)
    pit_columns = ["roe_latest", "pb_latest", "debt_ratio_latest", "ocf_ni", "accrual"]
    pit_market = attach_fundamental_factors(pit_market, pit_snapshot, pit_columns)
    for column in pit_columns:
        if column in pit_market.columns:
            series = pit_market[column]
            financial_snapshot_check["pit_inputs"][column] = {
                "column_present": True,
                "non_null_rows": int(series.is_not_null().sum()),
                "total_rows": pit_market.height,
                "min": series.min(),
                "max": series.max(),
            }
        else:
            financial_snapshot_check["pit_inputs"][column] = {
                "column_present": False,
                "non_null_rows": 0,
                "total_rows": pit_market.height,
            }
    financial_snapshot_check["snapshot_rows"] = pit_snapshot.height if pit_snapshot is not None else None
    financial_snapshot_check["snapshot_symbols"] = pit_snapshot["symbol"].n_unique() if pit_snapshot is not None and not pit_snapshot.is_empty() else 0
    financial_snapshot_check["snapshot_helpers_used"] = [
        "app.backtest.fundamentals.load_fundamental_snapshot",
        "app.backtest.fundamentals.attach_fundamental_factors",
    ]
    sources = {
        path: source_record(path, spans) for path, spans in SOURCE_SPANS.items()
    }

    ordinary_path = {
        "extension_runtime_assembly": {
            "indicator_history_builder_calls_compute_indicators_and_compute_signals": True,
            "compute_signals_attaches_extension_columns_with_include_snapshot_false": True,
            "extension_timeseries_join_keys": ["symbol", "date"],
            "repository_caches_full_runtime_history_frame": True,
            "repository_fallback_on_runtime_compute_failure_uses_narrow_parquet_frame": True,
            "repository_fallback_does_not_reassemble_extensions_before_return": True,
            "repo_history_cache_and_TTL_history_cache_return_without_fresh_reassembly": True,
            "cold_history_screener_path_recomputes_and_reassembles_via_compute_signals": True,
            "standard_strategy_context_uses_current_and_optional_runtime_history": True,
            "standard_strategy_context_has_no_financial_pit_attach_call": True,
            "standard_scoring_materializes_registered_custom_or_virtual_factors_only_from_frame_inputs": True,
            "required_features_validation_checks_only_context_metadata_and_not_column_presence": True,
            "extension_filter_expression_can_raise_when_required_columns_missing": True,
            "scoring_skips_factors_when_scoring_value_expression_cannot_resolve_dependencies": True,
        },
        "financial_pit_usage_found": [
            "backend/app/backtest/engine.py",
            "backend/app/backtest/factor.py",
            "backend/app/backtest/mining_runtime.py",
        ],
        "financial_pit_semantics": {
            "financial_snapshot_normalizes_announce_dates_and_uses_conservative_deadline_fallback": True,
            "pit_values_become_effective_after_announce_day": True,
            "derived_factor_pit_date_uses_latest_dependency_table_date": True,
            "derived_ttm_inputs_require_income_metrics_cash_flow_and_balance_data": True,
            "ordinary_strategy_scoring_does_not_call_any_fundamentals_helper": True,
        },
        "public_execution_gates": {
            "strategy_run_rejects_research_only_via_public_guard": True,
            "screener_run_preset_rejects_research_only": True,
            "screener_run_all_skips_research_only_and_writes_shared_strategy_cache_for_daily_results": True,
        },
        "execution_side_effect_risk": {
            "direct_strategy_run_returns_only_and_does_not_write_strategy_cache": True,
            "direct_strategy_run_still_rejects_research_only_candidate": True,
            "screener_run_preset_rejects_research_only_candidate": True,
            "screener_run_all_skips_research_only_candidate": True,
            "daily_screener_run_all_writes_shared_strategy_cache": True,
            "batch_progressive_mode_writes_shared_strategy_cache": True,
            "no_independent_signal_archive_endpoint_or_path_was_proven": True,
        },
    }
    date_alignment = {
        "enriched_latest_date": partitions["enriched_daily"].get("latest_date"),
        "alt_hs300_latest_date": partitions["alt_hs300_timeseries"].get("latest_date"),
        "current_members_as_of": membership.get("as_of"),
        "enriched_and_extension_dates_match": partitions["enriched_daily"].get("latest_date") == partitions["alt_hs300_timeseries"].get("latest_date"),
        "local_inputs_match_current_membership_date": partitions["enriched_daily"].get("latest_date") == membership.get("as_of") and partitions["alt_hs300_timeseries"].get("latest_date") == membership.get("as_of"),
        "current_members": {
            "source": membership.get("source"),
            "as_of": membership.get("as_of"),
            "count": membership.get("count"),
            "sha256": file_record(membership_path)["sha256"],
        },
        "prior_R279_system_provider_latest_sample_date": "2026-10-27",
        "prior_R279_evidence_path": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r279_macro_source_audit.json",
        "current_membership_input_coverage": {
            "member_count": len(member_symbols),
            "latest_enriched_member_rows": len(enriched_symbols & member_symbols),
            "latest_enriched_rows": partitions["enriched_daily"].get("rows"),
            "latest_alt_member_rows": alt_member_rows.height,
            "latest_alt_member_rows_with_all_candidate_ext_fields_non_null": int(complete_ext),
        },
        "extension_pull_config": {
            "config_sha256": file_record(extension_config_path).get("sha256"),
            "pull_config_present": bool(pull_config),
            "enabled": bool(pull_config.get("enabled", False)),
            "date_param_configured": bool(pull_config.get("date_param")),
            "last_status": pull_config.get("last_status"),
            "last_rows": pull_config.get("last_rows"),
            "last_run": pull_config.get("last_run"),
        },
    }
    audit = {
        "round": "R281",
        "captured_at_utc": captured_at,
        "classification": "read_only_runtime_extension_and_financial_pit_assembly_audit_not_alpha_test",
        "runner": {
            "relative_path": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": file_record(Path(__file__))["sha256"],
        },
        "panel_registration_proof_sha256": panel_sha,
        "checks": {
            "panel_candidate_registered_research_only_and_source_frozen": bool(candidate) and candidate.get("research_only") is True and candidate_sha == FROZEN_SOURCE_SHA256,
            "all_required_factors_registered": all(name in factor_ids for name in required_ids),
            "extension_columns_have_runtime_assembly_path": True,
            "physical_enriched_schema_is_narrow_storage_not_runtime_context_schema": True,
            "required_financial_raw_datasets_exist": all(value.get("exists") for key, value in financials.items() if key in {"metrics", "income", "balance_sheet", "cash_flow"}),
            "required_financial_pit_columns_are_attached_in_ordinary_strategy_context": False,
            "local_market_and_extension_data_are_current_membership_date_aligned": date_alignment["local_inputs_match_current_membership_date"],
            "isolated_strategy_execution_and_archive_path_proven": False,
        },
        "date_alignment": date_alignment,
        "local_partitions": partitions,
        "financial_datasets": financials,
        "financial_pit_snapshot_probe": financial_snapshot_check,
        "financial_pit_snapshot_probe": financial_snapshot_check,
        "shared_cache_files_before_and_after": {
            relative: {
                "before": shared_cache_before[relative],
                "after": file_record(ROOT / relative),
                "unchanged": shared_cache_before[relative] == file_record(ROOT / relative),
            }
            for relative in SNAPSHOT_CACHE_FILES
        },
        "runtime_call_path_audit": ordinary_path,
        "source_evidence": sources,
        "correction_to_R280": {
            "prior_claim": "standard strategy context has no extension-column assembly path",
            "corrected_finding": "compute_enriched_history_window calls compute_signals; compute_signals attaches timeseries extension columns with include_snapshot=False; repository retains the full runtime frame in its in-memory history/current caches. The enriched Parquet physical schema is intentionally narrow and is not the runtime frame schema.",
            "unchanged_blockers": [
                "local enriched and alt_hs300 data are at 2026-10-19 while current membership is 2026-10-27",
                "ordinary strategy scoring/context has no financial PIT attachment path",
                "no isolated execution plus archival route is proven; public path rejects or skips research-only candidate and shared cache is used by batch screener",
            ],
        },
        "decision": "not_ready_no_signal_generated",
        "production_status": "pause_production",
    }
    audit_sha = write_json("r281_runtime_assembly_audit.json", audit)
    print(json.dumps({"panel_sha256": panel_sha, "audit_sha256": audit_sha, "audit_path": "r281_runtime_assembly_audit.json"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
