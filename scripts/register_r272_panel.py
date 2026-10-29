#!/usr/bin/env python3
"""Capture pre-backtest R272 registration proof from the local system panel."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
API_BASE = "http://127.0.0.1:3018"
FACTOR_ID = "ext_alt_hs300_opm_operating_margin_yoy_change_q"
STRATEGY_IDS = [
    "ai_hs300_operating_margin_improvement_quality_h20_sl20",
    "ai_hs300_operating_margin_improvement_quality_control",
]
OUT = EVIDENCE / "r272_panel_registration.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(opener, method: str, endpoint: str) -> tuple[int, dict]:
    try:
        with opener.open(Request(API_BASE + endpoint, method=method), timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def _contract(strategy_id: str) -> dict:
    path = ROOT / "data/strategies/ai" / f"{strategy_id}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    meta = ast.literal_eval(assignments["META"])
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha256(path),
        "id_matches": meta.get("id") == strategy_id,
        "research_only": meta.get("research_only") is True,
        "scoring": meta.get("scoring"),
        "required_features": sorted(ast.literal_eval(assignments["REQUIRED_FEATURES"])),
    }


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 panel proof exists; refusing to overwrite it")
    audit_path = EVIDENCE / "r272_corrected_factor_audit_v2.json"
    audit_sidecar = audit_path.with_suffix(".json.sha256")
    if not audit_sidecar.exists() or audit_sidecar.read_text(encoding="ascii").split()[0] != _sha256(audit_path):
        raise RuntimeError("R272 factor audit is missing or checksum-invalid")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("all_checks_pass") is not True:
        raise RuntimeError("R272 factor audit failed; refusing panel registration step")

    opener = build_opener(ProxyHandler({}))
    reload_status, reload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_response = _request(opener, "GET", "/api/factors?asset_type=stock")
    strategy_status, strategy_response = _request(opener, "GET", "/api/strategies?include_research=true")
    factors = factor_response.get("factors", [])
    strategies = strategy_response.get("strategies", [])
    errors = strategy_response.get("load_errors", [])
    factor = next((item for item in factors if item.get("id") == FACTOR_ID), None)
    registered = {sid: next((item for item in strategies if item.get("id") == sid), None) for sid in STRATEGY_IDS}
    contracts = {sid: _contract(sid) for sid in STRATEGY_IDS}
    config_path = ROOT / "data/ext_data/alt_hs300_opm/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    checks = {
        "audit_passed_before_reload": True,
        "reload_http_ok": reload_status == 200,
        "reload_ok": reload.get("ok") is True,
        "factor_api_ok": factor_status == 200,
        "strategy_api_ok": strategy_status == 200,
        "canonical_factor_registered": bool(factor and factor.get("kind") == "base" and "stock" in factor.get("asset_types", [])),
        "candidate_and_control_registered": all(registered.values()),
        "both_strategies_research_only": all(row and row.get("research_only") for row in registered.values()),
        "source_contracts_match": all(item["id_matches"] and item["research_only"] for item in contracts.values()),
        "candidate_scores_canonical_factor": FACTOR_ID in contracts[STRATEGY_IDS[0]]["scoring"],
        "both_share_same_factor_availability_filter": all(FACTOR_ID in item["required_features"] for item in contracts.values()),
        "config_matches_factor": config.get("id") == "alt_hs300_opm" and any(item.get("name") == "operating_margin_yoy_change_q" and item.get("dtype") == "float" for item in config.get("fields", [])),
        "no_strategy_load_errors": not errors,
    }
    payload = {
        "round": "R272",
        "classification": "pre_backtest_system_panel_reload_and_registration_proof",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API_BASE,
        "registrar": {"path": Path(__file__).resolve().relative_to(ROOT).as_posix(), "sha256": _sha256(Path(__file__).resolve())},
        "factor_audit": {"path": audit_path.relative_to(ROOT).as_posix(), "sha256": _sha256(audit_path)},
        "reload": {"endpoint": "/api/strategies/reload", "http_status": reload_status, "response": reload},
        "factor": {"endpoint": "/api/factors?asset_type=stock", "http_status": factor_status, "registered": factor, "config_path": config_path.relative_to(ROOT).as_posix(), "config_sha256": _sha256(config_path)},
        "strategies": {"endpoint": "/api/strategies?include_research=true", "http_status": strategy_status, "registered": registered, "source_contracts": contracts, "load_error_count": len(errors), "load_errors": errors},
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "production_status": "pause_production",
        "outcome_data_read": False,
        "backtest_run": False,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"evidence": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "reload": reload, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
