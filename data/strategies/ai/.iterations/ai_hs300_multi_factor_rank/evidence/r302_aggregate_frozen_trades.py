from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
TRADE_CACHE = ROOT / "data/research/r299_cost_audit_cache"
OUT = EVIDENCE / "r302_segment_concentration.json"
INPUT_MANIFEST = EVIDENCE / "r302_analysis_input_manifest.jsonl"
EXEC_AMENDMENT = EVIDENCE / "r302_analysis_execution_amendment.json"

SEGMENTS = {
    "discovery": "2023-05-04_2024-12-31",
    "validation": "2025-01-02_2026-10-16",
}
STRATEGIES = {
    "candidate": "ai_hs300_quality_relative_strength_h20_sl20",
    "control": "ai_hs300_quality_relative_strength_control",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze(trades: list[dict], days: int, source: str) -> dict:
    by_symbol: dict[str, dict] = defaultdict(
        lambda: {"net_pnl": 0.0, "trades": 0, "positive_trades": 0, "name": None}
    )
    amounts: list[float] = []
    returns: list[float] = []
    gross_profit = 0.0
    gross_loss = 0.0
    for trade in trades:
        amount = float(trade.get("pnl_amount") or 0.0)
        pct = float(trade.get("pnl_pct") or 0.0)
        row = by_symbol[str(trade["symbol"])]
        row["net_pnl"] += amount
        row["trades"] += 1
        row["positive_trades"] += amount > 0
        row["name"] = trade.get("name")
        amounts.append(amount)
        returns.append(pct)
        gross_profit += max(amount, 0.0)
        gross_loss += max(-amount, 0.0)

    ranked = sorted(by_symbol.items(), key=lambda item: (-item[1]["net_pnl"], item[0]))
    positive_symbols = [(symbol, row) for symbol, row in ranked if row["net_pnl"] > 0]
    net_pnl = sum(row["net_pnl"] for row in by_symbol.values())
    sorted_returns = sorted(returns)
    middle = len(sorted_returns) // 2
    median_return = (
        sorted_returns[middle]
        if len(sorted_returns) % 2
        else (sorted_returns[middle - 1] + sorted_returns[middle]) / 2
    ) if sorted_returns else None

    def share(count: int, denominator: float) -> float | None:
        if denominator <= 0:
            return None
        return sum(row["net_pnl"] for _, row in positive_symbols[:count]) / denominator

    top_symbols = positive_symbols[:5]
    return {
        "trade_count": len(trades),
        "trading_days": days,
        "distinct_symbols": len(by_symbol),
        "positive_net_symbols": len(positive_symbols),
        "negative_net_symbols": sum(row["net_pnl"] < 0 for row in by_symbol.values()),
        "trade_win_rate": sum(amount > 0 for amount in amounts) / len(amounts) if amounts else None,
        "total_net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss_abs": round(gross_loss, 2),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "mean_trade_pnl_pct": sum(returns) / len(returns) if returns else None,
        "median_trade_pnl_pct": median_return,
        "top1_net_share": share(1, net_pnl),
        "top3_net_share": share(3, net_pnl),
        "top5_net_share": share(5, net_pnl),
        "top1_gross_profit_share": share(1, gross_profit),
        "top3_gross_profit_share": share(3, gross_profit),
        "top5_gross_profit_share": share(5, gross_profit),
        "top5_symbols_by_net_pnl": [
            {"symbol": symbol, "name": row["name"], "trades": row["trades"], "net_pnl": round(row["net_pnl"], 2)}
            for symbol, row in top_symbols
        ],
        "source_trade_cache": source,
    }


def main() -> None:
    if OUT.exists() or OUT.with_name(OUT.name + ".sha256").exists():
        raise RuntimeError("R302 result exists; refusing to overwrite")
    amendment = json.loads(EXEC_AMENDMENT.read_text(encoding="utf-8"))
    manifest_raw = INPUT_MANIFEST.read_bytes()
    if amendment["input_manifest_sha256"] != hashlib.sha256(manifest_raw).hexdigest():
        raise RuntimeError("R302 analysis input manifest hash mismatch")
    if amendment["aggregation_script_sha256"] != sha(Path(__file__)):
        raise RuntimeError("R302 aggregation script hash mismatch")

    frozen = [json.loads(line) for line in manifest_raw.decode("utf-8").splitlines() if line]
    for item in frozen:
        path = ROOT / item["path"]
        if not path.is_file():
            raise RuntimeError(f"Frozen analysis input missing: {item['path']}")
        actual = {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns, "sha256": sha(path)}
        expected = {key: item[key] for key in actual}
        if actual != expected:
            raise RuntimeError(f"Frozen analysis input changed: {item['path']}")

    result = {
        "round": "R302",
        "classification": "post_hoc_descriptive_segment_trade_attribution_not_alpha_confirmation",
        "registered_panel_candidate_id": "f438e8b03baa464d9bf7b44d891580c9",
        "registered_panel_status": "pending",
        "parent_strategy_round": "R257",
        "parent_cost_corrected_system_rerun": "R299",
        "cost_model": {"fees_pct_provenance": 0.0006, "commission_pct_effective": 0.0012, "stamp_tax_pct_sell": 0.001, "slippage_bps_per_side": 15.0},
        "segments": {},
        "entry_year_trade_attribution": {},
        "original_concentration_gates": {"C2": 0.25, "C3": 0.55, "C4": 0.75},
        "limitations": [
            "Trade-level symbol P&L attribution is not a portfolio return decomposition.",
            "Segment log P&L starts from separate 1,000,000 initial capital runs; do not add segment profits as compounded equity.",
            "R257/R299 outcomes and segment boundaries were already inspected; this is not blind or independent validation.",
            "Current industry/financial PIT and historical constituent limitations remain unresolved.",
        ],
        "input_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "aggregation_script_sha256": sha(Path(__file__)),
    }

    for segment, date_range in SEGMENTS.items():
        days = 406 if segment == "discovery" else 431
        result["segments"][segment] = {}
        for role, strategy in STRATEGIES.items():
            filename = f"{strategy}_3x_{date_range}.trades.json"
            path = TRADE_CACHE / filename
            trades = json.loads(path.read_text(encoding="utf-8"))
            result["segments"][segment][role] = analyze(
                trades, days, path.relative_to(ROOT).as_posix()
            )

    candidate_strategy = STRATEGIES["candidate"]
    for segment, date_range in SEGMENTS.items():
        path = TRADE_CACHE / f"{candidate_strategy}_3x_{date_range}.trades.json"
        trades = json.loads(path.read_text(encoding="utf-8"))
        years: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            years[str(trade["entry_date"])[:4]].append(trade)
        result["entry_year_trade_attribution"][segment] = {}
        for year, year_trades in sorted(years.items()):
            year_result = analyze(year_trades, 0, path.relative_to(ROOT).as_posix())
            result["entry_year_trade_attribution"][segment][year] = {
                "trade_count": year_result["trade_count"],
                "distinct_symbols": year_result["distinct_symbols"],
                "total_net_pnl": year_result["total_net_pnl"],
                "top1_net_share": year_result["top1_net_share"],
                "top3_net_share": year_result["top3_net_share"],
                "top5_net_share": year_result["top5_net_share"],
                "classification": "entry-year trade attribution only; not an annual portfolio return",
            }

    payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    OUT.write_bytes(payload)
    OUT.with_name(OUT.name + ".sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {OUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        segment: {
            role: {key: value for key, value in metrics.items() if key not in {"top5_symbols_by_net_pnl", "source_trade_cache"}}
            for role, metrics in roles.items()
        }
        for segment, roles in result["segments"].items()
    }, ensure_ascii=False, indent=2))
    print(f"saved {OUT.relative_to(ROOT).as_posix()} sha256={hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    main()
