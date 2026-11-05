from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
V1 = EVIDENCE / "r350_sleeve_ensemble_preflight.json"
OUTPUT = EVIDENCE / "r350_sleeve_ensemble_preflight_v2.json"
DATA = ROOT / "data"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
TRADES = {
    "A10": DATA / "research/r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.trades.json",
    "A20": DATA / "research/r346_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p20.trades.json",
    "B10": DATA / "research/r344_cache/ai_hs300_industry_relative_strength_quality_h20_sl20_2022-05-05_2x_p10.trades.json",
    "B20": DATA / "research/r346_cache/ai_hs300_industry_relative_strength_quality_h20_sl20_2022-05-05_2x_p20.trades.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R350 v2 preflight exists; refusing overwrite")
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    v1_expected = V1.with_name(V1.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha256(V1) != v1_expected:
        raise RuntimeError("R350 v1 preflight checksum mismatch")
    if not v1["no_return_or_backtest_evaluated"] or not v1["input_isolation"]["stable"]:
        raise RuntimeError("R350 v1 preflight input/isolation contract changed")
    symbols = {key: {str(trade["symbol"]) for trade in json.loads(path.read_text(encoding="utf-8"))} for key, path in TRADES.items()}
    additions = {
        "A_top20_only_symbols": sorted(symbols["A20"] - symbols["A10"]),
        "B_top20_only_symbols": sorted(symbols["B20"] - symbols["B10"]),
    }
    checks = {
        "D1_required_cache_complete": v1["checks"]["D1_required_cache_complete"],
        "D2_a_top20_adds_symbols": len(additions["A_top20_only_symbols"]) > 0,
        "D3_b_top20_adds_symbols": len(additions["B_top20_only_symbols"]) > 0,
        "D4_a_overlap_below_060": v1["checks"]["D4_a_overlap_below_060"],
        "D5_b_overlap_below_060": v1["checks"]["D5_b_overlap_below_060"],
        "D6_inputs_byte_stable": v1["checks"]["D6_inputs_byte_stable"],
    }
    payload = {
        "round": "R350",
        "status": "coverage_and_holdings_preflight_v2_no_returns",
        "supersedes": {
            "file": V1.name,
            "sha256": sha256(V1),
            "reason": "D2/D3 were mathematically misspecified as union_symbols > max(pair), although Top10 holdings are usually a subset of Top20; corrected to count Top20-only symbols.",
            "original_checks": v1["checks"],
        },
        "rule": v1["rule"],
        "window": v1["window"],
        "cache_manifest": v1["cache_manifest"],
        "holdings_overlap": v1["holdings_overlap"],
        "top20_only_symbols": additions,
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": v1["input_isolation"],
        "no_return_or_backtest_evaluated": True,
        "next": "If all checks pass, freeze R350 ensemble construction, ensemble cost and portfolio gates before any candidate return calculation.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"checks": checks, "all_checks": payload["all_checks"], "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
