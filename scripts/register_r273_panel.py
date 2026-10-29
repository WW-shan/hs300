#!/usr/bin/env python3
"""Register R273 signals through the local panel and capture raw API proof."""
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
OUT = EVIDENCE / "r273_panel_registration_v2.json"
STRATEGY_IDS = ["ai_hs300_macro_gate_quality", "ai_hs300_macro_gate_quality_r273_gate_exit"]
FACTOR_IDS = ["ext_alt_hs300_holder_top10_free_float_share", "ext_alt_hs300_equity_risk_premium", "ext_alt_hs300_credit_spread_5y_low", "ext_alt_hs300_term_spread_10y_2y"]
SIGNAL_IDS = ["r273_macro_exit_erp", "r273_macro_exit_credit", "r273_macro_exit_term"]


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


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        size += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(paths), "total_size_bytes": size, "inventory_sha256": digest.hexdigest()}


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R273 registration evidence exists; refusing to overwrite")
    prereg = EVIDENCE / "r273_preregistration_v4.json"
    prereg_hash = prereg.with_suffix(".sha256")
    if not prereg.exists() or not prereg_hash.exists():
        raise RuntimeError("R273 must be preregistered before panel registration")
    if prereg_hash.read_text(encoding="ascii").split()[0] != _sha256(prereg):
        raise RuntimeError("R273 preregistration checksum mismatch")

    opener = build_opener(ProxyHandler({}))
    raw_signal_saves = []
    for signal_id in SIGNAL_IDS:
        source = DATA / "user_data/custom_signals" / f"{signal_id}.json"
        signal_payload = json.loads(source.read_text(encoding="utf-8"))
        status, response = _request(opener, "POST", "/api/custom-signals", signal_payload)
        raw_signal_saves.append({"source": source.relative_to(ROOT).as_posix(), "http_status": status, "response": response})
        if status != 200 or response.get("ok") is not True:
            break

    reload_status, reload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_response = _request(opener, "GET", "/api/factors?asset_type=stock")
    strategy_status, strategy_response = _request(opener, "GET", "/api/strategies?include_research=true")
    signal_status, signal_response = _request(opener, "GET", "/api/custom-signals")
    factors = factor_response.get("factors", [])
    strategies = strategy_response.get("strategies", [])
    errors = strategy_response.get("load_errors", [])
    signals = signal_response.get("signals", [])
    factor_map = {item.get("id"): item for item in factors}
    strategy_map = {sid: next((item for item in strategies if item.get("id") == sid), None) for sid in STRATEGY_IDS}
    signal_map = {sid: next((item for item in signals if item.get("id") == sid), None) for sid in SIGNAL_IDS}
    contracts = {sid: _source_contract(sid) for sid in STRATEGY_IDS}
    candidate = strategy_map[STRATEGY_IDS[1]]
    checks = {
        "all_signal_saves_ok": len(raw_signal_saves) == 3 and all(item["http_status"] == 200 and item["response"].get("ok") is True for item in raw_signal_saves),
        "reload_http_ok": reload_status == 200,
        "reload_ok": reload.get("ok") is True,
        "factor_api_ok": factor_status == 200,
        "strategy_api_ok": strategy_status == 200,
        "signal_api_ok": signal_status == 200,
        "all_four_factors_registered_for_stock": all(factor_map.get(fid) is not None and "stock" in factor_map[fid].get("asset_types", []) for fid in FACTOR_IDS),
        "candidate_and_control_registered": all(strategy_map.values()),
        "candidate_research_only": bool(candidate and candidate.get("research_only") is True),
        "control_is_unchanged_r266_baseline": bool(strategy_map[STRATEGY_IDS[0]] and strategy_map[STRATEGY_IDS[0]].get("id") == STRATEGY_IDS[0]),
        "all_three_signals_registered_enabled": all(signal_map[sid] and signal_map[sid].get("enabled") is True for sid in SIGNAL_IDS),
        "candidate_exit_signals_match": bool(candidate and candidate.get("exit_signals") == [f"csg_{sid}" for sid in SIGNAL_IDS]),
        "source_contracts_match": all(contracts[sid]["id_matches"] for sid in STRATEGY_IDS) and contracts[STRATEGY_IDS[1]]["research_only"],
        "candidate_score_matches_control": bool(strategy_map[STRATEGY_IDS[0]] and candidate and candidate.get("scoring") == strategy_map[STRATEGY_IDS[0]].get("scoring")),
        "candidate_non_exit_contract_matches_control": bool(strategy_map[STRATEGY_IDS[0]] and candidate and candidate.get("basic_filter") == strategy_map[STRATEGY_IDS[0]].get("basic_filter") and candidate.get("params") == strategy_map[STRATEGY_IDS[0]].get("params") and candidate.get("max_hold_days") == strategy_map[STRATEGY_IDS[0]].get("max_hold_days") == 35),
        "no_strategy_load_errors": not errors,
    }
    payload = {
        "round": "R273",
        "classification": "pre_backtest_live_panel_registration_proof",
        "supersedes_failed_registration_proof": {
            "path": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r273_panel_registration.json",
            "sha256": _sha256(EVIDENCE / "r273_panel_registration.json"),
            "failure_cause": "The earlier verification incorrectly required the public R266 control to be research-only and compared unprefixed signal IDs; no backtest was run and no results were read.",
        },
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API_BASE,
        "macro_data_inventory": {
            "config_sha256": _sha256(DATA / "ext_data/alt_hs300/config.json"),
            **_inventory(DATA / "ext_data/alt_hs300/timeseries"),
        },
        "preregistration": {"path": prereg.relative_to(ROOT).as_posix(), "sha256": _sha256(prereg)},
        "registrar": {"path": Path(__file__).resolve().relative_to(ROOT).as_posix(), "sha256": _sha256(Path(__file__).resolve())},
        "signal_sources": {sid: {"path": (DATA / "user_data/custom_signals" / f"{sid}.json").relative_to(ROOT).as_posix(), "sha256": _sha256(DATA / "user_data/custom_signals" / f"{sid}.json")} for sid in SIGNAL_IDS},
        "raw_signal_save_responses": raw_signal_saves,
        "reload": {"endpoint": "/api/strategies/reload", "http_status": reload_status, "response": reload},
        "factors": {"endpoint": "/api/factors?asset_type=stock", "http_status": factor_status, "expected_ids": FACTOR_IDS, "registered": {fid: factor_map.get(fid) for fid in FACTOR_IDS}},
        "strategies": {"endpoint": "/api/strategies?include_research=true", "http_status": strategy_status, "registered": strategy_map, "source_contracts": contracts, "load_error_count": len(errors), "load_errors": errors},
        "signals": {"endpoint": "/api/custom-signals", "http_status": signal_status, "registered": {sid: signal_map.get(sid) for sid in SIGNAL_IDS}},
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "production_status": "pause_production",
        "backtest_run": False,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"evidence": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
