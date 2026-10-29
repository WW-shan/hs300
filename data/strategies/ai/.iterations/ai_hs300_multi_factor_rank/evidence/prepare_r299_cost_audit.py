from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
ROUND = "R299"
START, END = date(2023, 5, 4), date(2026, 10, 16)
SEGMENTS = [(date(2023, 5, 4), date(2024, 12, 31)), (date(2025, 1, 2), END)]
API = "http://127.0.0.1:3018"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha_bytes(data)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def load_script(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def get_json(path: str) -> tuple[int, bytes, dict]:
    request = Request(API + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        raw = response.read()
        return response.status, raw, json.loads(raw)


def source_paths() -> list[Path]:
    fixed = [
        "CONTRIBUTING.md", "docs/secondary-development.md",
        "backend/app/backtest/engine.py", "backend/app/backtest/strategy.py",
        "backend/app/backtest/fundamentals.py", "backend/app/backtest/matrix.py",
        "backend/app/factors/ext_factors.py", "backend/app/factors/registry.py",
        "backend/app/factors/store.py", "backend/app/services/industry_map.py",
        "backend/app/strategy/config.py", "backend/app/strategy/engine.py",
        "backend/app/tickflow/repository.py", "backend/tests/backtest/test_cost_model.py",
        "scripts/backtest_r112_dual_sleeve_risk_parity.py",
        "scripts/backtest_r166_weak_regime_factor_scan.py",
        "scripts/backtest_r221_sga_nonfin_quality.py",
        "scripts/backtest_r229_ocf_margin.py", "scripts/backtest_r239_concentration_audit.py",
        "scripts/backtest_r253_min_rank.py", "scripts/backtest_r257_quality_relative_strength.py",
        "scripts/build_r257_quality_relative_strength_factor.py",
        "scripts/build_alt_factor_data.py",
        "data/strategies/ai/ai_hs300_quality_relative_strength_h20_sl20.py",
        "data/strategies/ai/ai_hs300_quality_relative_strength_control.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_preregistration.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_data_feasibility.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_factor_build.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_quality_relative_strength.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r298_preregistration.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r298_result_v2.md",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/prepare_r299_cost_audit.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/register_r299_once.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r299_cost_audit.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/finalize_r299_cost_audit.py",
    ]
    paths = {ROOT / relative for relative in fixed}
    missing = sorted(str(path.relative_to(ROOT)) for path in paths if not path.is_file())
    if missing:
        raise FileNotFoundError(f"missing frozen inputs: {missing}")
    for pattern in (
        "data/kline_daily_enriched/date=*/part.parquet",
        "data/kline_index_daily/date=*/part.parquet",
        "data/ext_data/alt_hs300/timeseries/date=*/part.parquet",
        "data/ext_data/alt_hs300_minrank/timeseries/date=*/part.parquet",
        "data/ext_data/alt_hs300_qrs/timeseries/date=*/part.parquet",
        "data/ext_data/ext_hy_ths/**/*", "data/financials/**/*",
        "data/instruments/**/*",
    ):
        matches = list(ROOT.glob(pattern))
        if pattern.endswith("part.parquet") and not matches:
            raise FileNotFoundError(f"no files match {pattern}")
        paths.update(path for path in matches if path.is_file())
    return sorted(paths)


def main() -> int:
    if any((EVIDENCE / f"r299_{name}").exists() for name in ("input_manifest.jsonl", "preregistration.json")):
        raise FileExistsError("R299 preparation artifacts already exist; refusing overwrite")
    sys.path.insert(0, str(ROOT / "backend"))
    import polars as pl
    from app.backtest.engine import MatcherConfig

    r112 = load_script("scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r299_prepare")
    r221 = load_script("scripts/backtest_r221_sga_nonfin_quality.py", "r221_r299_prepare")
    r229 = load_script("scripts/backtest_r229_ocf_margin.py", "r229_r299_prepare")
    r257 = load_script("scripts/backtest_r257_quality_relative_strength.py", "r257_r299_prepare")

    calendar = r229._benchmark_dates(START, END)
    members = r221._nonfin_membership(r221._membership_with_industry())
    factor = r257._factor_rows().filter((pl.col("date") >= START) & (pl.col("date") <= END))
    eligible = r257._member_daily(calendar, members).join(factor, on=["date", "symbol"], how="left")
    valid = eligible.filter(pl.col(r257.FACTOR_COLUMN).is_not_null())
    coverage = valid.height / eligible.height if eligible.height else 0.0
    valid_days = valid["date"].n_unique()

    declared = MatcherConfig(fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0)
    corrected = MatcherConfig(fees_pct=0.0006, commission_pct=0.0012, stamp_tax_pct=0.001, slippage_bps=15.0)
    if abs((corrected.buy_cost_pct() + corrected.sell_cost_pct()) - 0.0064) > 1e-12:
        raise AssertionError("corrected round-trip cost does not match declared components")

    panel_status, panel_raw, panel = get_json("/api/backtest/candidates")
    strategies_status, strategies_raw, strategies = get_json("/api/strategies?include_research=true")
    strategy_ids = {item.get("id") for item in strategies.get("strategies", [])}
    required_strategies = {"ai_hs300_quality_relative_strength_h20_sl20", "ai_hs300_quality_relative_strength_control"}
    if panel_status != 200 or strategies_status != 200 or not required_strategies.issubset(strategy_ids):
        raise RuntimeError("panel or required R257 strategy preflight failed")
    r257_result_path = EVIDENCE / "r257_quality_relative_strength.json"
    r257_result = json.loads(r257_result_path.read_text(encoding="utf-8-sig"))
    if not (coverage >= 0.99 and valid_days / len(calendar) >= 0.95):
        raise RuntimeError("R257 data coverage is insufficient for a cost sensitivity rerun")

    feasibility = {
        "round": ROUND, "classification": "post_hoc_cost_semantics_audit_feasibility_before_corrected_cost_rerun",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": [str(START), str(END)], "segments": [[str(a), str(b)] for a, b in SEGMENTS],
        "calendar_days": len(calendar), "first_last_trading_day": [str(calendar[0]), str(calendar[-1])],
        "historical_nonfinancial_member_rows": eligible.height, "valid_factor_rows": valid.height,
        "factor_coverage": round(coverage, 8), "valid_factor_days": valid_days,
        "factor_day_coverage": round(valid_days / len(calendar), 8),
        "factor_source": r257.FACTOR_ID, "builder_invoked": False,
        "panel_preflight": {"candidates_http_status": panel_status, "candidates_count": len(panel.get("items", [])),
                              "strategies_http_status": strategies_status,
                              "required_strategy_ids_present": sorted(required_strategies),
                              "strategy_count": len(strategy_ids)},
        "r257_original_decision": r257_result["decision"],
        "declared_cost_model_engine_effective": {
            "buy_round_trip_component_pct": round(declared.buy_cost_pct(), 8),
            "sell_component_pct": round(declared.sell_cost_pct(), 8),
            "round_trip_pct": round(declared.buy_cost_pct() + declared.sell_cost_pct(), 8),
        },
        "corrected_cost_model_engine_effective": {
            "buy_component_pct": round(corrected.buy_cost_pct(), 8),
            "sell_component_pct": round(corrected.sell_cost_pct(), 8),
            "round_trip_pct": round(corrected.buy_cost_pct() + corrected.sell_cost_pct(), 8),
            "incremental_round_trip_pct": round((corrected.buy_cost_pct() + corrected.sell_cost_pct()) - (declared.buy_cost_pct() + declared.sell_cost_pct()), 8),
        },
        "data_side_effects": {"provider_calls": False, "strategy_builder_calls": False, "formal_data_written": False, "orders_or_paper": False},
        "panel_candidates_response_sha256": sha_bytes(panel_raw),
        "panel_strategies_response_sha256": sha_bytes(strategies_raw),
    }
    feasibility_path = EVIDENCE / "r299_feasibility.json"
    feasibility_bytes = (json.dumps(feasibility, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if feasibility_path.exists():
        feasibility = json.loads(feasibility_path.read_text(encoding="utf-8"))
        feasibility_sha = sha_file(feasibility_path)
        expected_sidecar = feasibility_path.with_name(feasibility_path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if feasibility_sha != expected_sidecar or feasibility.get("classification") != "post_hoc_cost_semantics_audit_feasibility_before_corrected_cost_rerun":
            raise RuntimeError("existing R299 feasibility artifact has invalid checksum/classification")
    else:
        feasibility_sha = write_once(feasibility_path, feasibility_bytes)

    manifest_lines = []
    for path in source_paths():
        stat = path.stat()
        manifest_lines.append(json.dumps({"path": path.relative_to(ROOT).as_posix(), "bytes": stat.st_size,
                                          "mtime_ns": stat.st_mtime_ns, "sha256": sha_file(path)}, sort_keys=True))
    manifest_bytes = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    manifest_path = EVIDENCE / "r299_input_manifest.jsonl"
    manifest_sha = write_once(manifest_path, manifest_bytes)

    candidate_config = {
        "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "start": str(START), "end": str(END), "matching": "open_t+1",
        "fees_pct": 0.0006, "commission_pct": 0.0012, "stamp_tax_pct": 0.001,
        "slippage_bps": 15.0, "max_positions": 10, "max_exposure_pct": 1.0,
        "holding_days": 20, "asset_type": "stock", "position_sizing": "equal",
        "mode": "position", "initial_capital": 1000000.0, "params": {}, "overrides": {},
    }
    preregistration = {
        "round": ROUND, "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pre_registered_before_corrected_cost_system_reruns",
        "classification": "post_hoc_cost_semantics_sensitivity_audit_not_alpha_confirmation",
        "question": "Do R257's frozen candidate and matched control still meet the original R257 gates when the separately documented 6bp fee and 6bp commission are both charged by the system engine?",
        "selection_note": "This audit was prompted by reading the engine cost contract after R257/R298 results were known. It is a correction/sensitivity analysis and cannot be treated as a new or independent alpha discovery.",
        "window": [str(START), str(END)], "segments": [[str(a), str(b)] for a, b in SEGMENTS],
        "strategies": {"candidate": "ai_hs300_quality_relative_strength_h20_sl20", "control": "ai_hs300_quality_relative_strength_control"},
        "factor": "Existing R257 factor values only; no builder run. Historical HS300 membership and the original nonfinancial filter are retained.",
        "execution": {
            "engine": "StrategyBacktestService using the existing R253 segment runner with an R299 round-scoped cache",
            "matching": "open_t+1", "score_limit": 30, "max_positions": 10,
            "position_sizing": "equal", "holding_days": 20, "stop_loss": -0.20,
            "declared_3x_components_per_side": {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct_sell": 0.001, "slippage_bps_per_side": 15.0},
            "engine_contract": "commission_pct overrides fees_pct; fees_pct is only a fallback. The corrected run sets commission_pct to 0.0012 to represent the documented 6bp fee plus 6bp commission on each side, while retaining fees_pct=0.0006 for provenance.",
            "corrected_3x_components": {"fees_pct_legacy_fallback": 0.0006, "commission_pct_effective": 0.0012, "stamp_tax_pct_sell": 0.001, "slippage_bps_per_side": 15.0},
            "stress": {"4x": "multiply each declared component by 4/3", "5x": "multiply each declared component by 5/3"},
            "cache_dir": "data/research/r299_cost_audit_cache"},
        "gates": r257_result["decision"],
        "gate_definitions_source": "R257 preregistration and original result; thresholds are not changed",
        "primary_decision_rule": "Report every original R257 gate under corrected costs. Passing would only show this cost correction does not overturn the historical result; it does not clear concentration failures, the R298 pre-discovery loss, PIT limitations, or production.",
        "panel_candidate": {"kind": "strategy", "name": "R299 R257成本口径更正复算",
                              "source_id": candidate_config["strategy_id"], "config": candidate_config,
                              "metrics": {}, "data_as_of": str(END), "status": "pending"},
        "feasibility_path": "evidence/r299_feasibility.json", "feasibility_sha256": feasibility_sha,
        "input_manifest_path": "evidence/r299_input_manifest.jsonl", "input_manifest_sha256": manifest_sha,
        "input_manifest_file_count": len(manifest_lines),
        "r257_original_result_sha256": sha_file(r257_result_path),
        "r257_preregistration_sha256": sha_file(EVIDENCE / "r257_preregistration.json"),
        "r298_corrected_cost_rerun": "Not included in the primary run. The R298 negative result used a lower effective cost; correcting it can only increase charged costs and its recorded failure remains non-supportive.",
        "side_effect_boundary": "Only round-scoped R299 research cache and evidence are written. No provider, builder, formal market/factor data, strategy/backtest write API, paper account, or order flow is called.",
        "source_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
    }
    prereg_path = EVIDENCE / "r299_preregistration.json"
    prereg_sha = write_once(prereg_path, (json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"feasibility": feasibility, "input_manifest_sha256": manifest_sha,
                      "input_manifest_count": len(manifest_lines), "preregistration_sha256": prereg_sha}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
