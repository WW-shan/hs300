#!/usr/bin/env python3
"""Capture panel registration evidence for R262, R263 and R264."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
API_BASE = "http://127.0.0.1:3018"

ROUNDS = {
    "R262": {
        "strategies": [
            "ai_hs300_quality_relative_strength_h20_sl20",
            "ai_hs300_quality_relative_strength_control",
            "ai_hs300_qrs_tp20_h20_sl20",
            "ai_hs300_qrs_tp30_h20_sl20",
            "ai_hs300_qrs_tp40_h20_sl20",
            "ai_hs300_qrs_h10_sl20",
        ],
        "factors": [
            "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
        ],
    },
    "R263": {
        "strategies": [
            "ai_hs300_macro_gate_quality",
            "ai_hs300_industry_relative_strength_quality_h20_sl20",
            "ai_hs300_quality_relative_strength_h20_sl20",
            "ai_hs300_qrs_tp20_h20_sl20",
        ],
        "factors": [
            "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
            "ext_alt_hs300_holder_top10_free_float_share",
            "ext_alt_hs300_equity_risk_premium",
            "ext_alt_hs300_credit_spread_5y_low",
            "ext_alt_hs300_term_spread_10y_2y",
        ],
    },
    "R264": {
        "strategies": [
            "ai_hs300_macro_gate_quality",
            "ai_hs300_industry_relative_strength_quality_h20_sl20",
        ],
        "factors": [
            "ext_alt_hs300_holder_top10_free_float_share",
            "ext_alt_hs300_equity_risk_premium",
            "ext_alt_hs300_credit_spread_5y_low",
            "ext_alt_hs300_term_spread_10y_2y",
            "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
        ],
    },
}


def _request(opener, method: str, path: str):
    request = Request(API_BASE + path, method=method)
    with opener.open(request, timeout=60) as response:
        return response.status, json.load(response)


def main() -> int:
    opener = build_opener(ProxyHandler({}))
    reload_status, reload_payload = _request(opener, "POST", "/api/strategies/reload")
    factor_status, factor_payload = _request(opener, "GET", "/api/factors")
    strategy_status, strategy_payload = _request(
        opener, "GET", "/api/strategies?include_research=true"
    )
    factors = factor_payload.get("factors", [])
    strategies = strategy_payload.get("strategies", [])
    load_errors = strategy_payload.get("load_errors", [])
    factor_ids = {row.get("id") for row in factors}
    strategy_ids = {row.get("id") for row in strategies}

    for round_name, spec in ROUNDS.items():
        expected_factors = set(spec["factors"])
        expected_strategies = set(spec["strategies"])
        missing_factors = sorted(expected_factors - factor_ids)
        missing_strategies = sorted(expected_strategies - strategy_ids)
        registered_factors = [row for row in factors if row.get("id") in expected_factors]
        registered_strategies = [row for row in strategies if row.get("id") in expected_strategies]
        checks = {
            "reload_http_ok": reload_status == 200,
            "reload_ok": bool(reload_payload.get("ok")),
            "factors_http_ok": factor_status == 200,
            "strategies_http_ok": strategy_status == 200,
            "factor_registered": not missing_factors,
            "strategies_registered": not missing_strategies,
            "no_strategy_load_errors": len(load_errors) == 0,
        }
        payload = {
            "round": round_name,
            "captured_at": datetime.now(UTC).isoformat(),
            "api_base": API_BASE,
            "reload": {
                "method": "POST",
                "endpoint": "/api/strategies/reload",
                "http_status": reload_status,
                "ok": bool(reload_payload.get("ok")),
                "count": reload_payload.get("count"),
            },
            "factors": {
                "endpoint": "/api/factors",
                "http_status": factor_status,
                "count": len(factors),
                "expected_factor_count": len(expected_factors),
                "missing_factors": missing_factors,
                "registered": registered_factors,
            },
            "strategies": {
                "endpoint": "/api/strategies?include_research=true",
                "http_status": strategy_status,
                "count": len(strategies),
                "expected_strategy_count": len(expected_strategies),
                "missing_strategies": missing_strategies,
                "load_error_count": len(load_errors),
                "load_errors": load_errors,
                "registered": [
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "research_only": row.get("research_only"),
                        "take_profit": row.get("take_profit"),
                        "max_hold_days": row.get("max_hold_days"),
                        "stop_loss": row.get("stop_loss"),
                    }
                    for row in registered_strategies
                ],
            },
            "checks": checks,
            "all_checks_pass": all(checks.values()),
        }
        out = EVIDENCE / f"{round_name.lower()}_panel_registration.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(round_name, "saved", out, "all_checks_pass=", payload["all_checks_pass"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
