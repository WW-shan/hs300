from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r350_sleeve_ensemble_preflight.json"
R344_CACHE = DATA / "research/r344_cache"
R346_CACHE = DATA / "research/r346_cache"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
SOURCES = {
    "A10": (R344_CACHE, A_STRATEGY, 10),
    "B10": (R344_CACHE, B_STRATEGY, 10),
    "A20": (R346_CACHE, A_STRATEGY, 20),
    "B20": (R346_CACHE, B_STRATEGY, 20),
    "C": (R344_CACHE, C_STRATEGY, 50),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trade_path(key: str, cost: int) -> Path:
    cache, strategy_id, positions = SOURCES[key]
    return cache / f"{strategy_id}_{START}_{cost}x_p{positions}.trades.json"


def parse_day(value) -> date | None:
    if value is None or str(value) in {"None", "null"}:
        return None
    return date.fromisoformat(str(value)[:10])


def active_symbols(trades: list[dict], day: date) -> set[str]:
    out = set()
    for trade in trades:
        entry = parse_day(trade["entry_date"])
        exit_ = parse_day(trade.get("exit_date")) or END
        if entry <= day <= exit_ and trade.get("symbol"):
            out.add(str(trade["symbol"]))
    return out


def overlap_summary(left: list[dict], right: list[dict]) -> dict:
    days = sorted({parse_day(trade["entry_date"]) for trade in left + right})
    values = []
    for day in days:
        a = active_symbols(left, day)
        b = active_symbols(right, day)
        union = a | b
        if union:
            values.append(len(a & b) / len(union))
    return {
        "average_jaccard": sum(values) / len(values) if values else None,
        "days": len(values),
        "left_symbols": len({trade["symbol"] for trade in left}),
        "right_symbols": len({trade["symbol"] for trade in right}),
        "union_symbols": len({trade["symbol"] for trade in left + right}),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R350 preflight exists; refusing overwrite")
    required: dict[str, Path] = {}
    for key, (cache, strategy_id, positions) in SOURCES.items():
        for factor in (2, 3, 4, 5):
            required[f"{key}_{factor}x"] = cache / f"{strategy_id}_{START}_{factor}x_p{positions}.parquet"
        for phase in PHASES:
            required[f"{key}_{phase}_2x"] = cache / f"{strategy_id}_{phase}_2x_p{positions}.parquet"
        if key != "C":
            for factor in (2, 5):
                required[f"{key}_{factor}x_trades"] = trade_path(key, factor)
    missing = [str(path.relative_to(ROOT)) for path in required.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"R350 cache files missing: {missing[:5]}")
    before = {key: sha256(path) for key, path in required.items()}
    trades = {key: json.loads(trade_path(key, 2).read_text(encoding="utf-8")) for key in ("A10", "B10", "A20", "B20")}
    a_overlap = overlap_summary(trades["A10"], trades["A20"])
    b_overlap = overlap_summary(trades["B10"], trades["B20"])
    after = {key: sha256(path) for key, path in required.items()}
    checks = {
        "D1_required_cache_complete": not missing,
        "D2_a_pair_breadth_expands": a_overlap["union_symbols"] > max(a_overlap["left_symbols"], a_overlap["right_symbols"]),
        "D3_b_pair_breadth_expands": b_overlap["union_symbols"] > max(b_overlap["left_symbols"], b_overlap["right_symbols"]),
        "D4_a_overlap_below_060": a_overlap["average_jaccard"] is not None and a_overlap["average_jaccard"] < 0.60,
        "D5_b_overlap_below_060": b_overlap["average_jaccard"] is not None and b_overlap["average_jaccard"] < 0.60,
        "D6_inputs_byte_stable": before == after,
    }
    payload = {
        "round": "R350",
        "status": "coverage_and_holdings_preflight_no_returns",
        "rule": {
            "A": "50/50 monthly-rebalanced ensemble of frozen A Top10 and A Top20 curves",
            "B": "50/50 monthly-rebalanced ensemble of frozen B Top10 and B Top20 curves",
            "C": "frozen C Top50 curve unchanged",
            "defensive_bucket": "frozen R344 gold trend proxy and one-third defensive bucket unchanged",
            "no_weight_scan": True,
        },
        "window": [str(START), str(END)],
        "cache_manifest": before,
        "holdings_overlap": {"A_top10_vs_top20": a_overlap, "B_top10_vs_top20": b_overlap},
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": {"before": before, "after": after, "stable": before == after},
        "no_return_or_backtest_evaluated": True,
        "next": "If all checks pass, freeze R350 ensemble construction, ensemble cost and portfolio gates before any candidate return calculation.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "status": payload["status"],
        "holdings_overlap": payload["holdings_overlap"],
        "checks": checks,
        "all_checks": payload["all_checks"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
