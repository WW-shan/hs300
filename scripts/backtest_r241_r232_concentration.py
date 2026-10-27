#!/usr/bin/env python3
"""R241: concentration audit of the R232 three-factor composite."""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r241_r232_concentration.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
STRATEGY = "ai_hs300_sga_ocf_gm_trend_composite_h20_sl20"
SEGMENTS = [
    ("discovery", date(2022, 7, 1), date(2024, 12, 31)),
    ("validation", date(2025, 1, 2), date(2026, 10, 16)),
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _aggregate(trades: list[dict], industry: dict[str, str]) -> dict:
    return _load(R239_SCRIPT, "r239_concentration_aggregation")._aggregate(trades, industry)


def _run(service, membership, r112, label: str, start: date, end: date) -> dict:
    cfg = r112.StrategyBacktestConfig(
        strategy_id=STRATEGY, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position", asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{label} {start}~{end}: {result.error}")
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
    return {"label": label, "start": str(start), "end": str(end), "stats": result.stats, "trades": trades, "elapsed_seconds": round(time.perf_counter() - started, 3)}


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r221 = _load(R221_SCRIPT, "r221")
    membership = r221._nonfin_membership(r221._membership_with_industry())
    service = r112._service()
    from app.services.industry_map import load_industry_l1
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    runs = []
    trades: list[dict] = []
    for label, start, end in SEGMENTS:
        run = _run(service, membership, r112, label, start, end)
        runs.append({k: run[k] for k in ("label", "start", "end", "stats", "elapsed_seconds")})
        trades.extend(run["trades"])
        print(f"{label}: trades={len(run['trades'])} total_return={run['stats'].get('total_return')}", flush=True)

    full = _aggregate(trades, industry)
    validation = _aggregate([t for t in trades if t["segment"] == "validation"], industry)
    validation_positive = sum(1 for row in validation["per_symbol"] if row["pnl_amount"] > 0)

    c1 = full["distinct_symbols"] >= 30
    c2 = full["top1_net_share"] <= 0.25
    c3 = full["top3_net_share"] <= 0.55
    c4 = full["top5_net_share"] <= 0.75
    c5 = validation_positive >= 8
    c6 = full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45
    c7 = full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20
    decision = {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R241 concentration audit of the R232 three-factor composite",
        "preregistration": (EVIDENCE / "r241_preregistration.json").relative_to(ROOT).as_posix(),
        "strategy": STRATEGY,
        "execution": {"engine": "StrategyBacktestService", "matching": "open_t+1", "max_positions": 10, "holding_days": 20, "stop_loss": -0.2, "cost": "3x baseline"},
        "runs": runs,
        "summary": {
            "full": {k: v for k, v in full.items() if k != "per_symbol"},
            "validation": {k: v for k, v in validation.items() if k != "per_symbol"},
            "validation_positive_symbols": validation_positive,
        },
        "per_symbol": full["per_symbol"],
        "trades": trades,
        "decision": decision,
        "caveats": [
            "Trade P&L amounts from two segments each start from 1,000,000 notional, so shares are contribution-share diagnostics, not compounded-return decomposition.",
            "Industry classification is a current snapshot, not historical PIT.",
            "The audit describes concentration, not causality.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
