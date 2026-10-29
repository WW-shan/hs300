from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
TRADE_CACHE = ROOT / "data/research/r299_cost_audit_cache"
INPUT_MANIFEST = EVIDENCE / "r302_v2_input_manifest.jsonl"
AMENDMENT = EVIDENCE / "r302_v2_analysis_amendment.json"
OUT = EVIDENCE / "r302_segment_concentration_v2.json"
PARENT_RESULT = EVIDENCE / "r299_result.json"

SEGMENTS = {
    "discovery": {"dates": "2023-05-04_2024-12-31", "days": 406},
    "validation": {"dates": "2025-01-02_2026-10-16", "days": 431},
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


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")


def analyze(trades: list[dict], trading_days: int, source: str) -> dict:
    symbols: dict[str, dict] = defaultdict(
        lambda: {"net_pnl": 0.0, "gross_profit": 0.0, "trades": 0, "name": None}
    )
    amounts: list[float] = []
    returns: list[float] = []
    gross_profit = 0.0
    gross_loss = 0.0
    for trade in trades:
        pnl = float(trade.get("pnl_amount") or 0.0)
        row = symbols[str(trade["symbol"])]
        row["net_pnl"] += pnl
        row["gross_profit"] += max(pnl, 0.0)
        row["trades"] += 1
        row["name"] = trade.get("name")
        amounts.append(pnl)
        returns.append(float(trade.get("pnl_pct") or 0.0))
        gross_profit += max(pnl, 0.0)
        gross_loss += max(-pnl, 0.0)

    ranked = sorted(symbols.items(), key=lambda item: (-item[1]["net_pnl"], item[0]))
    positive = [(symbol, row) for symbol, row in ranked if row["net_pnl"] > 0]
    total_net = sum(row["net_pnl"] for row in symbols.values())
    ordered_returns = sorted(returns)
    mid = len(ordered_returns) // 2
    median_return = (
        ordered_returns[mid]
        if len(ordered_returns) % 2
        else (ordered_returns[mid - 1] + ordered_returns[mid]) / 2
    ) if ordered_returns else None

    def net_share(n: int) -> float | None:
        if total_net <= 0:
            return None
        return sum(row["net_pnl"] for _, row in positive[:n]) / total_net

    def gross_share(n: int) -> float | None:
        if gross_profit <= 0:
            return None
        return sum(row["gross_profit"] for _, row in positive[:n]) / gross_profit

    return {
        "trade_count": len(trades),
        "trading_days": trading_days,
        "distinct_symbols": len(symbols),
        "positive_net_symbols": len(positive),
        "negative_net_symbols": sum(row["net_pnl"] < 0 for row in symbols.values()),
        "trade_win_rate": sum(pnl > 0 for pnl in amounts) / len(amounts) if amounts else None,
        "total_net_pnl": round(total_net, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss_abs": round(gross_loss, 2),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "mean_trade_pnl_pct": sum(returns) / len(returns) if returns else None,
        "median_trade_pnl_pct": median_return,
        "top1_positive_symbol_net_share": net_share(1),
        "top3_positive_symbols_net_share": net_share(3),
        "top5_positive_symbols_net_share": net_share(5),
        "top1_net_winner_share_of_gross_profit": gross_share(1),
        "top3_net_winners_share_of_gross_profit": gross_share(3),
        "top5_net_winners_share_of_gross_profit": gross_share(5),
        "top5_symbols_by_net_pnl": [
            {"symbol": symbol, "name": row["name"], "trades": row["trades"], "net_pnl": round(row["net_pnl"], 2)}
            for symbol, row in positive[:5]
        ],
        "source_trade_cache": source,
    }


def main() -> None:
    if OUT.exists() or OUT.with_name(OUT.name + ".sha256").exists():
        raise RuntimeError("R302 v2 result exists; refusing to overwrite")
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    manifest_raw = INPUT_MANIFEST.read_bytes()
    if amendment["input_manifest_sha256"] != hashlib.sha256(manifest_raw).hexdigest():
        raise RuntimeError("R302 v2 manifest hash mismatch")
    if amendment["aggregation_script_sha256"] != sha(Path(__file__)):
        raise RuntimeError("R302 v2 script does not match frozen amendment")

    records = [json.loads(line) for line in manifest_raw.decode("utf-8").splitlines() if line]
    for item in records:
        path = ROOT / item["path"]
        if not path.is_file():
            raise RuntimeError(f"R302 v2 input missing: {item['path']}")
        actual = {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns, "sha256": sha(path)}
        if actual != {key: item[key] for key in actual}:
            raise RuntimeError(f"R302 v2 input changed: {item['path']}")

    result = {
        "round": "R302",
        "revision": "v2_gross_profit_by_winning_symbols_corrected",
        "classification": "post_hoc_descriptive_segment_trade_attribution_not_alpha_confirmation",
        "supersedes": "r302_segment_concentration.json only for gross-profit-by-symbol attribution labels; original net-P&L concentration figures are unchanged",
        "panel_candidate_id": "f438e8b03baa464d9bf7b44d891580c9",
        "panel_candidate_status": "pending",
        "cost_model": {"fees_pct_provenance": 0.0006, "commission_pct_effective": 0.0012, "stamp_tax_pct_sell": 0.001, "slippage_bps_per_side": 15.0},
        "system_backtest_curve_stats": {},
        "segments": {},
        "entry_year_trade_attribution": {},
        "original_gates": {"C2": 0.25, "C3": 0.55, "C4": 0.75},
        "limitations": [
            "This post-hoc diagnostic is not blind, independent validation or proof of alpha.",
            "Trade P&L attribution is not a portfolio return decomposition.",
            "The discovery and validation system runs each start from separate 1,000,000 capital; segment P&Ls are not compounded returns.",
            "Financial and constituent historical PIT limitations remain unresolved.",
        ],
        "input_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "aggregation_script_sha256": sha(Path(__file__)),
    }

    parent = json.loads(PARENT_RESULT.read_text(encoding="utf-8"))
    for segment, spec in SEGMENTS.items():
        result["segments"][segment] = {}
        for role, strategy in STRATEGIES.items():
            filename = f"{strategy}_3x_{spec['dates']}.trades.json"
            path = TRADE_CACHE / filename
            trades = json.loads(path.read_text(encoding="utf-8"))
            result["segments"][segment][role] = analyze(
                trades, spec["days"], path.relative_to(ROOT).as_posix()
            )
            run = parent["execution"]["runs"][f"{role}_3x"]["segments"]
            match = next(row for row in run if row["start"] == spec["dates"].split("_")[0])
            result["system_backtest_curve_stats"].setdefault(segment, {})[role] = {
                "start": match["start"], "end": match["end"],
                "total_return": match["curve_stats"]["total_return"],
                "annualized_return": match["curve_stats"]["annual"],
                "sharpe": match["curve_stats"]["sharpe"],
                "max_drawdown": match["curve_stats"]["mdd"],
                "trade_count": match["result_stats"]["n_trades"],
                "final_equity": match["result_stats"]["final_equity"],
            }

        candidate_path = TRADE_CACHE / f"{STRATEGIES['candidate']}_3x_{spec['dates']}.trades.json"
        trades = json.loads(candidate_path.read_text(encoding="utf-8"))
        by_year: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            by_year[str(trade["entry_date"])[:4]].append(trade)
        result["entry_year_trade_attribution"][segment] = {}
        for year, rows in sorted(by_year.items()):
            metric = analyze(rows, 0, candidate_path.relative_to(ROOT).as_posix())
            result["entry_year_trade_attribution"][segment][year] = {
                key: metric[key] for key in (
                    "trade_count", "distinct_symbols", "total_net_pnl",
                    "top1_positive_symbol_net_share", "top3_positive_symbols_net_share",
                    "top5_positive_symbols_net_share",
                )
            } | {"classification": "entry-year trade attribution only; not an annual portfolio return"}

    raw = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_once(OUT, raw)
    print(json.dumps({
        segment: {
            role: {key: value for key, value in metrics.items() if key not in {"top5_symbols_by_net_pnl", "source_trade_cache"}}
            for role, metrics in roles.items()
        }
        for segment, roles in result["segments"].items()
    }, ensure_ascii=False, indent=2))
    print(f"saved {OUT.relative_to(ROOT).as_posix()} sha256={hashlib.sha256(raw).hexdigest()}")


if __name__ == "__main__":
    main()
