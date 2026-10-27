#!/usr/bin/env python3
"""Capture R266 strategy and factor registration evidence from the running panel."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "data"
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
)
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
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": str(exc)}
        return exc.code, payload
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def main() -> int:
    opener = build_opener(ProxyHandler({}))
    reload_status, reload_payload = _request(
        opener, "POST", "/api/strategies/reload"
    )
    factor_status, factor_payload = _request(opener, "GET", "/api/factors")
    strategy_status, strategy_payload = _request(
        opener, "GET", "/api/strategies?include_research=true"
    )

    factors = factor_payload.get("factors", [])
    strategies = strategy_payload.get("strategies", [])
    load_errors = strategy_payload.get("load_errors", [])
    factor_ids = {row.get("id") for row in factors}
    strategy_ids = {row.get("id") for row in strategies}
    missing_factors = sorted(set(FACTOR_IDS) - factor_ids)
    strategy_registered = STRATEGY_ID in strategy_ids
    strategy_panel_ready = (
        reload_status == 200
        and bool(reload_payload.get("ok"))
        and factor_status == 200
        and strategy_status == 200
        and strategy_registered
        and not missing_factors
        and not load_errors
    )
    checks = {
        "reload_http_ok": reload_status == 200,
        "reload_ok": bool(reload_payload.get("ok")),
        "factors_http_ok": factor_status == 200,
        "strategies_http_ok": strategy_status == 200,
        "strategy_registered": strategy_registered,
        "referenced_factors_registered": not missing_factors,
        "no_strategy_load_errors": not load_errors,
    }
    registered_strategy = next(
        (row for row in strategies if row.get("id") == STRATEGY_ID), None
    )
    payload = {
        "round": "R266",
        "captured_at": datetime.now(UTC).isoformat(),
        "api_base": API_BASE,
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
            "missing_factors": missing_factors,
            "registered": [
                row for row in factors if row.get("id") in FACTOR_IDS
            ],
        },
        "strategies": {
            "endpoint": "/api/strategies?include_research=true",
            "http_status": strategy_status,
            "count": len(strategies),
            "registered": registered_strategy,
            "load_error_count": len(load_errors),
            "load_errors": load_errors,
        },
        "checks": checks,
        "strategy_panel_ready": strategy_panel_ready,
        "all_checks_pass": all(checks.values()),
    }
    output = EVIDENCE / "r266_panel_registration.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result_path = EVIDENCE / "r266_a_leg_validation.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result.setdefault("decision", {})["P2"] = strategy_panel_ready
        result["panel_check_pending"] = False
        result["panel_registration"] = (
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
            "r266_panel_registration.json"
        )
        result["all_pass"] = all(result["decision"].values())
        result["paper_trading_candidate"] = result["all_pass"]
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "panel_evidence": str(output),
                "checks": checks,
                "strategy_panel_ready": strategy_panel_ready,
                "all_checks_pass": payload["all_checks_pass"],
                "r266_all_pass": (
                    result.get("all_pass") if result_path.exists() else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
