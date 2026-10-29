#!/usr/bin/env python3
"""Read-only R280 audit of isolated R275 after-close snapshot feasibility."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import polars as pl  # noqa: E402

DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r280_preregistration.json"
PANEL_OUT = EVIDENCE / "r280_panel_registration.json"
OUT = EVIDENCE / "r280_snapshot_feasibility_audit.json"
API = "http://127.0.0.1:3018"
CONTROL = "ai_hs300_macro_gate_quality"
CANDIDATE = "ai_hs300_macro_gate_quality_r275_remove_term"
FACTORS = (
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
)
FINANCIAL_COLUMNS = (
    "roe_latest", "pb_latest", "debt_ratio_latest", "ocf_ni", "accrual",
)


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    path.write_bytes(raw)
    digest = sha_bytes(raw)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def api_get(opener, endpoint: str) -> tuple[int, dict]:
    request = Request(API + endpoint, method="GET")
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except (URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


def partition_snapshot(directory: Path) -> dict:
    parts = sorted(
        (item for item in directory.glob("date=*/part.parquet") if item.is_file()),
        key=lambda item: item.parent.name.removeprefix("date="),
    )
    if not parts:
        return {"directory": directory.relative_to(ROOT).as_posix(), "latest_date": None}
    latest = parts[-1]
    schema = pl.scan_parquet(str(latest)).collect_schema().names()
    return {
        "directory": directory.relative_to(ROOT).as_posix(),
        "partition_count": len(parts),
        "latest_date": latest.parent.name.removeprefix("date="),
        "latest_partition": latest.relative_to(ROOT).as_posix(),
        "latest_partition_sha256": sha_file(latest),
        "latest_partition_size_bytes": latest.stat().st_size,
        "latest_partition_mtime_utc": datetime.fromtimestamp(latest.stat().st_mtime, UTC).isoformat(),
        "columns": schema,
    }


def source_summary(relative_path: str, needles: tuple[str, ...]) -> dict:
    path = ROOT / relative_path
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = {
        needle: [index for index, line in enumerate(lines, 1) if needle in line][:12]
        for needle in needles
    }
    return {
        "path": relative_path,
        "sha256": sha_file(path),
        "line_matches": matches,
    }


def strategy_source(strategy_id: str) -> dict:
    path = DATA / "strategies/ai" / f"{strategy_id}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    meta = ast.literal_eval(assignments["META"])
    required = ast.literal_eval(assignments["REQUIRED_FEATURES"])
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha_file(path),
        "research_only": meta.get("research_only") is True,
        "scoring": meta.get("scoring", {}),
        "required_features": sorted(required),
    }


def main() -> int:
    for path in (PANEL_OUT, OUT, PANEL_OUT.with_suffix(".json.sha256"), OUT.with_suffix(".json.sha256")):
        if path.exists():
            raise RuntimeError(f"R280 evidence exists; refusing overwrite: {path.name}")
    prereg_sidecar = Path(str(PREREG) + ".sha256")
    prereg_sha = sha_file(PREREG)
    if prereg_sidecar.read_text(encoding="ascii").split()[0] != prereg_sha:
        raise RuntimeError("R280 preregistration checksum mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha_file(Path(__file__).resolve()) != prereg["runner_sha256"]:
        raise RuntimeError("R280 runner differs from frozen preregistration")

    opener = build_opener(ProxyHandler({}))
    panel_started = datetime.now(UTC)
    strategies_status, strategies_payload = api_get(opener, "/api/strategies?include_research=true")
    factors_status, factors_payload = api_get(opener, "/api/factors?asset_type=stock")
    capabilities_status, capabilities_payload = api_get(opener, "/api/settings/capability-matrix")
    sources_status, sources_payload = api_get(opener, "/api/settings/data-sources")
    panel_finished = datetime.now(UTC)

    strategies = {item.get("id"): item for item in strategies_payload.get("strategies", [])}
    factors = {item.get("id"): item for item in factors_payload.get("factors", [])}
    candidate_source = strategy_source(CANDIDATE)
    control_source = strategy_source(CONTROL)
    candidate_in_panel = strategies.get(CANDIDATE)
    factor_checks = {name: name in factors for name in FACTORS}
    panel_checks = {
        "strategies_api_ok": strategies_status == 200,
        "factors_api_ok": factors_status == 200,
        "candidate_registered_and_research_only": bool(candidate_in_panel and candidate_in_panel.get("research_only") is True),
        "control_registered": CONTROL in strategies,
        "four_scoring_factors_registered": all(factor_checks.values()),
        "zero_strategy_load_errors": not strategies_payload.get("load_errors"),
        "candidate_source_matches_r275_freeze": candidate_source["sha256"] == prereg["frozen_candidate"]["candidate_source_sha256"],
    }
    panel_sha = write_json(PANEL_OUT, {
        "round": "R280",
        "classification": "read_only_live_panel_registration_proof_existing_frozen_candidate",
        "captured_at_utc": panel_finished.isoformat(),
        "capture_started_at_utc": panel_started.isoformat(),
        "api_base": API,
        "preregistration_sha256": prereg_sha,
        "reload_performed": False,
        "registered_candidate": candidate_in_panel,
        "candidate_source": candidate_source,
        "control_source": control_source,
        "required_factors": {name: factors.get(name) for name in FACTORS},
        "factor_count": len(factors),
        "strategy_count_including_research": len(strategies),
        "strategy_load_errors": strategies_payload.get("load_errors", []),
        "capability_http_status": capabilities_status,
        "capabilities": capabilities_payload.get("capabilities", []),
        "data_sources_http_status": sources_status,
        "plugins": sources_payload.get("plugins", []),
        "checks": panel_checks,
        "registration_pass": all(panel_checks.values()),
        "backtest_run": False,
        "signal_generated": False,
        "production_status": "pause_production",
    })

    daily = partition_snapshot(DATA / "kline_daily_enriched")
    alt = partition_snapshot(DATA / "ext_data/alt_hs300/timeseries")
    members_path = DATA / "hs300/current_members.json"
    members = json.loads(members_path.read_text(encoding="utf-8"))
    alt_config_path = DATA / "ext_data/alt_hs300/config.json"
    alt_config = json.loads(alt_config_path.read_text(encoding="utf-8"))
    alt_config_fields = {item.get("name") for item in alt_config.get("fields", [])}
    required = set(candidate_source["required_features"])
    enriched_columns = set(daily.get("columns", []))
    alt_columns = set(alt.get("columns", []))
    alt_required = sorted(name for name in required if name.startswith("ext_alt_hs300_"))
    custom_factors = {}
    for factor_id in ("uf_quality_value_defensive", "uf_cash_quality", "uf_low_vol"):
        path = DATA / "user_data/custom_factors" / f"{factor_id}.json"
        definition = json.loads(path.read_text(encoding="utf-8"))
        custom_factors[factor_id] = {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha_file(path),
            "formula": definition.get("formula"),
            "financial_inputs_present_in_enriched_schema": sorted(set(FINANCIAL_COLUMNS) & enriched_columns),
            "formula_identifier_inputs": sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", definition.get("formula", ""))) & set(FINANCIAL_COLUMNS)),
        }

    expected_financial_inputs = {
        "uf_quality_value_defensive": ["roe_latest", "pb_latest", "debt_ratio_latest"],
        "uf_cash_quality": ["ocf_ni", "accrual"],
    }
    financial_columns_available = {
        name: name in enriched_columns for name in sorted(set(sum(expected_financial_inputs.values(), [])))
    }
    dates = {
        "enriched_daily_latest_partition": daily.get("latest_date"),
        "alt_hs300_latest_partition": alt.get("latest_date"),
        "current_hs300_members_as_of": members.get("as_of"),
        "r279_akshare_sample_daily_latest": prereg["prior_observations"]["r279_akshare_latest_completed_day"],
    }
    date_alignment = len({dates["enriched_daily_latest_partition"], dates["alt_hs300_latest_partition"], dates["current_hs300_members_as_of"]}) == 1
    ext_contract = {
        name: {
            "registered_in_alt_config": name.removeprefix("ext_alt_hs300_") in alt_config_fields,
            "column_in_latest_alt_partition": name in alt_columns,
            "column_in_enriched_daily_partition": name in enriched_columns,
        }
        for name in alt_required
    }

    source_paths = {
        "strategy_context": source_summary("backend/app/services/screener.py", ("def build_strategy_context", "self._load_enriched_for_date(as_of)", "return StrategyDataContext(")),
        "strategy_engine": source_summary("backend/app/strategy/engine.py", ("def run(", "_materialize_scoring_frames(", "_apply_scoring(")),
        "scoring_materializer": source_summary("backend/app/strategy/scoring.py", ("def materialize_scoring_columns(", "compiled.frame_transform(frame)", "if transformed is None:")),
        "screener_api": source_summary("backend/app/api/screener.py", ("def run_preset(", "if engine.get(req.strategy_id).meta.get(\"research_only\"):", "_update_cache_strategy(data_dir", "strategy_cache.write_cache(data_dir")),
        "backtest_financial_pit": source_summary("backend/app/backtest/fundamentals.py", ("financial_pit_date", "FUNDAMENTAL_FACTORS", "strictly")),
    }

    checks = {
        "panel_registration_proof_pass": all(panel_checks.values()),
        "local_daily_and_alt_dates_align": dates["enriched_daily_latest_partition"] == dates["alt_hs300_latest_partition"],
        "local_inputs_align_to_current_members_date": dates["enriched_daily_latest_partition"] == dates["current_hs300_members_as_of"],
        "all_candidate_ext_features_present_in_standard_enriched_context": all(item["column_in_enriched_daily_partition"] for item in ext_contract.values()),
        "financial_pit_columns_present_in_standard_enriched_context": all(financial_columns_available.values()),
        "isolated_research_execution_path_proven_without_shared_cache_or_realtime_hooks": False,
    }
    payload = {
        "round": "R280",
        "classification": "preregistered_read_only_after_close_snapshot_feasibility_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_sha,
        "panel_registration_proof_sha256": panel_sha,
        "input_hashes": {
            "candidate_source": candidate_source["sha256"],
            "enriched_latest_partition": daily.get("latest_partition_sha256"),
            "alt_latest_partition": alt.get("latest_partition_sha256"),
            "current_members": sha_file(members_path),
            "alt_config": sha_file(alt_config_path),
        },
        "dates": dates,
        "date_alignment_pass": date_alignment,
        "partition_schemas": {"enriched_daily": daily, "alt_hs300": alt},
        "current_members": {"as_of": members.get("as_of"), "source": members.get("source"), "count": members.get("count"), "sha256": sha_file(members_path)},
        "required_ext_features": ext_contract,
        "required_financial_factor_inputs": expected_financial_inputs,
        "financial_columns_available_in_enriched": financial_columns_available,
        "custom_factor_definitions": custom_factors,
        "system_call_path_sources": source_paths,
        "checks": checks,
        "all_pass": all(checks.values()),
        "decision": "not_ready_no_signal_generated",
        "side_effects": {
            "panel_reload_performed": False,
            "strategy_or_screener_run": False,
            "backtest_or_parameter_scan_run": False,
            "market_or_extension_data_written": False,
            "shared_strategy_cache_written": False,
            "quote_polling_or_realtime_hooks_started": False,
            "paper_account_or_order_created": False,
            "signal_generated": False,
        },
        "production_status": "pause_production",
    }
    audit_sha = write_json(OUT, payload)
    print(json.dumps({
        "panel_evidence": PANEL_OUT.relative_to(ROOT).as_posix(),
        "panel_sha256": panel_sha,
        "audit_evidence": OUT.relative_to(ROOT).as_posix(),
        "audit_sha256": audit_sha,
        "checks": checks,
        "dates": dates,
    }, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
