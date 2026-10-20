#!/usr/bin/env python3
"""R238: result-driven five-position qualification of the R221 factor."""
from __future__ import annotations

import gc
import importlib.util
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r238_five_position.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
CACHE = DATA / "research" / "r238_cache"
START = date(2022, 7, 1)
END = date(2026, 10, 16)
SEGMENTS = [
    (date(2022, 7, 1), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
CANDIDATE = "ai_hs300_sga_nonfin_quality_h5_sl20"
CONTROL = "ai_hs300_sga_nonfin_quality_h20_sl20"
VARIANTS = [
    {"key": "candidate_3x_5", "strategy_id": CANDIDATE, "cost": 3.0, "max_positions": 5},
    {"key": "candidate_4x_5", "strategy_id": CANDIDATE, "cost": 4.0, "max_positions": 5},
    {"key": "candidate_5x_5", "strategy_id": CANDIDATE, "cost": 5.0, "max_positions": 5},
    {"key": "control_3x_10", "strategy_id": CONTROL, "cost": 3.0, "max_positions": 10},
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_variant(service, membership, r112, r221, start: date, end: date, variant: dict) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    scale = variant["cost"] / 3.0
    path = CACHE / f"{variant['key']}_{start.isoformat()}_{end.isoformat()}.parquet"
    if path.exists() and path.stat().st_size:
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}
    cfg = r112.StrategyBacktestConfig(
        strategy_id=variant["strategy_id"], symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006 * scale,
        commission_pct=0.0006 * scale,
        stamp_tax_pct=0.001 * scale,
        slippage_bps=15.0 * scale,
        max_positions=variant["max_positions"],
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "\u672a\u4ea7\u751f\u4e70\u5165\u4fe1\u53f7" in str(result.error):
            frame = pl.DataFrame({"date": r221._benchmark_dates(start, end)}).with_columns(pl.lit(1_000_000.0).alias("value"))
        else:
            raise RuntimeError(f"{variant['key']} {start}~{end}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
        "factor_attribution": result.factor_attribution,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r221 = _load(R221_SCRIPT, "r221")
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    service = r112._service()

    with (EVIDENCE / "r221_sga_nonfin_quality.json").open(encoding="utf-8") as handle:
        r221_evidence = json.load(handle)
    factor_evidence = r221_evidence["factor"]["coverage"]

    from app.strategy.engine import StrategyEngine
    engine = StrategyEngine(strategy_dirs=[DATA / "strategies" / "ai"])
    candidate_meta = engine.get(CANDIDATE)
    control_meta = engine.get(CONTROL)
    d3 = (
        candidate_meta.meta["scoring"] == control_meta.meta["scoring"]
        and candidate_meta.stop_loss == control_meta.stop_loss
        and candidate_meta.max_hold_days == control_meta.max_hold_days
        and candidate_meta.required_features == control_meta.required_features
    )

    variant_results: dict[str, dict] = {}
    for variant in VARIANTS:
        segment_curves = []
        runs = []
        for start, end in SEGMENTS:
            curve, info = _run_variant(service, membership, r112, r221, start, end, variant)
            segment_curves.append(curve)
            runs.append({"start": str(start), "end": str(end), **info})
            print(f"{variant['key']} {start}~{end}: {info['curve_stats']}", flush=True)
            gc.collect()
        stitched = r221._stitch(segment_curves)
        variant_results[variant["key"]] = {**variant, "runs": runs, "result": r221._result(r112, stitched)}

    def result(key: str) -> dict:
        return variant_results[key]["result"]

    cand = result("candidate_3x_5")
    ctrl = result("control_3x_10")
    d1 = factor_evidence["coverage_2022_2026"] >= 0.90
    d2 = factor_evidence["first_valid_date"] <= "2022-06-30" and factor_evidence["recent_12m_coverage"] >= 0.95
    g1 = cand["full"]["total_return"] >= ctrl["full"]["total_return"] + 0.10
    g2 = cand["full"]["mdd"] >= ctrl["full"]["mdd"] - 0.05
    g3 = cand["recent_12m"]["total_return"] >= ctrl["recent_12m"]["total_return"] + 0.10
    g4 = cand["rolling_1y"]["positive_rate"] >= ctrl["rolling_1y"]["positive_rate"]
    g5 = result("candidate_4x_5")["full"]["total_return"] > 0
    g6 = result("candidate_5x_5")["full"]["total_return"] > 0
    g7 = result("candidate_4x_5")["full"]["mdd"] >= -0.45
    g8 = cand["discovery_2022_2024"]["total_return"] >= ctrl["discovery_2022_2024"]["total_return"] - 0.02
    g9 = cand["validation_2025_2026"]["total_return"] >= ctrl["validation_2025_2026"]["total_return"] + 0.10
    decision = {"D1": d1, "D2": d2, "D3": d3, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7, "G8": g8, "G9": g9}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R238 result-driven five-position qualification of the R221 SGA factor",
        "preregistration": (EVIDENCE / "r238_preregistration.json").relative_to(ROOT).as_posix(),
        "result_driven_disclosure": "The 5-position configuration was selected after R237. This is not independent evidence and only qualifies for paper trading.",
        "window": [str(START), str(END)],
        "factor": {"id": "ext_alt_hs300_sga_ratio_4q_low", "coverage": factor_evidence},
        "strategies": {"candidate": CANDIDATE, "control": CONTROL, "candidate_scoring": candidate_meta.meta["scoring"], "control_scoring": control_meta.meta["scoring"]},
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "baseline_cost": "3x: 6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "candidate_max_positions": 5,
            "control_max_positions": 10,
            "holding_days": 20,
            "stop_loss": -0.2,
        },
        "variants": variant_results,
        "decision": decision,
        "caveats": [
            "The 5-position configuration is result-driven, not an independent discovery.",
            "A 5-position portfolio is highly concentrated and has higher idiosyncratic risk.",
            "R237 rolling worst 1-year return for the 5-position variant was -26.78%, worse than the 10-position baseline's -21.21%.",
            "Passing these gates qualifies only for paper trading, not production.",
            "Cost stress scales the baseline model proportionally and is not a full market-impact model.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": cand, "control": ctrl, "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
