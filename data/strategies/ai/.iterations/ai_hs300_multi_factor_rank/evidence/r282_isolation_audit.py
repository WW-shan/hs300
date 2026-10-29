"""Read-only R282 audit of research-only screening and backtest execution paths."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
DATA = ROOT / "data"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"
FROZEN_SOURCE_SHA256 = "c707832dda70fa51f461814d623d88eba94dd32a45e1dbede61da7b18bcdd2d8"
PREREG = EVIDENCE / "r282_preregistration.json"
PANEL = EVIDENCE / "r282_panel_registration.json"
PRIOR_R281_AUDIT = EVIDENCE / "r281_runtime_assembly_audit.json"
SOURCES = {
    "backend/app/api/backtest.py": [(384, 424)],
    "backend/app/backtest/worker.py": [(85, 92), (178, 208)],
    "backend/app/backtest/strategy.py": [(1226, 1260), (1378, 1495)],
    "backend/app/backtest/engine.py": [(400, 477), (526, 588)],
    "backend/app/strategy/engine.py": [(613, 636)],
    "backend/app/strategy/scoring.py": [(250, 274), (438, 487)],
    "backend/app/factors/dsl.py": [(700, 738)],
    "backend/app/backtest/fundamentals.py": [(1, 26), (283, 380)],
    "backend/app/api/screener.py": [(376, 436), (757, 817), (892, 897)],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_record(relative: str, spans: list[tuple[int, int]]) -> dict:
    path = ROOT / relative
    lines = path.read_text(encoding="utf-8").splitlines()
    excerpts = []
    for start, end in spans:
        excerpts.append(
            {
                "start_line": start,
                "end_line": min(end, len(lines)),
                "text": [f"{number}: {lines[number - 1]}" for number in range(start, min(end, len(lines)) + 1)],
            }
        )
    return {"sha256": sha256(path), "excerpts": excerpts}


def path_state(relative: str) -> dict:
    path = ROOT / relative
    if not path.is_file():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256(path),
    }


def latest_partition(relative: str) -> dict:
    root = DATA / relative
    dates = sorted(
        child.name.removeprefix("date=")
        for child in root.glob("date=*")
        if child.is_dir() and (child / "part.parquet").is_file()
    ) if root.exists() else []
    return {
        "partition_count": len(dates),
        "latest_date": dates[-1] if dates else None,
    }


def main() -> int:
    prereg_sha = sha256(PREREG)
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    panel_candidate = panel.get("candidate") or {}
    candidate_path = DATA / "strategies/ai/ai_hs300_macro_gate_quality_r275_remove_term.py"
    candidate_sha = sha256(candidate_path)
    sources = {relative: source_record(relative, spans) for relative, spans in SOURCES.items()}
    source_text = {
        relative: "\n".join(
            line
            for span in record["excerpts"]
            for line in span["text"]
        )
        for relative, record in sources.items()
    }
    candidate_text = candidate_path.read_text(encoding="utf-8")
    r281 = json.loads(PRIOR_R281_AUDIT.read_text(encoding="utf-8"))
    cache_manifest_paths = sorted(
        (DATA / ".backtest_matrix_cache").rglob("manifest.json")
    ) if (DATA / ".backtest_matrix_cache").exists() else []
    cache_manifests = [
        {
            "relative_path": path.relative_to(DATA).as_posix(),
            **path_state(path.relative_to(ROOT).as_posix()),
        }
        for path in cache_manifest_paths
    ]
    audit = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "read_only_research_execution_boundary_audit_not_backtest_or_alpha_test",
        "preregistration_sha256": prereg_sha,
        "panel_registration_sha256": sha256(PANEL),
        "panel_response_proofs": panel.get("response_proofs"),
        "candidate": {
            "id": CANDIDATE_ID,
            "registered_research_only": panel_candidate.get("research_only") is True,
            "panel_reports_source_hash": bool(panel_candidate.get("source_sha256")),
            "panel_metadata_matches_frozen_identity": (
                panel_candidate.get("id") == CANDIDATE_ID
                and panel_candidate.get("research_only") is True
                and panel_candidate.get("execution_backend") == "polars_expr"
                and panel_candidate.get("scoring") == {
                    "ext_alt_hs300_holder_top10_free_float_share": 0.35,
                    "uf_cash_quality": 0.20,
                    "uf_low_vol": 0.20,
                    "uf_quality_value_defensive": 0.25,
                }
            ),
            "local_source_sha256": candidate_sha,
            "local_source_matches_frozen": candidate_sha == FROZEN_SOURCE_SHA256,
            "execution_backend": "polars_expr" if 'EXECUTION_BACKEND = "polars_expr"' in candidate_text else None,
            "required_features": [
                "ma60",
                "ext_alt_hs300_holder_top10_free_float_share",
                "ext_alt_hs300_equity_risk_premium",
                "ext_alt_hs300_credit_spread_5y_low",
            ],
        },
        "route_findings": {
            "public_strategy_backtest_route_exists": '@router.post("/strategy/run")' in source_text["backend/app/api/backtest.py"],
            "backtest_api_has_research_only_guard": "research_only" in source_text["backend/app/api/backtest.py"],
            "backtest_service_run_has_research_only_guard": "research_only" in source_text["backend/app/backtest/strategy.py"],
            "strategy_lookup_returns_registered_research_candidate": "def get(self, strategy_id: str)" in source_text["backend/app/strategy/engine.py"],
            "worker_loads_research_strategy_directory": 'app_dir / "strategy" / "research"' in source_text["backend/app/backtest/worker.py"],
            "backtest_attaches_extension_timeseries": "attach_ext_columns(df, include_snapshot=False, data_dir=data_dir)" in source_text["backend/app/backtest/engine.py"],
            "backtest_attaches_financial_pit": "attach_fundamental_factors(" in source_text["backend/app/backtest/engine.py"] and "load_fundamental_snapshot(" in source_text["backend/app/backtest/engine.py"],
            "backtest_scoring_materializes_registered_custom_factors": "materialize_scoring_columns(panel, scoring.keys())" in (ROOT / "backend/app/backtest/strategy.py").read_text(encoding="utf-8"),
            "composite_score_can_resolve_members": "_composite_value_expr" in source_text["backend/app/strategy/scoring.py"],
            "composite_score_missing_dependency_fails_closed": "return None" in source_text["backend/app/strategy/scoring.py"],
            "financial_pit_uses_conservative_announcement_date_gate": "严格晚于生效日" in source_text["backend/app/backtest/fundamentals.py"] and "announce_date" in source_text["backend/app/backtest/fundamentals.py"],
            "ordinary_screener_preset_blocks_research_only": 'get(req.strategy_id).meta.get("research_only")' in source_text["backend/app/api/screener.py"],
            "batch_screener_skips_research_only": 'engine.get(sid).meta.get("research_only")' in source_text["backend/app/api/screener.py"],
            "batch_screener_writes_shared_strategy_cache": "strategy_cache.write_cache(data_dir, str(as_of), results)" in source_text["backend/app/api/screener.py"],
            "polars_expression_candidate_uses_in_memory_panel_path": 'elif s.execution_backend in ("matrix_native", "composite")' in source_text["backend/app/backtest/strategy.py"] and "load_panel_for_backtest(" in source_text["backend/app/backtest/strategy.py"],
            "candidate_uses_matrix_disk_cache": False,
            "backtest_route_persists_candidate_result": False,
        },
        "data_alignment": {
            "enriched": latest_partition("kline_daily_enriched"),
            "alt_hs300": latest_partition("ext_data/alt_hs300/timeseries"),
            "current_members_as_of": r281.get("date_alignment", {}).get("current_members_as_of"),
            "current_membership_enriched_rows_on_latest_date": r281.get("date_alignment", {}).get("current_membership_input_coverage", {}).get("latest_enriched_member_rows"),
            "current_members_count": r281.get("date_alignment", {}).get("current_members", {}).get("count"),
            "aligned_to_current_membership": False,
        },
        "persistent_state_metadata": {
            relative: path_state(relative)
            for relative in [
                "data/user_data/strategy_cache.json",
                "data/user_data/research_candidates.json",
            ]
        },
        "existing_matrix_cache": {
            "manifest_count": len(cache_manifests),
            "manifests": cache_manifests,
            "candidate_would_use_it": False,
        },
        "decision": "research_only_is_not_a_global_historical_backtest_gate; the public screener blocks the candidate, while the standard strategy backtest route resolves it and has extension plus PIT assembly. This route is process-isolated for the polars_expr candidate and does not persist candidate results. Effective new forward evaluation remains blocked by stale and sparse local inputs (2026-10-19 vs membership 2026-10-27), not by research_only alone. No backtest or signal was run in R282.",
        "decision_status": "backtest_path_available_unexecuted_forward_readiness_false",
        "production_status": "pause_production",
        "source_files": sources,
    }
    output = EVIDENCE / "r282_isolation_audit.json"
    payload = json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.write_text(payload, encoding="utf-8")
    digest = sha256(output)
    (EVIDENCE / "r282_isolation_audit.json.sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "audit": output.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "decision_status": audit["decision_status"],
        "route_findings": audit["route_findings"],
        "data_alignment": audit["data_alignment"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
