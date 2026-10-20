#!/usr/bin/env python3
"""R239: concentration and industry audit of the R238 five-position candidate."""
from __future__ import annotations

import importlib.util
import json
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r239_concentration_audit.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
STRATEGY = "ai_hs300_sga_nonfin_quality_h5_sl20"
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


def _run_segment(service, membership, r112, label: str, start: date, end: date) -> dict:
    cfg = r112.StrategyBacktestConfig(
        strategy_id=STRATEGY, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=5, position_sizing="equal", mode="position", asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{label} {start}~{end}: {result.error}")
    trades = []
    for trade in result.trades:
        trades.append({
            "segment": label,
            "symbol": trade.get("symbol"),
            "name": trade.get("name"),
            "entry_date": trade.get("entry_date"),
            "exit_date": trade.get("exit_date"),
            "pnl_pct": trade.get("pnl_pct"),
            "pnl_amount": trade.get("pnl_amount"),
            "exit_reason": trade.get("exit_reason"),
        })
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "stats": result.stats,
        "trades": trades,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _aggregate(trades: list[dict], industry: dict[str, str]) -> dict:
    total_pnl = sum(float(t["pnl_amount"] or 0.0) for t in trades)
    gross_profit = sum(max(float(t["pnl_amount"] or 0.0), 0.0) for t in trades)
    gross_loss = -sum(min(float(t["pnl_amount"] or 0.0), 0.0) for t in trades)

    per_symbol: dict[str, dict] = {}
    per_industry: dict[str, float] = defaultdict(float)
    for t in trades:
        symbol = str(t["symbol"])
        pnl = float(t["pnl_amount"] or 0.0)
        row = per_symbol.setdefault(symbol, {"symbol": symbol, "name": t.get("name"), "n_trades": 0, "pnl_amount": 0.0, "wins": 0})
        row["n_trades"] += 1
        row["pnl_amount"] += pnl
        if pnl > 0:
            row["wins"] += 1
            per_industry[industry.get(symbol, "未知")] += pnl

    symbols = sorted(per_symbol.values(), key=lambda row: row["pnl_amount"], reverse=True)
    positives = [row["pnl_amount"] for row in symbols if row["pnl_amount"] > 0]
    losses = [row["pnl_amount"] for row in symbols if row["pnl_amount"] < 0]
    top1 = positives[0] if positives else 0.0
    top3 = sum(positives[:3])
    top5 = sum(positives[:5])
    net = total_pnl if total_pnl > 0 else 1.0
    industries = sorted(({"industry": key, "gross_profit": value} for key, value in per_industry.items()), key=lambda row: row["gross_profit"], reverse=True)
    return {
        "n_trades": len(trades),
        "distinct_symbols": len(per_symbol),
        "total_pnl_amount": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_to_gross": round(total_pnl / gross_profit, 4) if gross_profit else None,
        "top1_symbol": symbols[0] if symbols else None,
        "top3_symbols": symbols[:3],
        "top5_symbols": symbols[:5],
        "worst_symbols": sorted(symbols, key=lambda row: row["pnl_amount"])[:5],
        "top1_net_share": round(top1 / net, 6),
        "top3_net_share": round(top3 / net, 6),
        "top5_net_share": round(top5 / net, 6),
        "top1_gross_share": round(top1 / gross_profit, 6) if gross_profit else None,
        "top3_gross_share": round(top3 / gross_profit, 6) if gross_profit else None,
        "top5_gross_share": round(top5 / gross_profit, 6) if gross_profit else None,
        "max_symbol_loss_abs": round(abs(min(losses)) if losses else 0.0, 2),
        "max_symbol_loss_to_gross": round(abs(min(losses)) / gross_profit, 6) if losses and gross_profit else None,
        "industries": industries,
        "top_industry_gross_share": round(industries[0]["gross_profit"] / gross_profit, 6) if industries and gross_profit else None,
        "per_symbol": symbols,
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r221 = _load(R221_SCRIPT, "r221")
    membership = r221._nonfin_membership(r221._membership_with_industry())
    service = r112._service()
    from app.services.industry_map import load_industry_l1
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    runs = []
    all_trades: list[dict] = []
    for label, start, end in SEGMENTS:
        run = _run_segment(service, membership, r112, label, start, end)
        runs.append({k: run[k] for k in ("label", "start", "end", "stats", "elapsed_seconds")})
        all_trades.extend(run["trades"])
        print(f"{label}: trades={len(run['trades'])} stats={run['stats'].get('total_return')}", flush=True)

    full = _aggregate(all_trades, industry)
    discovery = _aggregate([t for t in all_trades if t["segment"] == "discovery"], industry)
    validation = _aggregate([t for t in all_trades if t["segment"] == "validation"], industry)
    validation_positive_symbols = sum(1 for row in validation["per_symbol"] if row["pnl_amount"] > 0)

    c1 = full["distinct_symbols"] >= 30
    c2 = full["top1_net_share"] <= 0.25
    c3 = full["top3_net_share"] <= 0.55
    c4 = full["top5_net_share"] <= 0.75
    c5 = validation_positive_symbols >= 8
    c6 = validation["top1_gross_share"] is not None and validation["top1_gross_share"] <= 0.35
    c7 = full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45
    c8 = full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20
    decision = {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7, "C8": c8}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R239 concentration and industry audit of the R238 five-position candidate",
        "preregistration": (EVIDENCE / "r239_preregistration.json").relative_to(ROOT).as_posix(),
        "strategy": STRATEGY,
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "max_positions": 5,
            "holding_days": 20,
            "stop_loss": -0.2,
            "cost": "3x: 6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
        },
        "runs": runs,
        "summary": {
            "full": {k: v for k, v in full.items() if k != "per_symbol"},
            "discovery": {k: v for k, v in discovery.items() if k != "per_symbol"},
            "validation": {k: v for k, v in validation.items() if k != "per_symbol"},
            "validation_positive_symbols": validation_positive_symbols,
        },
        "per_symbol": {
            "full": full["per_symbol"],
            "discovery": discovery["per_symbol"],
            "validation": validation["per_symbol"],
        },
        "trades": all_trades,
        "decision": decision,
        "caveats": [
            "Trade P&L amounts from two segments each start from 1,000,000 notional, so shares are contribution-share diagnostics, not compounded-return decomposition.",
            "Industry classification is a current snapshot, not historical PIT.",
            "The audit describes concentration, not causality.",
            "Passing concentration gates does not override the result-driven nature of the five-position choice.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
