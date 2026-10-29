#!/usr/bin/env python3
"""Read-only R279 audit of current macro source freshness and provenance."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import polars as pl  # noqa: E402
import requests  # noqa: E402

from app.data_providers import custom as custom_sources  # noqa: E402

DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r279_preregistration.json"
PANEL_OUT = EVIDENCE / "r279_panel_registration.json"
OUT = EVIDENCE / "r279_macro_source_audit.json"
API = "http://127.0.0.1:3018"
CN = ZoneInfo("Asia/Shanghai")
SAMPLES = ("600519.SH", "000001.SZ", "300750.SZ")
CONTROL = "ai_hs300_macro_gate_quality"
CANDIDATE = "ai_hs300_macro_gate_quality_r275_remove_term"
FACTORS = (
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
)
MACRO_TIME_COLUMNS = {
    "announce_date", "announcement_time", "publish_time", "published_at",
    "updated_at", "collected_at", "采集时间", "发布时间", "公告时间",
}


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    path.write_bytes(raw)
    digest = sha_bytes(raw)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def api_get(path: str) -> tuple[int, dict]:
    response = requests.get(API + path, timeout=30, proxies={"http": None, "https": None})
    try:
        payload = response.json()
    except ValueError:
        payload = {"non_json": response.text[:500]}
    return response.status_code, payload


def cache_summary(path: Path) -> dict:
    if not path.exists():
        return {"path": path.relative_to(ROOT).as_posix(), "exists": False}
    frame = pl.read_parquet(path)
    date_col = next((name for name in ("日期", "date") if name in frame.columns), None)
    max_date = None
    min_date = None
    if date_col:
        values = frame[date_col].cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False)
        min_date, max_date = values.min(), values.max()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "exists": True,
        "rows": frame.height,
        "columns": frame.columns,
        "date_column": date_col,
        "date_min": str(min_date) if min_date else None,
        "date_max": str(max_date) if max_date else None,
        "timing_provenance_columns": sorted(MACRO_TIME_COLUMNS.intersection(frame.columns)),
        "sha256": sha_file(path),
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
    }


def summarize_frame(frame, date_column: str, value_column: str | None = None) -> dict:
    if date_column not in frame.columns:
        raise ValueError(f"missing date column: {date_column}")
    rows = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else frame.to_dicts()
    normalized_dates = [str(row.get(date_column))[:10] for row in rows if row.get(date_column) is not None]
    latest_date = max(normalized_dates) if normalized_dates else None
    latest_rows = [row for row in rows if str(row.get(date_column, ""))[:10] == latest_date]
    value = None
    if latest_rows and value_column and value_column in latest_rows[-1]:
        raw_value = latest_rows[-1][value_column]
        value = raw_value.item() if hasattr(raw_value, "item") else raw_value
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "rows": len(rows),
        "columns": list(frame.columns),
        "date_min": min(normalized_dates) if normalized_dates else None,
        "date_max": latest_date,
        "latest_value": value,
        "timing_provenance_columns": sorted(MACRO_TIME_COLUMNS.intersection(frame.columns)),
        "response_sha256": sha_bytes(canonical.encode("utf-8")),
    }


def main() -> int:
    for path in (PANEL_OUT, OUT, PANEL_OUT.with_suffix(".json.sha256"), OUT.with_suffix(".json.sha256")):
        if path.exists():
            raise RuntimeError(f"R279 evidence exists; refusing overwrite: {path.name}")
    prereg_sha = sha_file(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    started = datetime.now(UTC)
    now_local = started.astimezone(CN)
    panel_started = datetime.now(UTC)
    strategy_status, strategy_response = api_get("/api/strategies?include_research=true")
    factor_status, factor_response = api_get("/api/factors?asset_type=stock")
    capability_status, capability_response = api_get("/api/settings/capability-matrix")
    sources_status, sources_response = api_get("/api/settings/data-sources")
    panel_done = datetime.now(UTC)

    strategies = {item.get("id"): item for item in strategy_response.get("strategies", [])}
    factors = {item.get("id"): item for item in factor_response.get("factors", [])}
    candidate_path = DATA / "strategies/ai" / f"{CANDIDATE}.py"
    source_sha = sha_file(candidate_path)
    candidate_ok = (
        strategies.get(CANDIDATE, {}).get("research_only") is True
        and strategies.get(CONTROL) is not None
        and not strategy_response.get("load_errors")
        and all(factor_id in factors for factor_id in FACTORS)
        and candidate_path.exists()
        and source_sha == prereg["candidate_source_sha256"]
    )
    capabilities = capability_response.get("capabilities", [])
    cap_by_id = {item.get("id"): item for item in capabilities}
    plugins = {item.get("name"): item for item in sources_response.get("plugins", [])}
    panel_proof = {
        "round": "R279",
        "classification": "read_only_live_panel_registration_proof",
        "captured_at_utc": panel_done.isoformat(),
        "panel_capture_started_utc": panel_started.isoformat(),
        "api_base": API,
        "preregistration_sha256": prereg_sha,
        "strategies_http_status": strategy_status,
        "strategy_count_including_research": len(strategies),
        "strategy_load_errors": strategy_response.get("load_errors", []),
        "control_registered": CONTROL in strategies,
        "candidate_registered": CANDIDATE in strategies,
        "candidate_research_only": strategies.get(CANDIDATE, {}).get("research_only") is True,
        "candidate_source_path": candidate_path.relative_to(ROOT).as_posix(),
        "candidate_source_sha256": source_sha,
        "factors_http_status": factor_status,
        "factor_count": len(factors),
        "required_factor_registration": {key: key in factors for key in FACTORS},
        "capability_http_status": capability_status,
        "daily_capability": cap_by_id.get("daily"),
        "realtime_capability": cap_by_id.get("realtime"),
        "data_sources_http_status": sources_status,
        "akshare_plugin": plugins.get("akshare"),
        "registration_pass": strategy_status == 200 and factor_status == 200 and candidate_ok,
        "reload_performed": False,
        "backtest_run": False,
        "quote_polling_changed": False,
        "paper_account_created": False,
        "orders_placed": False,
        "production_status": "pause_production",
    }
    panel_sha = write_json(PANEL_OUT, panel_proof)

    provider_observations: dict[str, dict] = {}
    provider_errors: list[str] = []
    # Read directly through the same installed AkShare adapter used by the system.
    try:
        custom_sources.load_all()
        provider = custom_sources.get_provider("akshare")
        trading_days = provider.trading_days()
        completed = sorted(day for day in trading_days if day <= now_local.date() - (timedelta(days=0) if now_local.time() >= datetime.strptime("15:30", "%H:%M").time() else timedelta(days=1)))
        latest_completed = completed[-1] if completed else None
        for symbol in SAMPLES:
            frame = provider.get_daily(
                [symbol],
                start_time=now_local.date() - timedelta(days=20),
                end_time=now_local.date(),
                asset_type="stock",
            )
            provider_observations[symbol] = {
                "daily_rows": frame.height,
                "daily_first_date": str(frame["date"].min()) if frame.height else None,
                "daily_last_date": str(frame["date"].max()) if frame.height else None,
                "daily_payload_sha256": sha_bytes(
                    json.dumps(frame.to_dicts(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                ),
            }
        daily_fresh = bool(latest_completed and all(row["daily_last_date"] == str(latest_completed) for row in provider_observations.values()))
        provider_observations["calendar"] = {
            "trading_days": len(trading_days),
            "latest_completed_trading_day": str(latest_completed) if latest_completed else None,
            "sample_daily_all_reach_latest_completed": daily_fresh,
        }
    except Exception as exc:  # noqa: BLE001 - preserve the failure as evidence
        daily_fresh = False
        provider_errors.append(f"system AkShare provider: {type(exc).__name__}: {str(exc)[:240]}")
        provider_observations["calendar"] = {"latest_completed_trading_day": None}

    upstream: dict[str, dict] = {}
    try:
        import akshare as ak

        start = (now_local.date() - timedelta(days=90)).strftime("%Y%m%d")
        end = now_local.date().strftime("%Y%m%d")
        bond_frame = ak.bond_china_yield(start_date=start, end_date=end)
        bond_summary = summarize_frame(bond_frame, "日期", "中国国债收益率10年")
        upstream["bond_china_yield"] = bond_summary
        pe_frame = ak.stock_index_pe_lg(symbol="沪深300")
        pe_summary = summarize_frame(pe_frame, "日期", "滚动市盈率")
        upstream["hs300_trailing_pe"] = pe_summary
        credit_frame = ak.bond_china_yield(start_date=start, end_date=end)
        credit_summary = summarize_frame(credit_frame, "日期", "5年")
        credit_summary["curves"] = sorted(set(credit_frame["曲线名称"].dropna().astype(str).to_list())) if "曲线名称" in credit_frame.columns else []
        upstream["credit_yield_curves"] = credit_summary
    except Exception as exc:  # noqa: BLE001 - capture source availability without changing caches
        provider_errors.append(f"AkShare macro source query: {type(exc).__name__}: {str(exc)[:240]}")

    local_caches = [
        cache_summary(DATA / "research/macro/bond_10y.parquet"),
        cache_summary(DATA / "research/r93_erp/hs300_pe.parquet"),
        *[cache_summary(path) for path in sorted((DATA / "research/r92_credit_spread").glob("*.parquet"))],
    ]
    ext_parts = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    ext_latest = ext_parts[-1].parent.name.removeprefix("date=") if ext_parts else None
    ext_config = json.loads((DATA / "ext_data/alt_hs300/config.json").read_text(encoding="utf-8"))
    macro_fields = {
        "equity_risk_premium", "credit_spread_5y_low", "term_spread_10y_2y",
    }
    ext_config_fields = {item.get("name") for item in ext_config.get("fields", [])}
    macro_field_registration = {field: field in ext_config_fields for field in sorted(macro_fields)}
    freshness_rows = [value.get("date_max") for value in upstream.values() if value.get("date_max")]
    macro_observation_latest = min(freshness_rows) if freshness_rows else None
    latest_completed_str = provider_observations.get("calendar", {}).get("latest_completed_trading_day")
    try:
        macro_age_days = (date.fromisoformat(latest_completed_str) - date.fromisoformat(macro_observation_latest)).days if latest_completed_str and macro_observation_latest else None
    except ValueError:
        macro_age_days = None
    macro_fresh = macro_age_days is not None and 0 <= macro_age_days <= 5
    macro_timing_proven = bool(upstream) and all(
        value.get("timing_provenance_columns") for value in upstream.values()
    )
    result = {
        "round": "R279",
        "classification": "preregistered_read_only_macro_source_freshness_and_provenance_audit",
        "captured_at": {"started_utc": started.isoformat(), "finished_utc": datetime.now(UTC).isoformat(), "local_beijing": now_local.isoformat()},
        "preregistration_sha256": prereg_sha,
        "panel_registration_proof_sha256": panel_sha,
        "panel_registration_pass": panel_proof["registration_pass"],
        "candidate_source_sha256": source_sha,
        "system_provider": {
            "provider": "akshare",
            "read_path": "app.data_providers.custom.get_provider('akshare')",
            "sample_daily_observations": provider_observations,
            "sample_daily_freshness_pass": daily_fresh,
        },
        "macro_upstream_read_only_probe": upstream,
        "macro_source_errors": provider_errors,
        "local_macro_caches": local_caches,
        "alt_hs300": {
            "latest_partition_date": ext_latest,
            "partition_count": len(ext_parts),
            "macro_fields_registered": macro_field_registration,
            "pull_configured": bool(ext_config.get("pull")),
            "config_updated_at": ext_config.get("updated_at"),
        },
        "readiness_criteria": {
            "candidate_registered_research_only": panel_proof["registration_pass"],
            "sample_daily_freshness": daily_fresh,
            "macro_observation_fresh_within_5_calendar_days": macro_fresh,
            "macro_vendor_publication_or_collection_time_available": macro_timing_proven,
            "prospective_collection_path_created": False,
            "all_pass": False,
        },
        "decision": "macro_cache_maintenance_gap_identified_alpha_not_tested",
        "interpretation": [
            "This audit reads current panel state, registered-provider daily bars, local cache metadata, and public AkShare macro endpoints; it does not inspect equity returns.",
            "Observation dates and local capture time do not prove vendor publication time or historical point-in-time availability.",
            "No history cache, extension partition, strategy, quote-polling setting, paper account, or order is changed.",
        ],
        "production_status": "pause_production",
        "backtest_run": False,
        "quote_polling_changed": False,
        "paper_account_created": False,
        "orders_placed": False,
    }
    digest = write_json(OUT, result)
    print(json.dumps({"result": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "criteria": result["readiness_criteria"], "errors": provider_errors}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
