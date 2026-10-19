#!/usr/bin/env python3
"""R140: rerun the event/behaviour strategy batch on the fixed data."""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r140_event_batch_fixed.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
STRATEGIES = [
    "ai_hs300_lhb_quality",
    "ai_hs300_margin_quality",
    "ai_hs300_pledge_quality",
    "ai_hs300_repurchase_quality",
    "ai_hs300_block_trade_quality",
    "ai_hs300_analyst_quality",
    "ai_hs300_earnings_surprise_quality",
    "ai_hs300_goodwill_quality",
    "ai_hs300_insider_net_quality",
    "ai_hs300_dividend_yield_quality",
    "ai_hs300_low_fund_crowding_quality",
    "ai_hs300_fund_reduction_quality",
    "ai_hs300_supply_quality",
    "ai_hs300_mainline_quality",
    "ai_hs300_emotion_quality",
    "ai_hs300_risk_appetite_quality",
    "ai_hs300_macro_liquidity_quality",
    "ai_hs300_industry_momentum_quality",
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    membership = r112._historical_membership()
    service = r112._service()

    records = {}
    for strategy_id in STRATEGIES:
        cfg = r112.StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=START,
            end=END,
            params={},
            overrides={},
            matching="open_t+1",
            fees_pct=0.0006,
            commission_pct=0.0006,
            stamp_tax_pct=0.001,
            slippage_bps=15.0,
            max_positions=10,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        started = time.perf_counter()
        with r112._patched_membership(membership):
            result = service.run(cfg)
        stats = result.stats or {}
        records[strategy_id] = {
            "error": result.error,
            "total_return": stats.get("total_return"),
            "annual": stats.get("annual_return"),
            "sharpe": stats.get("sharpe"),
            "mdd": stats.get("max_drawdown"),
            "n_trades": stats.get("n_trades"),
            "elapsed": round(time.perf_counter() - started, 2),
        }
        print(
            f"{strategy_id}: err={result.error} ret={stats.get('total_return')} "
            f"sharpe={stats.get('sharpe')} mdd={stats.get('max_drawdown')} trades={stats.get('n_trades')}",
            flush=True,
        )

    valid = {k: v for k, v in records.items() if v["error"] is None and v["sharpe"] is not None}
    d1 = len(valid) == len(STRATEGIES)
    g1 = sum(1 for v in valid.values() if v["sharpe"] >= 0.5) >= 3
    g2 = any(v["sharpe"] >= 0.7 and (v["mdd"] or -9) >= -0.25 for v in valid.values())
    lhb = records.get("ai_hs300_lhb_quality", {})
    g3 = (lhb.get("sharpe") or -9) >= 0.5
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3}
    decision["all_pass"] = all(decision.values())

    ranked = sorted(valid.items(), key=lambda kv: -(kv[1]["sharpe"] or -9))
    result = {
        "purpose": "R140 event/behaviour batch rerun on fixed data",
        "window": [str(START), str(END)],
        "cost": "3x",
        "records": records,
        "ranking": [{"strategy_id": k, **v} for k, v in ranked],
        "decision": decision,
        "caveats": ["Single window; phase validation would be required before any promotion."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("top5:", [(k, v["sharpe"]) for k, v in ranked[:5]], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
