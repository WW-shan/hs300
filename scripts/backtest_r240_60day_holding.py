#!/usr/bin/env python3
"""R240: 60-day holding test of the R221 10-position strategy, with concentration audit."""
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
OUT = EVIDENCE / "r240_60day_holding.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
CACHE = DATA / "research" / "r240_cache"
START = date(2022, 7, 1)
END = date(2026, 10, 16)
SEGMENTS = [
    ("discovery", date(2022, 7, 1), date(2024, 12, 31)),
    ("validation", date(2025, 1, 2), END),
]
CANDIDATE = "ai_hs300_sga_nonfin_quality_h60_sl20"
CONTROL = "ai_hs300_sga_nonfin_quality_h20_sl20"
VARIANTS = [
    {"key": "candidate_3x", "strategy_id": CANDIDATE, "cost": 3.0, "max_positions": 10},
    {"key": "candidate_4x", "strategy_id": CANDIDATE, "cost": 4.0, "max_positions": 10},
    {"key": "candidate_5x", "strategy_id": CANDIDATE, "cost": 5.0, "max_positions": 10},
    {"key": "control_3x", "strategy_id": CONTROL, "cost": 3.0, "max_positions": 10},
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_variant(service, membership, r112, r221, label: str, start: date, end: date, variant: dict) -> tuple[pl.DataFrame, dict, list[dict]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    scale = variant["cost"] / 3.0
    path = CACHE / f"{variant['key']}_{label}_{start.isoformat()}_{end.isoformat()}.parquet"
    if path.exists() and path.stat().st_size:
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}, []
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
            trades = []
        else:
            raise RuntimeError(f"{variant['key']} {label} {start}~{end}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
        trades = [
            {
                "segment": label,
                "symbol": trade.get("symbol"),
                "name": trade.get("name"),
                "pnl_amount": trade.get("pnl_amount"),
                "pnl_pct": trade.get("pnl_pct"),
                "entry_date": trade.get("entry_date"),
                "exit_date": trade.get("exit_date"),
                "exit_reason": trade.get("exit_reason"),
            }
            for trade in result.trades
        ]
    frame.write_parquet(path)
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }, trades


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r221 = _load(R221_SCRIPT, "r221")
    r239 = _load(R239_SCRIPT, "r239")
    membership = r221._nonfin_membership(r221._membership_with_industry())
    service = r112._service()
    from app.services.industry_map import load_industry_l1
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

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
        and candidate_meta.required_features == control_meta.required_features
        and candidate_meta.max_hold_days == 60
        and control_meta.max_hold_days == 20
    )

    variant_results: dict[str, dict] = {}
    variant_trades: dict[str, list[dict]] = {}
    for variant in VARIANTS:
        curves = []
        runs = []
        trades: list[dict] = []
        for label, start, end in SEGMENTS:
            curve, info, segment_trades = _run_variant(service, membership, r112, r221, label, start, end, variant)
            curves.append(curve)
            trades.extend(segment_trades)
            runs.append({"segment": label, "start": str(start), "end": str(end), **info})
            print(f"{variant['key']} {label}: {info['curve_stats']}", flush=True)
            gc.collect()
        stitched = r221._stitch(curves)
        variant_results[variant["key"]] = {**variant, "runs": runs, "result": r221._result(r112, stitched)}
        variant_trades[variant["key"]] = trades

    def result(key: str) -> dict:
        return variant_results[key]["result"]

    cand = result("candidate_3x")
    ctrl = result("control_3x")
    cand_trades = variant_trades["candidate_3x"]
    ctrl_trades = variant_trades["control_3x"]
    cand_conc = r239._aggregate(cand_trades, industry)
    ctrl_conc = r239._aggregate(ctrl_trades, industry)
    cand_validation = [t for t in cand_trades if t["segment"] == "validation"]
    validation_positive = sum(1 for row in r239._aggregate(cand_validation, industry)["per_symbol"] if row["pnl_amount"] > 0)

    d1 = factor_evidence["coverage_2022_2026"] >= 0.90
    d2 = factor_evidence["first_valid_date"] <= "2022-06-30" and factor_evidence["recent_12m_coverage"] >= 0.95
    g1 = cand["full"]["total_return"] >= ctrl["full"]["total_return"] - 0.02
    g2 = result("candidate_4x")["full"]["total_return"] > 0
    g3 = result("candidate_5x")["full"]["total_return"] > 0
    g4 = cand["recent_12m"]["total_return"] > 0
    g5 = cand["full"]["mdd"] >= ctrl["full"]["mdd"] - 0.05
    g6 = cand["rolling_1y"]["positive_rate"] >= ctrl["rolling_1y"]["positive_rate"] - 0.05
    g7 = cand_conc["top1_net_share"] <= 0.25
    g8 = cand_conc["top3_net_share"] <= 0.55
    g9 = cand_conc["distinct_symbols"] >= 30 and validation_positive >= 8
    g10 = cand["validation_2025_2026"]["total_return"] >= ctrl["validation_2025_2026"]["total_return"] - 0.05
    g11 = cand["full"]["total_return"] >= r221_evidence["results"]["candidate"]["full"]["total_return"] - 0.05
    decision = {"D1": d1, "D2": d2, "D3": d3, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7, "G8": g8, "G9": g9, "G10": g10, "G11": g11}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R240 60-day holding test of the R221 10-position strategy with concentration audit",
        "preregistration": (EVIDENCE / "r240_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "factor": {"id": "ext_alt_hs300_sga_ratio_4q_low", "coverage": factor_evidence},
        "strategies": {"candidate": CANDIDATE, "control": CONTROL, "candidate_scoring": candidate_meta.meta["scoring"], "control_scoring": control_meta.meta["scoring"]},
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "base_cost": "3x: 6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 10,
            "candidate_hold_days": 60,
            "control_hold_days": 20,
            "stop_loss": -0.2,
        },
        "variants": variant_results,
        "concentration": {
            "candidate": {k: v for k, v in cand_conc.items() if k != "per_symbol"},
            "control": {k: v for k, v in ctrl_conc.items() if k != "per_symbol"},
            "candidate_per_symbol": cand_conc["per_symbol"],
            "candidate_validation_positive_symbols": validation_positive,
        },
        "decision": decision,
        "caveats": [
            "The 60-day holding period is a turnover hypothesis, not an optimized parameter.",
            "A 60-day hold can increase exposure to adverse reversals and reduces the number of independent trades.",
            "Concentration gates use trade P&L shares and are diagnostics, not causal tests.",
            "Industry classification is a current snapshot, not historical PIT.",
            "Passing gates would justify paper trading only.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": cand, "control": ctrl, "concentration": payload["concentration"], "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
