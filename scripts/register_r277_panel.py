#!/usr/bin/env python3
"""Capture live panel registration proof for the frozen R277 audit inputs."""
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
OUT = EVIDENCE / "r277_panel_registration.json"
CONTROL = "ai_hs300_macro_gate_quality"
CANDIDATES = (
    "ai_hs300_macro_gate_quality_r275_remove_erp",
    "ai_hs300_macro_gate_quality_r275_remove_credit",
    "ai_hs300_macro_gate_quality_r275_remove_term",
)
FACTORS = (
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
)
RESULT = EVIDENCE / "r277_r275_active_return_statistics_v2.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(opener, method: str, endpoint: str) -> tuple[int, dict]:
    req = Request(API + endpoint, method=method)
    try:
        with opener.open(req, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


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
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha(path),
        "research_only": meta.get("research_only") is True,
    }


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R277 panel evidence exists; refusing overwrite")
    if not RESULT.is_file() or not RESULT.with_suffix(".json.sha256").is_file():
        raise RuntimeError("R277 finalized audit evidence is missing")
    if RESULT.with_suffix(".json.sha256").read_text(encoding="ascii").split()[0] != sha(RESULT):
        raise RuntimeError("R277 audit checksum mismatch")

    opener = build_opener(ProxyHandler({}))
    reload_status, reload_response = request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_response = request(opener, "GET", "/api/factors?asset_type=stock")
    strategy_status, strategy_response = request(
        opener, "GET", "/api/strategies?include_research=true"
    )
    factor_map = {item.get("id"): item for item in factor_response.get("factors", [])}
    strategy_map = {
        item.get("id"): item for item in strategy_response.get("strategies", [])
    }
    strategy_ids = (CONTROL, *CANDIDATES)
    sources = {strategy_id: strategy_source(strategy_id) for strategy_id in strategy_ids}
    load_errors = strategy_response.get("load_errors", [])
    checks = {
        "reload_succeeded": reload_status == 200 and reload_response.get("ok") is True,
        "factor_api_succeeded": factor_status == 200,
        "strategy_api_succeeded": strategy_status == 200,
        "all_required_strategies_registered": all(
            strategy_id in strategy_map for strategy_id in strategy_ids
        ),
        "all_candidates_are_research_only_in_panel": all(
            strategy_map.get(strategy_id, {}).get("research_only") is True
            for strategy_id in CANDIDATES
        ),
        "all_candidate_source_files_are_research_only": all(
            sources[strategy_id]["research_only"] for strategy_id in CANDIDATES
        ),
        "all_four_scoring_factors_registered_for_stock": all(
            factor_map.get(factor_id) is not None
            and "stock" in factor_map[factor_id].get("asset_types", [])
            for factor_id in FACTORS
        ),
        "zero_strategy_load_errors": not load_errors,
    }
    payload = {
        "round": "R277",
        "classification": "posthoc_audit_panel_registration_proof",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API,
        "audit_result_sha256": sha(RESULT),
        "reload": {"http_status": reload_status, "response": reload_response},
        "factors": {
            "http_status": factor_status,
            "expected_ids": FACTORS,
            "registered": {key: factor_map.get(key) for key in FACTORS},
            "total": len(factor_map),
        },
        "strategies": {
            "http_status": strategy_status,
            "expected_ids": strategy_ids,
            "registered": {key: strategy_map.get(key) for key in strategy_ids},
            "source_files": sources,
            "total": len(strategy_map),
            "load_errors": load_errors,
        },
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "backtest_run": False,
        "production_status": "pause_production",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = sha(OUT)
    OUT.with_suffix(".json.sha256").write_text(
        f"{digest}  {OUT.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "evidence": OUT.relative_to(ROOT).as_posix(),
                "sha256": digest,
                "checks": checks,
                "strategy_count": len(strategy_map),
                "factor_count": len(factor_map),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
