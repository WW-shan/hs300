#!/usr/bin/env python3
"""Capture the R271 candidate's panel registration after the exploratory rerun."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data" / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
API_BASE = "http://127.0.0.1:3018"
STRATEGY_ID = "ai_hs300_macro_gate_quality"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r271_industry_shrink05"
CANDIDATE_SOURCE = ROOT / "data" / "strategies" / "ai" / f"{CANDIDATE_ID}.py"
FACTOR_IDS = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
]


def _request(opener, method: str, path: str) -> tuple[int, dict]:
    try:
        with opener.open(Request(API_BASE + path, method=method), timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def _source_contract() -> dict:
    tree = ast.parse(CANDIDATE_SOURCE.read_text(encoding="utf-8"))
    meta = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "META" for target in node.targets)
    )
    values = ast.literal_eval(meta)
    digest = hashlib.sha256(CANDIDATE_SOURCE.read_bytes()).hexdigest()
    return {
        "path": CANDIDATE_SOURCE.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "id_matches": values.get("id") == CANDIDATE_ID,
        "research_only": values.get("research_only") is True,
        "neutralize": values.get("neutralize"),
        "scoring": values.get("scoring"),
    }


def main() -> int:
    opener = build_opener(ProxyHandler({}))
    reload_status, reload_payload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_payload = _request(opener, "GET", "/api/factors")
    strategy_status, strategy_payload = _request(
        opener, "GET", "/api/strategies?include_research=true"
    )
    factors = factor_payload.get("factors", [])
    strategies = strategy_payload.get("strategies", [])
    errors = strategy_payload.get("load_errors", [])
    missing = sorted(set(FACTOR_IDS) - {row.get("id") for row in factors})
    base_strategy = next((row for row in strategies if row.get("id") == STRATEGY_ID), None)
    candidate = next((row for row in strategies if row.get("id") == CANDIDATE_ID), None)
    source_contract = _source_contract()
    checks = {
        "reload_http_ok": reload_status == 200,
        "reload_ok": bool(reload_payload.get("ok")),
        "factors_http_ok": factor_status == 200,
        "strategies_http_ok": strategy_status == 200,
        "base_strategy_registered": base_strategy is not None,
        "candidate_registered": candidate is not None,
        "candidate_research_only_in_api": bool(candidate and candidate.get("research_only")),
        "candidate_source_matches_contract": (
            source_contract["id_matches"]
            and source_contract["research_only"]
            and source_contract["neutralize"]
            == {"industry": "industry_l1", "shrink": 0.5}
        ),
        "referenced_factors_registered": not missing,
        "no_strategy_load_errors": not errors,
    }
    payload = {
        "round": "R271",
        "classification": "post_exposure_exploratory_registration_proof",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API_BASE,
        "registrar": {
            "path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "reload": {
            "method": "POST",
            "endpoint": "/api/strategies/reload",
            "http_status": reload_status,
            "ok": bool(reload_payload.get("ok")),
            "count": reload_payload.get("count"),
            "error": reload_payload.get("detail") or reload_payload.get("error"),
        },
        "factors": {
            "endpoint": "/api/factors",
            "http_status": factor_status,
            "count": len(factors),
            "expected_factor_count": len(FACTOR_IDS),
            "missing_factors": missing,
            "registered": [row for row in factors if row.get("id") in FACTOR_IDS],
        },
        "strategies": {
            "endpoint": "/api/strategies?include_research=true",
            "http_status": strategy_status,
            "count": len(strategies),
            "base_registered": base_strategy,
            "candidate_registered": candidate,
            "candidate_source_contract": source_contract,
            "load_error_count": len(errors),
            "load_errors": errors,
        },
        "checks": checks,
        "strategy_panel_ready": all(checks.values()),
        "all_checks_pass": all(checks.values()),
        "note": (
            "This is a post-result panel reload and candidate registration check, not a blind "
            "or independent confirmation. Neutralization is verified from the hashed strategy "
            "source because the strategies API omits META.neutralize from its response."
        ),
    }
    panel_path = EVIDENCE / "r271_corrected_panel_registration.json"
    panel_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result_path = EVIDENCE / "r271_shrink_neutralization_corrected.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    gates = result["decision"]["decision"]
    gates["P2"] = payload["strategy_panel_ready"]
    result["decision"]["all_pass_before_panel"] = all(
        value is True for key, value in gates.items() if key != "P2"
    )
    result["decision"]["all_pass"] = all(value is True for value in gates.values())
    result["decision"]["panel_reload"] = payload["strategy_panel_ready"]
    result["decision"]["passing_variants"] = (
        ["industry_shrink_05"] if result["decision"]["all_pass"] else []
    )
    result["panel_check_pending"] = False
    result["panel_registration"] = panel_path.relative_to(ROOT).as_posix()
    result["all_pass"] = result["decision"]["all_pass"]
    result["passing_variants"] = result["decision"]["passing_variants"]
    result["paper_trading_candidate"] = False
    result["provenance"]["corrected_panel_registration"] = {
        "path": panel_path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {
        "round": "R271",
        "captured_at": datetime.now(UTC).isoformat(),
        "classification": "post_exposure_exploratory_artifact_chain_audit",
        "initial_pre_run_preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_preregistration.json",
        "initial_pre_run_preregistration_sha256": "ce1d3edc7a61792bf3fb9065cb003f725e4b6a8476745b134b6c97e6b1a5b87a",
        "initial_pre_run_preregistration_archive": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_preregistration_exposed_original.json",
        "initial_result": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_shrink_neutralization.json",
        "initial_result_sha256": "e5d71226897f9f2e24e506f6bf9c7b32366376c9dbc394e9f7ddebaf11d7e364",
        "corrected_execution_spec": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_execution_spec.json",
        "corrected_execution_spec_sha256": hashlib.sha256(
            (EVIDENCE / "r271_execution_spec.json").read_bytes()
        ).hexdigest(),
        "corrected_result": result_path.relative_to(ROOT).as_posix(),
        "corrected_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "final_panel_evidence": panel_path.relative_to(ROOT).as_posix(),
        "final_panel_evidence_sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
        "interpretation": (
            "The first run used an implicit override and was not a valid test of the registered candidate. "
            "Its output and preregistration are preserved and hashed. The corrected run explicitly tests "
            "the research-only candidate through the same system backtest path, but both outputs had been "
            "seen; corrected results are exploratory and are not independent confirmation."
        ),
    }
    audit_path = EVIDENCE / "r271_artifact_chain_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    audit_path.with_suffix(".sha256").write_text(
        audit_hash + "  " + audit_path.name + "\n", encoding="ascii"
    )
    print(json.dumps({"panel_evidence": panel_path.relative_to(ROOT).as_posix(), "checks": checks, "all_checks_pass": payload["all_checks_pass"]}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
