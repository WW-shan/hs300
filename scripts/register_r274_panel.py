#!/usr/bin/env python3
"""Register the R274 ablation in the local panel and retain raw API proof."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
API_BASE = "http://127.0.0.1:3018"
OUT = EVIDENCE / "r274_panel_registration.json"
STRATEGY_IDS = ["ai_hs300_macro_gate_quality", "ai_hs300_macro_gate_quality_r274_no_macro_gate"]
FACTOR_IDS = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(opener, method: str, endpoint: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(API_BASE + endpoint, data=body, method=method, headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def _source_contract(strategy_id: str) -> dict:
    path = DATA / "strategies/ai" / f"{strategy_id}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}
    meta = ast.literal_eval(assignments["META"])
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha256(path),
        "id_matches": meta.get("id") == strategy_id,
        "research_only": meta.get("research_only") is True,
        "required_features": sorted(ast.literal_eval(assignments["REQUIRED_FEATURES"])),
        "exit_signals": ast.literal_eval(assignments["EXIT_SIGNALS"]),
        "scoring": meta.get("scoring"),
    }


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R274 panel evidence exists; refusing overwrite")
    prereg = EVIDENCE / "r274_preregistration.json"
    if not prereg.is_file() or not prereg.with_suffix(".sha256").is_file():
        raise RuntimeError("R274 preregistration must be frozen before registration")
    if prereg.with_suffix(".sha256").read_text(encoding="ascii").split()[0] != _sha256(prereg):
        raise RuntimeError("R274 preregistration checksum mismatch")
    opener = build_opener(ProxyHandler({}))
    reload_status, reload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_response = _request(opener, "GET", "/api/factors?asset_type=stock")
    strategy_status, strategy_response = _request(opener, "GET", "/api/strategies?include_research=true")
    factors = factor_response.get("factors", [])
    strategies = strategy_response.get("strategies", [])
    errors = strategy_response.get("load_errors", [])
    factor_map = {item.get("id"): item for item in factors}
    strategy_map = {sid: next((item for item in strategies if item.get("id") == sid), None) for sid in STRATEGY_IDS}
    contracts = {sid: _source_contract(sid) for sid in STRATEGY_IDS}
    control, candidate = (strategy_map[sid] for sid in STRATEGY_IDS)
    checks = {
        "reload_http_ok": reload_status == 200,
        "reload_ok": reload.get("ok") is True,
        "factor_api_ok": factor_status == 200,
        "strategy_api_ok": strategy_status == 200,
        "all_four_scoring_factors_registered_for_stock": all(factor_map.get(fid) is not None and "stock" in factor_map[fid].get("asset_types", []) for fid in FACTOR_IDS),
        "candidate_and_control_registered": all(strategy_map.values()),
        "candidate_research_only": bool(candidate and candidate.get("research_only") is True),
        "control_is_r266_baseline": bool(control and control.get("id") == STRATEGY_IDS[0]),
        "source_contracts_match": all(contracts[sid]["id_matches"] for sid in STRATEGY_IDS) and contracts[STRATEGY_IDS[1]]["research_only"],
        "candidate_score_matches_control": bool(control and candidate and candidate.get("scoring") == control.get("scoring")),
        "candidate_filter_only_removes_macro_gates": bool(control and candidate and candidate.get("basic_filter") == control.get("basic_filter") and candidate.get("params") == [] and candidate.get("max_hold_days") == control.get("max_hold_days") == 35 and candidate.get("exit_signals") == []),
        "no_strategy_load_errors": not errors,
    }
    prereg_digest = _sha256(prereg)
    payload = {
        "round": "R274",
        "classification": "pre_backtest_live_panel_registration_proof",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API_BASE,
        "preregistration": {"path": prereg.relative_to(ROOT).as_posix(), "sha256": prereg_digest},
        "registrar": {"path": Path(__file__).resolve().relative_to(ROOT).as_posix(), "sha256": _sha256(Path(__file__).resolve())},
        "reload": {"endpoint": "/api/strategies/reload", "http_status": reload_status, "response": reload},
        "factors": {"endpoint": "/api/factors?asset_type=stock", "http_status": factor_status, "expected_ids": FACTOR_IDS, "registered": {fid: factor_map.get(fid) for fid in FACTOR_IDS}},
        "strategies": {"endpoint": "/api/strategies?include_research=true", "http_status": strategy_status, "registered": strategy_map, "source_contracts": contracts, "load_error_count": len(errors), "load_errors": errors},
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "production_status": "pause_production",
        "backtest_run": False,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"evidence": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "preregistration_sha256": prereg_digest, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
