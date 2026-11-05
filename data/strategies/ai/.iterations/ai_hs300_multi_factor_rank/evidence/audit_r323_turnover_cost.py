from __future__ import annotations
import hashlib, json, math, statistics as st
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EV = Path(__file__).resolve().parent
OUT = EV / "r323_turnover_cost_audit.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(returns: list[float]) -> dict:
    eq = [1.0]
    for r in returns:
        eq.append(eq[-1] * (1 + r))
    total = eq[-1] - 1
    annual = (1 + total) ** (12 / len(returns)) - 1
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(12) if sd else 0.0
    peak = 1.0
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {"total_return": round(total, 6), "annual": round(annual, 6), "sharpe": round(sharpe, 4), "mdd": round(mdd, 6)}


def rolling_positive(returns: list[float], window: int = 12) -> float:
    out = []
    for i in range(len(returns) - window + 1):
        x = 1.0
        for r in returns[i:i + window]:
            x *= 1 + r
        out.append(x - 1)
    return round(sum(x > 0 for x in out) / len(out), 6) if out else 0.0


def audit(path: Path, label: str) -> dict:
    data = json.loads(path.read_text())
    rows = data["prediction_rows"]
    turns = []
    prev = None
    for row in rows:
        current = set(row["selected_symbols"])
        turn = 1.0 if prev is None else 1 - len(current & prev) / max(len(current), 1)
        turns.append(turn)
        prev = current
    gross = [row["model_return"] + 0.0052 for row in rows]
    net_52 = [g - 0.0052 * t for g, t in zip(gross, turns)]
    net_19 = [g - 0.0019 * t for g, t in zip(gross, turns)]
    return {
        "label": label,
        "source": path.name,
        "source_sha256": sha(path),
        "months": len(rows),
        "average_turnover": round(st.mean(turns), 6),
        "median_turnover": round(st.median(turns), 6),
        "gross_metrics": metrics(gross),
        "turnover_adjusted_52bp": metrics(net_52),
        "turnover_adjusted_19bp": metrics(net_19),
        "rolling_12m_positive_52bp": rolling_positive(net_52),
        "rolling_12m_positive_19bp": rolling_positive(net_19),
        "reported_full_turnover_net": data["model_metrics"],
        "yearly_reported": data["yearly"],
    }


def main() -> int:
    if OUT.exists() or OUT.with_name(OUT.name + ".sha256").exists():
        raise RuntimeError("R323 result exists; refusing overwrite")
    top30 = audit(EV / "r321_ridge_meta_factor.json", "R321_top30")
    top10 = audit(EV / "r322_top10_ridge.json", "R322_top10")
    payload = {
        "round": "R323",
        "classification": "post-result turnover-aware cost audit; no new alpha or parameter search",
        "method": {
            "turnover": "1 - |selected_t ∩ selected_(t-1)| / |selected_t|; first month charged 1.0",
            "gross_return": "R321/R322 reported model_return + the original 0.0052 full-month cost",
            "cost_cases": ["0.52% round-trip times actual turnover", "0.19% round-trip times actual turnover"],
            "benchmark": "R321/R322 reported benchmark net returns (unchanged)"
        },
        "r321_top30": top30,
        "r322_top10": top10,
        "decision": {
            "status": "both_models_research_only",
            "reason": "Even the 19bp turnover-aware case remains below the 8% annual and annual-stability gates; 2022/2023 are negative.",
            "production_write": False,
            "provider_call": False
        },
        "limits": [
            "This is an arithmetic cost audit, not a new backtest.",
            "It ignores intra-month turnover and assumes proportional name overlap equals capital turnover.",
            "R40 snapshot and current factor versions remain."
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    OUT.write_bytes(raw)
    OUT.with_name(OUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUT.name}\n", encoding="ascii")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
