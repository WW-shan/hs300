#!/usr/bin/env python3
"""R264: trade-level concentration audit of the R131 dual-sleeve candidate."""

from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r264_r131_concentration_audit.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
CACHE = DATA / "research" / "r264_cache"
A_SID = "ai_hs300_macro_gate_quality"
B_SID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
RECENT_START = date(2025, 10, 14)
CACHE_A = DATA / "research" / "r125_cache" / f"{A_SID}_2021-06-01_2026-10-16_3x.parquet"
CACHE_B = DATA / "research" / "r119_cache" / f"{B_SID}_2021-06-01_2026-10-16.parquet"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _parse_day(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _run_sleeve(service, membership, r112, strategy_id: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    curve_path = CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}.parquet"
    trades_path = CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}.trades.json"
    if curve_path.exists() and trades_path.exists():
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return curve, json.loads(trades_path.read_text(encoding="utf-8"))

    cfg = r112.StrategyBacktestConfig(
        strategy_id=strategy_id, symbols=None, start=START, end=END,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id}: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [
        {
            "symbol": t.get("symbol"),
            "name": t.get("name"),
            "entry_date": str(t.get("entry_date")),
            "exit_date": str(t.get("exit_date")),
            "pnl_pct": t.get("pnl_pct"),
            "pnl_amount": t.get("pnl_amount"),
            "exit_reason": t.get("exit_reason"),
        }
        for t in result.trades
    ]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"ran {strategy_id}: days={curve.height} trades={len(trades)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    return curve, trades


def _rebase(frame: pl.DataFrame) -> pl.DataFrame:
    first = frame["value"][0]
    return frame.with_columns((pl.col("value") / first).alias("value"))


def _parity(cached: pl.DataFrame, rerun: pl.DataFrame) -> dict:
    joined = (
        _rebase(cached).rename({"value": "cached"})
        .join(_rebase(rerun).rename({"value": "rerun"}), on="date", how="inner")
        .sort("date")
    )
    diff = (joined["cached"] - joined["rerun"]).abs()
    return {
        "days": joined.height,
        "max_abs_diff": round(float(diff.max() or 0.0), 12),
        "mean_abs_diff": round(float(diff.mean() or 0.0), 12),
        "pass": bool(joined.height > 0 and float(diff.max() or 0.0) <= 1e-6),
    }


def _weight_at(records: list[dict], day: date, key: str) -> float:
    value = records[0][key]
    for record in records:
        if record["date"] <= day:
            value = record[key]
        else:
            break
    return float(value)


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r264")
    r114 = _load(R114_SCRIPT, "r114_r264")
    r239 = _load(R239_SCRIPT, "r239_r264")
    r114.TARGET_VOL = 0.15

    membership = r112._historical_membership()
    service = r112._service()

    a_cached = pl.read_parquet(CACHE_A).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    b_cached = pl.read_parquet(CACHE_B).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    a_curve, a_trades = _run_sleeve(service, membership, r112, A_SID)
    b_curve, b_trades = _run_sleeve(service, membership, r112, B_SID)

    parity = {"A": _parity(a_cached, a_curve), "B": _parity(b_cached, b_curve)}

    risk, rp_records = r112._combine(a_curve, b_curve, "risk")
    vol = r114._vol_target(risk)
    exposure_records = vol["weight_records"]

    scaled = []
    for t in a_trades:
        day = _parse_day(t["entry_date"])
        scale = _weight_at(rp_records, day, "weight_a") * _weight_at(exposure_records, day, "weight")
        scaled.append({**t, "sleeve": "A", "weight_scale": round(scale, 6), "pnl_amount": float(t["pnl_amount"] or 0.0) * scale})
    for t in b_trades:
        day = _parse_day(t["entry_date"])
        scale = (1.0 - _weight_at(rp_records, day, "weight_a")) * _weight_at(exposure_records, day, "weight")
        scaled.append({**t, "sleeve": "B", "weight_scale": round(scale, 6), "pnl_amount": float(t["pnl_amount"] or 0.0) * scale})

    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    full = r239._aggregate(scaled, industry)
    recent = r239._aggregate([t for t in scaled if _parse_day(t["entry_date"]) >= RECENT_START], industry)
    recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)

    sleeve_pnl = {
        "A": round(sum(t["pnl_amount"] for t in scaled if t["sleeve"] == "A"), 2),
        "B": round(sum(t["pnl_amount"] for t in scaled if t["sleeve"] == "B"), 2),
    }
    total = full["total_pnl_amount"]
    sleeve_share = {k: round(v / total, 6) if total > 0 else None for k, v in sleeve_pnl.items()}

    paper = json.loads((EVIDENCE / "r152_paper_trading_state.json").read_text(encoding="utf-8"))

    p1 = all(parity[k]["pass"] for k in ("A", "B"))
    p2 = bool(paper.get("decision", {}).get("all_pass"))
    c1 = full["distinct_symbols"] >= 30
    c2 = full["total_pnl_amount"] > 0 and full["top1_net_share"] <= 0.25
    c3 = full["total_pnl_amount"] > 0 and full["top3_net_share"] <= 0.55
    c4 = full["total_pnl_amount"] > 0 and full["top5_net_share"] <= 0.75
    c5 = recent_positive >= 8
    c6 = full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45
    c7 = full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20
    decision = {"P1": p1, "P2": p2, "C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7}
    decision["all_pass"] = all(decision.values())

    payload = {
        "round": "R264",
        "purpose": "Trade-level concentration audit of the R131 dual-sleeve candidate and paper-trading readiness.",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r264_preregistration.json",
        "window": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "sleeves": {
            "A": {"strategy": A_SID, "trades": len(a_trades)},
            "B": {"strategy": B_SID, "trades": len(b_trades)},
        },
        "parity": parity,
        "portfolio": {
            "risk_parity_weight_a_last": rp_records[-1]["weight_a"],
            "vol_target_exposure_last": exposure_records[-1]["weight"],
            "risk_parity_records": len(rp_records),
            "exposure_records": len(exposure_records),
        },
        "sleeve_attributed_pnl": {"amounts": sleeve_pnl, "shares": sleeve_share},
        "concentration_full": full,
        "concentration_recent_12m": recent,
        "recent_positive_symbols": recent_positive,
        "paper_trading_state": paper,
        "decision": decision,
        "caveats": [
            "Attribution uses monthly risk-parity weights and monthly vol-target exposures at trade entry.",
            "A/B sleeves were independently backtested with 1M starting capital; weighting is an approximation.",
            "Industry classification is the current snapshot, not historical PIT.",
            "R131 return is concentrated in 2023-2026; 2018-2022 was weak.",
            "Paper trading is not production deployment.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "parity": parity,
        "full": {k: full.get(k) for k in ("n_trades", "distinct_symbols", "total_pnl_amount", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "recent": {k: recent.get(k) for k in ("n_trades", "distinct_symbols", "total_pnl_amount")},
        "sleeve_pnl": sleeve_pnl,
        "decision": decision,
    }, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
