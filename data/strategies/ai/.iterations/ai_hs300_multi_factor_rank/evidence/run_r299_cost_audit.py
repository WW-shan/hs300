from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
ROUND = "R299"
START, END = date(2023, 5, 4), date(2026, 10, 16)
SEGMENTS = [(date(2023, 5, 4), date(2024, 12, 31)), (date(2025, 1, 2), END)]
CACHE = ROOT / "data/research/r299_cost_audit_cache"


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def load_script(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    prereg_path = EVIDENCE / "r299_preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    prereg_sha = sha_file(prereg_path)
    if prereg_sha != prereg_path.with_name(prereg_path.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R299 preregistration hash mismatch")
    manifest_path = EVIDENCE / "r299_input_manifest.jsonl"
    manifest_sha = sha_file(manifest_path)
    if manifest_sha != prereg.get("input_manifest_sha256"):
        raise RuntimeError("R299 input manifest hash mismatch")
    manifest = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    addendum_path = EVIDENCE / "r299_manifest_execution_addendum.json"
    addendum = json.loads(addendum_path.read_text(encoding="utf-8")) if addendum_path.exists() else None
    allowed_changes = {x["path"]: x for x in (addendum or {}).get("execution_script_changes", [])}
    if addendum is not None:
        addendum_sha = sha_file(addendum_path)
        sidecar = addendum_path.with_name(addendum_path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if addendum_sha != sidecar or addendum.get("parent_manifest_sha256") != manifest_sha:
            raise RuntimeError("R299 execution addendum checksum or parent mismatch")
    for item in manifest:
        path = ROOT / item["path"]
        stat = path.stat()
        actual_hash = sha_file(path)
        if stat.st_size != item["bytes"] or stat.st_mtime_ns != item["mtime_ns"] or actual_hash != item["sha256"]:
            change = allowed_changes.get(item["path"])
            if (change is None or change.get("original_sha256") != item["sha256"]
                    or change.get("current_sha256") != actual_hash or change.get("current_bytes") != stat.st_size
                    or change.get("current_mtime_ns") != stat.st_mtime_ns
                    or change.get("change_class") != "execution_harness_integrity_only"):
                raise RuntimeError(f"frozen input changed without execution-only addendum: {item['path']}")
    if CACHE.exists():
        raise FileExistsError(f"R299 cache already exists; refusing rerun: {CACHE}")
    if (EVIDENCE / "r299_result.json").exists() or (EVIDENCE / "r299_result.json.sha256").exists():
        raise FileExistsError("R299 result already exists; refusing overwrite")

    sys.path.insert(0, str(ROOT / "backend"))
    import polars as pl
    from app.backtest.engine import MatcherConfig

    r112 = load_script("scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r299_run")
    r221 = load_script("scripts/backtest_r221_sga_nonfin_quality.py", "r221_r299_run")
    r229 = load_script("scripts/backtest_r229_ocf_margin.py", "r229_r299_run")
    r239 = load_script("scripts/backtest_r239_concentration_audit.py", "r239_r299_run")
    r253 = load_script("scripts/backtest_r253_min_rank.py", "r253_r299_run")
    r257 = load_script("scripts/backtest_r257_quality_relative_strength.py", "r257_r299_run")

    original_config = r112.StrategyBacktestConfig

    def corrected_cost_config(*args, **kwargs):
        commission = kwargs.get("commission_pct")
        if commission is not None:
            kwargs["commission_pct"] = commission * 2.0
        return original_config(*args, **kwargs)

    r112.StrategyBacktestConfig = corrected_cost_config

    calendar = r229._benchmark_dates(START, END)
    members = r221._nonfin_membership(r221._membership_with_industry())
    factor = r257._factor_rows().filter((pl.col("date") >= START) & (pl.col("date") <= END))
    eligible = r257._member_daily(calendar, members).join(factor, on=["date", "symbol"], how="left")
    valid = eligible.filter(pl.col(r257.FACTOR_COLUMN).is_not_null())
    if len(calendar) != 837 or valid.height / eligible.height < 0.99 or valid["date"].n_unique() / len(calendar) < 0.95:
        raise RuntimeError("R299 frozen factor coverage gate failed; backtest not run")

    costs = prereg["execution"]
    expected_3x = MatcherConfig(fees_pct=0.0006, commission_pct=0.0012, stamp_tax_pct=0.001, slippage_bps=15.0)
    actual_3x = MatcherConfig(**{"fees_pct": costs["corrected_3x_components"]["fees_pct_legacy_fallback"],
                                 "commission_pct": costs["corrected_3x_components"]["commission_pct_effective"],
                                 "stamp_tax_pct": costs["corrected_3x_components"]["stamp_tax_pct_sell"],
                                 "slippage_bps": costs["corrected_3x_components"]["slippage_bps_per_side"]})
    if actual_3x.buy_cost_pct() != expected_3x.buy_cost_pct() or actual_3x.sell_cost_pct() != expected_3x.sell_cost_pct():
        raise AssertionError("R299 effective cost differs from frozen corrected model")

    r253.CACHE = CACHE
    service = r112._service()
    runs: dict[str, dict] = {}
    curves: dict[str, pl.DataFrame] = {}
    trades: dict[str, list[dict]] = {}
    specs = [
        ("candidate_3x", "ai_hs300_quality_relative_strength_h20_sl20", 3.0),
        ("control_3x", "ai_hs300_quality_relative_strength_control", 3.0),
        ("candidate_4x", "ai_hs300_quality_relative_strength_h20_sl20", 4.0),
        ("candidate_5x", "ai_hs300_quality_relative_strength_h20_sl20", 5.0),
    ]
    for label, strategy_id, multiplier in specs:
        parts, part_runs, part_trades = [], [], []
        for start, end in SEGMENTS:
            curve, info, segment_trades = r253._run_segment(service, members, r112, r229, strategy_id, start, end, multiplier)
            parts.append(curve)
            part_runs.append({"start": str(start), "end": str(end), **info})
            part_trades.extend(segment_trades)
            print(json.dumps({"round": ROUND, "run": label, "segment": [str(start), str(end)],
                              "curve_stats": info.get("curve_stats"), "result_stats": info.get("result_stats")}, default=str), flush=True)
        runs[label] = {"segments": part_runs, "trading_days": sum(int(item["days"]) for item in part_runs)}
        curves[label] = r229._stitch(parts)
        trades[label] = part_trades

    from app.services.industry_map import load_industry_l1
    industry_frame = load_industry_l1(ROOT / "data")
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    concentration = r239._aggregate(trades["candidate_3x"], industry)
    stats = {label: r112._stats(curve) for label, curve in curves.items()}
    candidate = r229._result(r112, curves["candidate_3x"])
    control = r229._result(r112, curves["control_3x"])
    stress = {key: stats[f"candidate_{key}"] for key in ("4x", "5x")}

    original = json.loads((EVIDENCE / "r257_quality_relative_strength.json").read_text(encoding="utf-8-sig"))
    old_decision = original["decision"]
    candidate_full, control_full = candidate["full"], control["full"]
    validation_trades = [trade for trade in trades["candidate_3x"] if str(trade.get("segment", "")).startswith("2025-01-02~")]
    concentration_validation = r239._aggregate(validation_trades, industry)
    validation_positive = sum(1 for row in concentration_validation["per_symbol"] if row["pnl_amount"] > 0)
    gates = {
        "D1": old_decision["D1"], "D2": old_decision["D2"], "D3": old_decision["D3"],
        "D4": old_decision["D4"], "I1": old_decision["I1"], "I2": old_decision["I2"],
        "G1": candidate["discovery_2022_2024"]["total_return"] >= control["discovery_2022_2024"]["total_return"] + 0.05,
        "G2": candidate["discovery_2022_2024"]["mdd"] >= control["discovery_2022_2024"]["mdd"] - 0.05,
        "G3": candidate["validation_2025_2026"]["total_return"] >= control["validation_2025_2026"]["total_return"],
        "G4": candidate_full["total_return"] > 0 and candidate_full["total_return"] >= control_full["total_return"],
        "G5": candidate_full["mdd"] >= -0.40 and candidate_full["mdd"] >= control_full["mdd"] - 0.05,
        "G6": candidate["rolling_1y"]["positive_rate"] >= 0.60 and candidate["rolling_1y"]["positive_rate"] >= control["rolling_1y"]["positive_rate"] - 0.02,
        "G7": candidate["recent_12m"]["total_return"] > 0 and candidate["recent_12m"]["mdd"] >= control["recent_12m"]["mdd"] - 0.05,
        "G8": stress["4x"]["total_return"] > 0, "G9": stress["5x"]["total_return"] > 0,
        "C1": concentration["distinct_symbols"] >= 30,
        "C2": concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25,
        "C3": concentration["total_pnl_amount"] > 0 and concentration["top3_net_share"] <= 0.55,
        "C4": concentration["total_pnl_amount"] > 0 and concentration["top5_net_share"] <= 0.75,
        "C5": validation_positive >= 8,
        "C6": concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45,
        "C7": concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20,
    }
    gates["all_pass"] = all(gates.values())

    round_trip = {}
    for multiplier in (3.0, 4.0, 5.0):
        scale = multiplier / 3.0
        cfg = MatcherConfig(fees_pct=0.0006 * scale, commission_pct=0.0012 * scale,
                            stamp_tax_pct=0.001 * scale, slippage_bps=15.0 * scale)
        round_trip[f"{multiplier:g}x"] = {"buy_component_pct": cfg.buy_cost_pct(), "sell_component_pct": cfg.sell_cost_pct(),
                                             "round_trip_pct": cfg.buy_cost_pct() + cfg.sell_cost_pct()}
    declared_3x = MatcherConfig(fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0)
    actual_3x_declared = {"buy_component_pct": declared_3x.buy_cost_pct(), "sell_component_pct": declared_3x.sell_cost_pct(),
                          "round_trip_pct": declared_3x.buy_cost_pct() + declared_3x.sell_cost_pct()}

    result = {
        "round": ROUND, "classification": "post_hoc_cost_semantics_sensitivity_audit_not_alpha_confirmation",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_sha, "input_manifest_sha256": manifest_sha,
        "input_manifest_files_verified_before_run": len(manifest),
        "manifest_execution_addendum_sha256": sha_file(addendum_path) if addendum_path.exists() else None,
        "manifest_execution_addendum_files": sorted(allowed_changes),
        "window": [str(START), str(END)], "segments": [[str(a), str(b)] for a, b in SEGMENTS],
        "calendar_days": len(calendar), "factor_coverage": round(valid.height / eligible.height, 8),
        "factor_days": valid["date"].n_unique(), "factor_day_coverage": round(valid["date"].n_unique() / len(calendar), 8),
        "factor_builder_invoked": False,
        "cost_semantics": {
            "engine_rule": "commission_pct overrides fees_pct; fees_pct is used only when commission_pct is None",
            "declared_3x_components": {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.001, "slippage_bps": 15.0},
            "declared_effective": actual_3x_declared, "corrected_round_trip_by_stress": round_trip,
            "round_trip_undercharge_pct_points": {"3x": round_trip["3x"]["round_trip_pct"] - actual_3x_declared["round_trip_pct"],
                                                       "4x": round_trip["4x"]["round_trip_pct"] - (actual_3x_declared["round_trip_pct"] * 4 / 3),
                                                       "5x": round_trip["5x"]["round_trip_pct"] - (actual_3x_declared["round_trip_pct"] * 5 / 3)},
        },
        "execution": {"engine": "StrategyBacktestService", "matching": "open_t+1", "position_sizing": "equal",
                       "max_positions": 10, "score_limit": 30, "holding_days": 20, "stop_loss": -0.20,
                       "candidate_strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
                       "control_strategy_id": "ai_hs300_quality_relative_strength_control",
                       "runs": runs, "candidate_3x": candidate, "control_3x": control,
                       "candidate_4x_5x": stress},
        "concentration": {key: value for key, value in concentration.items() if key != "per_symbol"},
        "concentration_by_symbol": concentration["per_symbol"],
        "gates": gates, "decision": "pass" if gates["all_pass"] else "fail",
        "interpretation": "Post-hoc cost correction only. A failed result remains a failure; any pass remains non-independent and does not authorize paper trading or production.",
        "side_effects": {"formal_market_or_factor_data_written": False, "provider_calls": False,
                          "strategy_or_backtest_api_write": False, "paper_or_orders": False,
                          "research_cache": str(CACHE.relative_to(ROOT)), "cache_is_new_and_round_scoped": True},
    }
    output = EVIDENCE / "r299_result.json"
    write_once(output, (json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8"))
    print(json.dumps({"round": ROUND, "decision": result["decision"], "gates": gates,
                      "candidate_3x": candidate["full"], "control_3x": control["full"],
                      "stress": stress, "concentration": result["concentration"],
                      "result_path": str(output)}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
