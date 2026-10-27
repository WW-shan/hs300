#!/usr/bin/env python3
"""Reload and capture the R261 factor/strategy panel registration evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
)
OUT = EVIDENCE / "r261_panel_registration.json"
API_BASE = "http://127.0.0.1:3018"
FACTOR_IDS = {"ext_alt_hs300_qlot_quality_low_lottery_min_rank"}
STRATEGY_IDS = {
    "ai_hs300_qrs_low_lottery_h20_sl20",
    "ai_hs300_qrs_low_lottery_control",
}


def _request(opener, method: str, path: str):
    request = Request(API_BASE + path, method=method)
    with opener.open(request, timeout=60) as response:
        return response.status, json.load(response)


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
    registered_factors = [row for row in factors if row.get("id") in FACTOR_IDS]
    registered_strategies = [
        row for row in strategies if row.get("id") in STRATEGY_IDS
    ]
    missing_factors = sorted(FACTOR_IDS - {row.get("id") for row in factors})
    missing_strategies = sorted(
        STRATEGY_IDS - {row.get("id") for row in strategies}
    )
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
        "round": "R261",
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
            "expected_factor_count": len(FACTOR_IDS),
            "missing_factors": missing_factors,
            "registered": registered_factors,
        },
        "strategies": {
            "endpoint": "/api/strategies?include_research=true",
            "http_status": strategy_status,
            "count": len(strategies),
            "expected_strategy_count": len(STRATEGY_IDS),
            "missing_strategies": missing_strategies,
            "load_error_count": len(load_errors),
            "load_errors": load_errors,
            "registered": registered_strategies,
        },
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "note": (
            "R261 factor and both candidate/control strategies are registered in "
            "the running panel before the final strategy backtest is read."
        ),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "factor_count": len(factors),
                "strategy_count": len(strategies),
                "missing_factors": missing_factors,
                "missing_strategies": missing_strategies,
                "load_error_count": len(load_errors),
                "checks": checks,
                "all_checks_pass": payload["all_checks_pass"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
