#!/usr/bin/env python3
"""Read-only R278 data-readiness audit through the system's registered provider."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.data_providers import custom as custom_sources  # noqa: E402

DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
OUT = EVIDENCE / "r278_readiness_audit.json"
PANEL_OUT = EVIDENCE / "r278_panel_registration.json"
PREREG = EVIDENCE / "r278_preregistration.json"
PREREG_SHA_FILE = EVIDENCE / "r278_preregistration.sha256"
API = "http://127.0.0.1:3018"
SAMPLES = ("600519.SH", "000001.SZ", "300750.SZ")
CONTROL = "ai_hs300_macro_gate_quality"
CANDIDATE = "ai_hs300_macro_gate_quality_r275_remove_term"
FACTORS = (
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
)
CN = ZoneInfo("Asia/Shanghai")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def api_get(path: str) -> tuple[int, dict]:
    import requests

    response = requests.get(API + path, timeout=30, proxies={"http": None, "https": None})
    try:
        payload = response.json()
    except ValueError:
        payload = {"non_json": response.text[:500]}
    return response.status_code, payload


def latest_capture() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    if any(path.exists() for path in (
        OUT, OUT.with_suffix(".json.sha256"), PANEL_OUT, PANEL_OUT.with_suffix(".json.sha256"),
        PREREG_SHA_FILE,
    )):
        raise RuntimeError("R278 evidence exists; refusing overwrite")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    prereg_sha = sha_file(PREREG)
    captured_started = latest_capture()
    provider = custom_sources.get_provider("akshare")

    captured_quotes_at = latest_capture()
    quote_records = provider.get_realtime()
    captured_quotes_done = latest_capture()
    quote_map = {row.get("symbol"): row for row in quote_records}
    canonical_quotes = json.dumps(
        quote_records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    captured_minute_at = latest_capture()
    minute_frame = provider.get_minute(
        list(SAMPLES),
        start_time=datetime.now(CN).replace(tzinfo=None) - timedelta(days=5),
        end_time=datetime.now(CN).replace(tzinfo=None),
        asset_type="stock",
        freq="1m",
    )
    captured_minute_done = latest_capture()
    minute_rows = minute_frame.to_dicts()
    minute_by_symbol: dict[str, list[dict]] = {symbol: [] for symbol in SAMPLES}
    for row in minute_rows:
        stamp = row.get("datetime")
        if isinstance(stamp, datetime) and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=CN)
        minute_by_symbol.setdefault(row["symbol"], []).append(
            {**row, "datetime": stamp}
        )

    daily_by_symbol: dict[str, dict] = {}
    daily_started = latest_capture()
    for symbol in SAMPLES:
        frame = provider.get_daily(
            [symbol],
            start_time=datetime.now(CN).date() - timedelta(days=20),
            end_time=datetime.now(CN).date(),
            asset_type="stock",
        )
        daily_by_symbol[symbol] = {
            "rows": frame.height,
            "first_date": str(frame["date"].min()) if frame.height else None,
            "last_date": str(frame["date"].max()) if frame.height else None,
            "sha256": sha_bytes(
                json.dumps(frame.to_dicts(), sort_keys=True, default=str, separators=(",", ":")).encode()
            ),
        }
    daily_done = latest_capture()
    calendar_started = latest_capture()
    trading_days = provider.trading_days()
    now_local = datetime.now(CN)
    today_limit = now_local.date() if now_local.time().isoformat() >= "15:30:00" else now_local.date() - timedelta(days=1)
    completed_trading_days = sorted(day for day in trading_days if day <= today_limit)
    latest_completed_day = completed_trading_days[-1].isoformat() if completed_trading_days else None
    calendar_done = latest_capture()

    panel_started = latest_capture()
    # Use read-only panel endpoints; candidate and factors were registered in R277.
    strategy_status, strategy_response = api_get("/api/strategies?include_research=true")
    factor_status, factor_response = api_get("/api/factors?asset_type=stock")
    capability_status, capability_response = api_get("/api/settings/capability-matrix")
    sources_status, sources_response = api_get("/api/settings/data-sources")
    overview_status, overview_response = api_get("/api/overview/market")
    accounts_status, accounts_response = api_get("/api/paper/accounts")
    panel_done = latest_capture()

    candidates = {
        item.get("id"): item for item in strategy_response.get("strategies", [])
    }
    factors = {item.get("id"): item for item in factor_response.get("factors", [])}
    capability_rows = capability_response.get("capabilities", [])
    realtime_capability = next((item for item in capability_rows if item.get("id") == "realtime"), {})
    akshare_plugins = [
        item for item in sources_response.get("plugins", []) if item.get("name") == "akshare"
    ]

    samples: dict[str, dict] = {}
    quote_minute_ok = True
    for symbol in SAMPLES:
        quote = quote_map.get(symbol)
        rows = minute_by_symbol.get(symbol, [])
        same_day = [row for row in rows if row["datetime"].date() == now_local.date()]
        latest_minute = max(same_day, key=lambda row: row["datetime"]) if same_day else None
        gap_minutes = (now_local - latest_minute["datetime"]).total_seconds() / 60 if latest_minute else None
        price_gap = (
            abs(float(quote["last_price"]) / float(latest_minute["close"]) - 1.0)
            if quote and latest_minute and latest_minute.get("close")
            else None
        )
        limit_minutes = 35 if 11 <= now_local.hour < 13 or now_local.hour >= 15 else 15
        valid = bool(
            quote
            and latest_minute
            and gap_minutes is not None
            and gap_minutes <= limit_minutes
            and price_gap is not None
            and price_gap <= 0.005
        )
        quote_minute_ok = quote_minute_ok and valid
        samples[symbol] = {
            "quote": quote,
            "minute_rows_in_window": len(rows),
            "latest_minute": (
                {**latest_minute, "datetime": latest_minute["datetime"].isoformat()}
                if latest_minute
                else None
            ),
            "minute_age_minutes_at_capture": round(gap_minutes, 3) if gap_minutes is not None else None,
            "quote_minute_relative_price_gap": round(price_gap, 8) if price_gap is not None else None,
            "quote_minute_anchor_pass": valid,
            "daily": daily_by_symbol[symbol],
        }

    daily_fresh = bool(
        latest_completed_day
        and all(item["last_date"] == latest_completed_day for item in daily_by_symbol.values())
    )
    strategy_pair_ok = (
        strategy_status == 200
        and CONTROL in candidates
        and CANDIDATE in candidates
        and candidates[CANDIDATE].get("research_only") is True
        and not strategy_response.get("load_errors")
    )
    source_path = DATA / "strategies/ai" / f"{CANDIDATE}.py"
    source_text = source_path.read_text(encoding="utf-8")
    source_is_research_only = "\"research_only\": True" in source_text
    factors_registered = all(
        factor_id in factors and "stock" in factors[factor_id].get("asset_types", [])
        for factor_id in FACTORS
    )
    quote_status = overview_response.get("quote_status", {})
    realtime_capable = realtime_capability.get("usable") is True and realtime_capability.get("effective") == "akshare"
    panel_registration_pass = strategy_pair_ok and source_is_research_only and factors_registered
    prospective_collection_ready = False
    panel_proof = {
        "round": "R278",
        "classification": "read_only_panel_registration_proof_for_frozen_candidate",
        "captured_at_utc": panel_done.isoformat(),
        "api_base": API,
        "preregistration_sha256": prereg_sha,
        "strategies_http_status": strategy_status,
        "strategy_count": len(candidates),
        "control_id": CONTROL,
        "candidate_id": CANDIDATE,
        "control_registered": CONTROL in candidates,
        "candidate_registered": CANDIDATE in candidates,
        "candidate_research_only": candidates.get(CANDIDATE, {}).get("research_only") is True,
        "candidate_source_research_only": source_is_research_only,
        "candidate_source_sha256": sha_file(source_path),
        "strategy_load_errors": strategy_response.get("load_errors", []),
        "factors_http_status": factor_status,
        "factor_count": len(factors),
        "expected_factors_registered": {factor_id: factor_id in factors for factor_id in FACTORS},
        "capability_http_status": capability_status,
        "realtime_capability": realtime_capability,
        "akshare_plugin_http_status": sources_status,
        "akshare_plugin_available": bool(akshare_plugins and akshare_plugins[0].get("available")),
        "panel_registration_pass": panel_registration_pass,
        "reload_performed": False,
        "backtest_run": False,
        "production_status": "pause_production",
    }
    PANEL_OUT.write_text(json.dumps(panel_proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    panel_proof_sha = sha_file(PANEL_OUT)
    PANEL_OUT.with_suffix(".json.sha256").write_text(
        f"{panel_proof_sha}  {PANEL_OUT.name}\n", encoding="ascii"
    )
    criteria = {
        "panel_registration": panel_registration_pass,
        "quote_time_anchor": quote_minute_ok,
        "daily_freshness": daily_fresh,
        "prospective_collection": prospective_collection_ready,
        "all_pass": panel_registration_pass and quote_minute_ok and daily_fresh and prospective_collection_ready,
    }

    payload = {
        "round": "R278",
        "classification": "preregistered_read_only_prospective_data_readiness_audit",
        "captured_at": {
            "started_utc": captured_started.isoformat(),
            "finished_utc": latest_capture().isoformat(),
            "quotes_started_utc": captured_quotes_at.isoformat(),
            "quotes_finished_utc": captured_quotes_done.isoformat(),
            "minute_started_utc": captured_minute_at.isoformat(),
            "minute_finished_utc": captured_minute_done.isoformat(),
            "daily_started_utc": daily_started.isoformat(),
            "daily_finished_utc": daily_done.isoformat(),
            "calendar_started_utc": calendar_started.isoformat(),
            "calendar_finished_utc": calendar_done.isoformat(),
            "panel_started_utc": panel_started.isoformat(),
            "panel_finished_utc": panel_done.isoformat(),
            "local_beijing_time_at_decision": now_local.isoformat(),
        },
        "preregistration_sha256": sha_file(PREREG),
        "panel_registration_proof_sha256": panel_proof_sha,
        "candidate_source": {
            "path": source_path.relative_to(ROOT).as_posix(),
            "sha256": sha_file(source_path),
            "research_only": source_is_research_only,
        },
        "provider": "akshare",
        "provider_data": {
            "quote_rows": len(quote_records),
            "quote_timestamp_non_null_rows": sum(row.get("timestamp") is not None for row in quote_records),
            "quote_payload_sha256": sha_bytes(canonical_quotes),
            "samples": samples,
            "minute_rows": minute_frame.height,
            "daily_samples": daily_by_symbol,
            "trading_calendar_rows": len(trading_days),
            "latest_completed_trading_day": latest_completed_day,
            "daily_all_samples_reach_latest_completed_day": daily_fresh,
        },
        "panel": {
            "api_base": API,
            "strategies_http_status": strategy_status,
            "strategy_count": len(candidates),
            "control_registered": CONTROL in candidates,
            "candidate_registered": CANDIDATE in candidates,
            "candidate_research_only": candidates.get(CANDIDATE, {}).get("research_only") is True,
            "candidate_source_research_only": source_is_research_only,
            "strategy_load_errors": strategy_response.get("load_errors", []),
            "factor_http_status": factor_status,
            "factor_count": len(factors),
            "expected_factors_registered": {factor_id: factor_id in factors for factor_id in FACTORS},
            "capability_http_status": capability_status,
            "realtime_capability": realtime_capability,
            "akshare_plugin_http_status": sources_status,
            "akshare_plugins": akshare_plugins,
            "overview_http_status": overview_status,
            "quote_status": quote_status,
            "paper_accounts_http_status": accounts_status,
            "paper_account_count": len(accounts_response.get("accounts", [])),
            "panel_registration_pass": panel_registration_pass,
            "realtime_capable_but_background_polling_disabled": realtime_capable and quote_status.get("enabled") is False and quote_status.get("running") is False,
        },
        "readiness_criteria": criteria,
        "decision": (
            "ready_for_prospective_observation_only"
            if criteria["all_pass"]
            else "data_readiness_hold_no_backtest_no_deployment"
        ),
        "interpretation": [
            "Local capture time is recorded independently and must not be mistaken for a vendor publication timestamp.",
            "The full-market realtime endpoint returns a large snapshot but omits vendor timestamps; only aligned same-day minute bars can independently anchor the sample quotes.",
            "A one-shot provider probe does not create an auditable prospective signal archive, so it cannot by itself start the forward observation period.",
            "This audit does not test or establish alpha and does not justify changing frozen strategy thresholds.",
        ],
        "source_hashes": {
            "preregistration_sha256": sha_file(PREREG),
            "panel_registration_proof_sha256": panel_proof_sha,
            "runner_sha256": prereg["runner_sha256"],
            "candidate_source_sha256": sha_file(source_path),
            "quote_payload_sha256": sha_bytes(canonical_quotes),
        },
        "production_status": "pause_production",
        "backtest_run": False,
        "paper_account_created": False,
        "orders_placed": False,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    digest = sha_file(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"evidence": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "panel_registration_sha256": panel_proof_sha, "readiness_criteria": criteria, "decision": payload["decision"], "quote_rows": len(quote_records), "minute_rows": minute_frame.height, "samples": samples, "quote_status": quote_status, "paper_account_count": payload["panel"]["paper_account_count"]}, ensure_ascii=False, indent=2))
    provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
