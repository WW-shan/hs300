#!/usr/bin/env python3
"""R237: cost and capacity stress test of the R221 all-pass candidate."""
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
OUT = EVIDENCE / "r237_r221_stress.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
CACHE = DATA / "research" / "r237_cache"
START = date(2022, 7, 1)
END = date(2026, 10, 16)
SEGMENTS = [
    (date(2022, 7, 1), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGY = "ai_hs300_sga_nonfin_quality_h20_sl20"
VARIANTS = [
    {"key": "c3_mp5", "cost": 3.0, "max_positions": 5},
    {"key": "c3_mp10", "cost": 3.0, "max_positions": 10},
    {"key": "c3_mp20", "cost": 3.0, "max_positions": 20},
    {"key": "c4_mp5", "cost": 4.0, "max_positions": 5},
    {"key": "c4_mp10", "cost": 4.0, "max_positions": 10},
    {"key": "c4_mp20", "cost": 4.0, "max_positions": 20},
    {"key": "c5_mp5", "cost": 5.0, "max_positions": 5},
    {"key": "c5_mp10", "cost": 5.0, "max_positions": 10},
    {"key": "c5_mp20", "cost": 5.0, "max_positions": 20},
    {"key": "c6_mp10", "cost": 6.0, "max_positions": 10},
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
        strategy_id=STRATEGY, symbols=None, start=start, end=end,
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
    service = r221._service() if hasattr(r221, "_service") else r112._service()

    with (EVIDENCE / "r221_sga_nonfin_quality.json").open(encoding="utf-8") as handle:
        r221_evidence = json.load(handle)
    baseline_evidence = r221_evidence["results"]["candidate"]
    factor_evidence = r221_evidence["factor"]["coverage"]

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
        variant_results[variant["key"]] = {
            **variant,
            "runs": runs,
            "result": r221._result(r112, stitched),
        }

    def result(key: str) -> dict:
        return variant_results[key]["result"]

    baseline = result("c3_mp10")
    r1 = abs(baseline["full"]["total_return"] - baseline_evidence["full"]["total_return"]) <= 0.005
    r2 = result("c4_mp10")["full"]["total_return"] > 0
    r3 = result("c5_mp10")["full"]["total_return"] > 0
    r4 = result("c4_mp10")["recent_12m"]["total_return"] > 0
    r5 = all(result(f"c3_mp{mp}")["full"]["total_return"] > 0 for mp in (5, 10, 20))
    r6 = all(result(f"c3_mp{mp}")["recent_12m"]["total_return"] > 0 for mp in (5, 10, 20))
    stress_keys = [variant["key"] for variant in VARIANTS if variant["cost"] in (3.0, 4.0)]
    worst_full = min(result(key)["full"]["total_return"] for key in stress_keys)
    worst_mdd = min(result(key)["full"]["mdd"] for key in stress_keys)
    r7 = worst_full > -0.05
    r8 = worst_mdd >= -0.45
    r9 = (
        baseline["rolling_1y"]["positive_rate"] >= 0.55
        and baseline["rolling_1y"]["return_p05"] > -0.20
    )
    decision = {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5, "R6": r6, "R7": r7, "R8": r8, "R9": r9}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R237 cost and capacity stress test of the R221 all-pass candidate",
        "preregistration": (EVIDENCE / "r237_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "factor": {
            "id": "ext_alt_hs300_sga_ratio_4q_low",
            "frozen_from": "R221",
            "coverage": factor_evidence,
        },
        "strategy": {
            "id": STRATEGY,
            "scoring": r221_evidence["strategies"]["candidate"],
            "holding_days": 20,
            "stop_loss": -0.2,
            "position_sizing": "equal",
        },
        "stress_matrix": {
            "cost_multipliers": [3.0, 4.0, 5.0, 6.0],
            "max_positions": [5, 10, 20],
            "baseline": "c3_mp10",
            "cost_definition": "3x baseline: 6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax; higher multipliers scale all four components proportionally",
        },
        "baseline_evidence": baseline_evidence,
        "variants": variant_results,
        "summary": {
            "worst_full_3x_4x": round(worst_full, 6),
            "worst_mdd_3x_4x": round(worst_mdd, 6),
            "baseline_full": baseline["full"],
            "baseline_recent_12m": baseline["recent_12m"],
            "baseline_rolling_1y": baseline["rolling_1y"],
        },
        "decision": decision,
        "caveats": [
            "This is a stress test of an existing factor, not an independent new alpha discovery.",
            "Cost multipliers scale the baseline 3x model proportionally; they are not a full market-impact model.",
            "Position count changes concentration but does not model capacity in yuan.",
            "R221 MDD is already -32.97%; the stress test only rejects if MDD worsens beyond -45%.",
            "Passing these gates would justify paper trading, not production deployment.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
