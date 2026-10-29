#!/usr/bin/env python3
"""Register and verify the R275 gate-isolation family through the live panel."""
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
API = "http://127.0.0.1:3018"
OUT = EVIDENCE / "r275_panel_registration_v2.json"
CONTROL = "ai_hs300_macro_gate_quality"
CANDIDATES = {
    "erp": "ai_hs300_macro_gate_quality_r275_remove_erp",
    "credit": "ai_hs300_macro_gate_quality_r275_remove_credit",
    "term": "ai_hs300_macro_gate_quality_r275_remove_term",
}
REGISTRATION_VERSION = "r275-panel-registrar-v2"
FACTORS = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
]
MACRO = {
    "erp": "ext_alt_hs300_equity_risk_premium",
    "credit": "ext_alt_hs300_credit_spread_5y_low",
    "term": "ext_alt_hs300_term_spread_10y_2y",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(opener, method: str, endpoint: str) -> tuple[int, dict]:
    request = Request(API + endpoint, method=method)
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


def _source(strategy_id: str) -> dict:
    path = DATA / "strategies/ai" / f"{strategy_id}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha(path),
        "meta": ast.literal_eval(assignments["META"]),
        "required_features": ast.literal_eval(assignments["REQUIRED_FEATURES"]),
        "filter_ast": next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "filter"),
    }


def _accepted(key: str, values: dict[str, float]) -> bool:
    erp = values[MACRO["erp"]] >= 5.5
    credit = values[MACRO["credit"]] >= -0.8
    term = values[MACRO["term"]] >= 0.3
    accepted = {"erp": credit and term, "credit": erp and term, "term": erp and credit}
    return accepted[key]


def main() -> int:
    prereg_path = EVIDENCE / "r275_preregistration_v2.json"
    sidecar = prereg_path.with_suffix(".sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != _sha(prereg_path):
        raise RuntimeError("R275 preregistration checksum mismatch")
    registrar_hash = _sha(Path(__file__).resolve())
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R275 v2 panel evidence exists; refusing overwrite")
    sidecar = prereg_path.with_suffix(".sha256")
    if not prereg_path.is_file() or sidecar.read_text(encoding="ascii").split()[0] != _sha(prereg_path):
        raise RuntimeError("R275 frozen preregistration is missing or invalid")
    opener = build_opener(ProxyHandler({}))
    reload_status, reload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_response = _request(opener, "GET", "/api/factors?asset_type=stock")
    strategy_status, strategy_response = _request(opener, "GET", "/api/strategies?include_research=true")
    factor_map = {item.get("id"): item for item in factor_response.get("factors", [])}
    strategy_map = {item.get("id"): item for item in strategy_response.get("strategies", [])}
    expected_ids = [CONTROL, *CANDIDATES.values()]
    required_present = all(factor_map.get(fid) is not None and "stock" in factor_map[fid].get("asset_types", []) for fid in FACTORS)
    candidates_present = all(strategy_map.get(sid) is not None for sid in expected_ids)
    comparisons = {}
    contracts = {}
    for key, strategy_id in CANDIDATES.items():
        candidate = _source(strategy_id)
        control_source = _source(CONTROL)
        contracts[key] = {
            "candidate_sha256": candidate["sha256"],
            "research_only": candidate["meta"].get("research_only") is True,
            "same_scoring": candidate["meta"].get("scoring") == control_source["meta"].get("scoring"),
            "same_basic_filter": candidate["meta"].get("basic_filter") == control_source["meta"].get("basic_filter"),
            "has_only_expected_macro_required_features": set(candidate["required_features"]) == set(control_source["required_features"]) - {MACRO[key]},
        }
        scenarios = {
            "all_pass": ({MACRO["erp"]: 6.0, MACRO["credit"]: -0.5, MACRO["term"]: 0.5}, {"erp", "credit", "term"}),
            "only_erp_blocked": ({MACRO["erp"]: 4.0, MACRO["credit"]: -0.5, MACRO["term"]: 0.5}, {"erp"}),
            "only_credit_blocked": ({MACRO["erp"]: 6.0, MACRO["credit"]: -0.9, MACRO["term"]: 0.5}, {"credit"}),
            "only_term_blocked": ({MACRO["erp"]: 6.0, MACRO["credit"]: -0.5, MACRO["term"]: 0.2}, {"term"}),
            "erp_credit_blocked": ({MACRO["erp"]: 4.0, MACRO["credit"]: -0.9, MACRO["term"]: 0.5}, set()),
            "erp_term_blocked": ({MACRO["erp"]: 4.0, MACRO["credit"]: -0.5, MACRO["term"]: 0.2}, set()),
            "credit_term_blocked": ({MACRO["erp"]: 6.0, MACRO["credit"]: -0.9, MACRO["term"]: 0.2}, set()),
            "all_blocked": ({MACRO["erp"]: 4.0, MACRO["credit"]: -0.9, MACRO["term"]: 0.2}, set()),
        }
        for row_name, (macro_values, expected_keys) in scenarios.items():
            expected = key in expected_keys
            comparisons.setdefault(row_name, {})[key] = {"accepted": _accepted(key, macro_values), "expected": expected}
    for key in comparisons:
        comparisons[key]["all_three_expected"] = all(value["accepted"] == value["expected"] for value in comparisons[key].values())
    load_errors = strategy_response.get("load_errors", [])
    checks = {
        "reload_http_ok": reload_status == 200 and reload.get("ok") is True,
        "factor_api_ok": factor_status == 200,
        "strategy_api_ok": strategy_status == 200,
        "all_four_scoring_factors_registered": required_present,
        "control_and_all_candidates_registered": candidates_present,
        "all_candidates_research_only": all(strategy_map.get(sid, {}).get("research_only") is True for sid in CANDIDATES.values()),
        "source_contracts_match": all(all(value.values()) for value in contracts.values()),
        "synthetic_gate_truth_table_passes": all(row["all_three_expected"] for row in comparisons.values()),
        "zero_strategy_load_errors": not load_errors,
    }
    payload = {
        "round": "R275",
        "registration_version": REGISTRATION_VERSION,
        "classification": "pre_backtest_live_panel_registration_proof",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API,
        "preregistration_sha256": _sha(prereg_path),
        "registrar_sha256": registrar_hash,
        "reload": {"http_status": reload_status, "response": reload},
        "factors": {"http_status": factor_status, "expected": FACTORS, "registered": {fid: factor_map.get(fid) for fid in FACTORS}},
        "strategies": {"http_status": strategy_status, "expected": expected_ids, "registered": {sid: strategy_map.get(sid) for sid in expected_ids}, "load_errors": load_errors, "source_contracts": contracts},
        "gate_truth_table": comparisons,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "backtest_run": False,
        "production_status": "pause_production",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"evidence": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
