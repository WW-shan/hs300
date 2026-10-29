from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "CONTRIBUTING.md").is_file())
BASE = Path("data/strategies/ai/.iterations/ai_hs300_multi_factor_rank")
EVIDENCE = ROOT / BASE / "evidence"
ROUND = "R300"
PARENT_RESULT = EVIDENCE / "r299_result.json"
PARENT_REPORT = EVIDENCE / "r299_result_v2.md"
PARENT_INDEX = EVIDENCE / "r299_round_index_v2.md"
PANEL_API = "http://127.0.0.1:3018/api/backtest/candidates"
GUARDS = {
    "R288": "280b43c9d2364b5589d9d0edc73d1144", "R289": "58dc4329c85e4df88271aa9b42276089",
    "R290": "2451e5a386d5489584e4121f42d97792", "R291": "4b5134a2f8a1441d8034dbb484b8e238",
    "R292": "cd6f5bc7773b44c9bedb69430f363220", "R293": "dbc1248434fb457f8312eadf47d459da",
    "R294": "4df004f0146b4e818f1f8ac16b31ebde", "R295": "51aefb61873a470a99abf7f66a614dfe",
    "R296": "48ca7b2f1105431d97c148e3e0574f4c", "R297": "25d66a13bc214247a93c233b2a32a7cd",
    "R298": "01aae1c6f0314612bec472bdd3140a69", "R299": "2d19f0651b3948bab8cd30c85e7c4e1a",
}


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    import urllib.request

    prereg = EVIDENCE / "r300_preregistration.json"
    manifest = EVIDENCE / "r300_input_manifest.jsonl"
    feasibility = EVIDENCE / "r300_feasibility.json"
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in (prereg, manifest, feasibility)):
        raise FileExistsError("R300 frozen evidence exists; refusing overwrite")

    for path in (PARENT_RESULT, PARENT_REPORT, PARENT_INDEX):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha_file(path) != sidecar:
            raise RuntimeError(f"R299 parent evidence checksum mismatch: {path.name}")

    result = json.loads(PARENT_RESULT.read_text(encoding="utf-8"))
    if result.get("round") != "R299" or result.get("decision") != "fail":
        raise RuntimeError("R299 source result is not the expected completed audit")
    candidates = result["execution"]["runs"]["candidate_3x"]["segments"]
    expected_segments = [
        ("2023-05-04", "2024-12-31"),
        ("2025-01-02", "2026-10-16"),
    ]
    if [(x["start"], x["end"]) for x in candidates] != expected_segments:
        raise RuntimeError("R299 frozen segment boundary mismatch")

    req = urllib.request.Request(PANEL_API, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        if response.status != 200:
            raise RuntimeError(f"R300 panel preflight HTTP {response.status}")
    panel = json.loads(raw)
    items = panel.get("items", [])
    if len(items) != 16:
        raise RuntimeError(f"R300 preflight expected 16 candidates, got {len(items)}")
    protected = {label: [item for item in items if item.get("id") == candidate_id]
                 for label, candidate_id in GUARDS.items()}
    panel_status = {label: (matches[0].get("status") if len(matches) == 1 else None)
                    for label, matches in protected.items()}
    if any(len(protected[label]) != 1 or panel_status[label] != "pending" for label in GUARDS):
        raise RuntimeError("R288-R299 panel protection preflight failed")

    trades = []
    for segment in candidates:
        if int(segment.get("days", 0)) <= 0:
            raise RuntimeError("R299 source segment has no trading days")
        cache_path = ROOT / "data/research/r299_cost_audit_cache" / (
            "ai_hs300_quality_relative_strength_h20_sl20_3x_"
            + segment["start"] + "_" + segment["end"] + ".trades.json"
        )
        cache_trades = json.loads(cache_path.read_text(encoding="utf-8"))
        if len(cache_trades) != segment["result_stats"]["n_trades"]:
            raise RuntimeError(f"R299 source trade count mismatch: {cache_path.name}")
        trades.append({
            "segment": segment["start"] + "~" + segment["end"],
            "trade_count": segment["result_stats"]["n_trades"],
            "trading_days": segment["days"],
            "source_trade_cache_path": cache_path.relative_to(ROOT).as_posix(),
            "source_trade_cache_sha256": sha_file(cache_path),
        })

    industry_path = ROOT / "data/ext_data/ext_hy_ths/part.parquet"
    if not industry_path.is_file():
        raise FileNotFoundError("R300 requires the existing local THS industry map; no provider fallback is allowed")
    industry = {
        "path": industry_path.relative_to(ROOT).as_posix(),
        "bytes": industry_path.stat().st_size,
        "mtime_ns": industry_path.stat().st_mtime_ns,
        "sha256": sha_file(industry_path),
    }
    feasibility_data = {
        "round": ROUND,
        "classification": "pre_registration_feasibility_for_frozen_period_subsegment_pnl_concentration",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "parent_round": "R299",
        "parent_result_sha256": sha_file(PARENT_RESULT),
        "parent_readable_report_sha256": sha_file(PARENT_REPORT),
        "panel_preflight": {"http_status": 200, "candidate_count": len(items), "response_sha256": hashlib.sha256(raw).hexdigest(),
                             "protected_statuses": panel_status},
        "fixed_segments": trades,
        "segment_source_caveat": "These are the preregistered R299 discovery and validation segments. The validation segment is the originally designated 2025-2026 validation period; year slices are not annual portfolio returns.",
        "industry_map_snapshot": industry,
        "research_cache": "data/research/r299_cost_audit_cache",
        "decision": "feasible",
        "side_effects": {"panel_post": False, "backtest": False, "provider_calls": False,
                          "formal_market_factor_strategy_data_written": False},
    }
    feasibility_data_bytes = (json.dumps(feasibility_data, ensure_ascii=False, indent=2) + "\n").encode()
    feasibility.write_bytes(feasibility_data_bytes)
    feasibility.with_name(feasibility.name + ".sha256").write_text(f"{hashlib.sha256(feasibility_data_bytes).hexdigest()}  {feasibility.name}\n", encoding="ascii")

    prereg_data = {
        "round": ROUND,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "post_hoc_cost_corrected_period_subsegment_concentration_diagnostic_not_alpha_confirmation",
        "question": "Within the two already-frozen R299 time segments, how concentrated is candidate net P&L by symbol, and is the aggregate C2-C4 failure present in both segments?",
        "selection_note": "Exploratory follow-up chosen after the full R257/R299 results and trade summaries were seen. This is not blind, independent validation, and no alpha, threshold, strategy or segment was selected or tuned in this round.",
        "parent": {"round": "R299", "result_sha256": sha_file(PARENT_RESULT),
                    "readable_report_sha256": sha_file(PARENT_REPORT), "round_index_sha256": sha_file(PARENT_INDEX)},
        "segments": [
            {key: value for key, value in x.items() if key != "segment"} | {"label": x["segment"]} for x in trades
        ],
        "strategy": "ai_hs300_quality_relative_strength_h20_sl20",
        "parent_candidate_id": "2d19f0651b3948bab8cd30c85e7c4e1a",
        "data_source": "R299's already-generated, cost-corrected system StrategyBacktestService trade records; no new backtest",
        "industry_map_snapshot": industry,
        "analysis": {
            "primary": "For each frozen time segment, group closed trade pnl_amount by symbol; calculate positive net symbol P&L top-1/top-3/top-5 shares using the exact R239/R257 definition.",
            "secondary": "Report gross-profit shares to show how positive-winner dispersion differs from net attribution; describe entry-calendar-year slices only when their aggregated net P&L is positive.",
            "no_annual_returns": True,
            "no_parameter_or_threshold_search": True,
        },
        "decision_rules": {
            "original_C2": "largest positive symbol net P&L divided by segment total net P&L <= 25%, evaluated only when segment net P&L > 0",
            "original_C3": "top three positive symbol net P&L divided by segment total net P&L <= 55%, evaluated only when segment net P&L > 0",
            "original_C4": "top five positive symbol net P&L divided by segment total net P&L <= 75%, evaluated only when segment net P&L > 0",
            "negative_or_zero_segment_net_pnl": "report concentration as not applicable for the original positive-net-share gates; do not divide by a substitute denominator",
            "threshold_changes": False,
        },
        "panel_candidate": {
            "kind": "strategy",
            "name": "R300 R299分段收益集中度诊断",
            "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
            "config": {
                "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
                "parent_round": "R299",
                "parent_candidate_id": "2d19f0651b3948bab8cd30c85e7c4e1a",
                "segment_start_dates": [x["segment"].split("~")[0] for x in trades],
                "segment_end_dates": [x["segment"].split("~")[1] for x in trades],
                "analysis": "closed_trade_net_pnl_symbol_concentration_diagnostic",
                "thresholds_changed": False,
            },
            "metrics": {},
            "data_as_of": "2026-10-16",
            "status": "pending",
        },
        "feasibility_path": "evidence/r300_feasibility.json",
        "feasibility_sha256": hashlib.sha256(feasibility_data_bytes).hexdigest(),
        "input_manifest_path": "evidence/r300_input_manifest.jsonl",
        "panel_guard_scope": GUARDS,
        "side_effect_boundary": "Only R300 preregistration, manifest, evidence and one panel candidate are written. Analysis reads R299's isolated cache and the existing local industry snapshot. No provider, builder, formal data or strategy/backtest API write, paper account or order flow.",
    }
    prereg_bytes = (json.dumps(prereg_data, ensure_ascii=False, indent=2) + "\n").encode()
    prereg.write_bytes(prereg_bytes)
    prereg.with_name(prereg.name + ".sha256").write_text(f"{hashlib.sha256(prereg_bytes).hexdigest()}  {prereg.name}\n", encoding="ascii")

    inputs = [
        EVIDENCE / "r299_result.json", EVIDENCE / "r299_result.json.sha256",
        EVIDENCE / "r299_result_v2.md", EVIDENCE / "r299_result_v2.md.sha256",
        EVIDENCE / "r299_round_index_v2.md", EVIDENCE / "r299_round_index_v2.md.sha256",
        EVIDENCE / "r299_input_integrity_final.json", EVIDENCE / "r299_input_integrity_final.json.sha256",
        EVIDENCE / "r299_final_panel_readback.json", EVIDENCE / "r299_final_panel_readback.json.sha256",
        ROOT / "scripts/backtest_r239_concentration_audit.py",
        ROOT / "backend/app/services/industry_map.py", industry_path,
        ROOT / "data/research/r299_cost_audit_cache/ai_hs300_quality_relative_strength_h20_sl20_3x_2023-05-04_2024-12-31.trades.json",
        ROOT / "data/research/r299_cost_audit_cache/ai_hs300_quality_relative_strength_h20_sl20_3x_2025-01-02_2026-10-16.trades.json",
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_preregistration.json",
    ]
    records = []
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(f"R300 frozen input missing: {path}")
        st = path.stat()
        records.append({"path": path.relative_to(ROOT).as_posix(), "bytes": st.st_size,
                        "mtime_ns": st.st_mtime_ns, "sha256": sha_file(path)})
    manifest_bytes = b"".join((json.dumps(item, sort_keys=True) + "\n").encode() for item in records)
    manifest.write_bytes(manifest_bytes)
    manifest.with_name(manifest.name + ".sha256").write_text(f"{hashlib.sha256(manifest_bytes).hexdigest()}  {manifest.name}\n", encoding="ascii")

    print(json.dumps({"round": ROUND, "feasibility": feasibility_data,
                      "preregistration_sha256": hashlib.sha256(prereg_bytes).hexdigest(),
                      "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                      "input_manifest_count": len(records)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
