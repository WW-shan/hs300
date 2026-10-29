#!/usr/bin/env python3
"""Reload the strategy panel and capture R271 registration evidence."""

from __future__ import annotations

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
FACTOR_IDS = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
]


def _request(opener, method: str, path: str) -> tuple[int, dict]:
    request = Request(API_BASE + path, method=method)
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {"error": str(exc)}
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


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
    strategy = next((row for row in strategies if row.get("id") == STRATEGY_ID), None)
    checks = {
        "reload_http_ok": reload_status == 200,
        "reload_ok": bool(reload_payload.get("ok")),
        "factors_http_ok": factor_status == 200,
        "strategies_http_ok": strategy_status == 200,
        "strategy_registered": strategy is not None,
        "referenced_factors_registered": not missing,
        "no_strategy_load_errors": not errors,
    }
    payload = {
        "round": "R271",
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
            "registered": strategy,
            "load_error_count": len(errors),
            "load_errors": errors,
        },
        "checks": checks,
        "strategy_panel_ready": all(checks.values()),
        "all_checks_pass": all(checks.values()),
        "note": (
            "R271 adds no persistent strategy or factor. The frozen industry/shrink override "
            "is exercised through StrategyBacktestService -> BacktestEngine; this reload "
            "confirms the base strategy and its referenced factors remain available."
        ),
    }
    panel_path = EVIDENCE / "r271_panel_registration.json"
    panel_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result_path = EVIDENCE / "r271_shrink_neutralization.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        decision = result.setdefault("decision", {})
        decision["panel_reload"] = payload["strategy_panel_ready"]
        gate_decision = decision.get("decision", {})
        gate_decision["P2"] = payload["strategy_panel_ready"]
        result["decision"]["all_pass_before_panel"] = all(
            value is True for key, value in gate_decision.items() if key != "P2"
        )
        result["panel_check_pending"] = False
        result["panel_registration"] = panel_path.relative_to(ROOT).as_posix()
        result["decision"]["all_pass"] = all(
            value is True for value in gate_decision.values()
        )
        result["all_pass"] = result["decision"]["all_pass"]
        result["passing_variants"] = (
            ["industry_shrink_05"] if result["all_pass"] else []
        )
        result["paper_trading_candidate"] = False
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(
        json.dumps(
            {
                "panel_evidence": panel_path.relative_to(ROOT).as_posix(),
                "reload": payload["reload"],
                "checks": checks,
                "strategy_panel_ready": payload["strategy_panel_ready"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
