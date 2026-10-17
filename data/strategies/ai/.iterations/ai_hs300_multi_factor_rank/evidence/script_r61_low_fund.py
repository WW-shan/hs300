# 输入说明: R61 — 低基金持仓 20% long-only 事后假设。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r61_low_fund.py
# 结果 JSON: 同目录 r61_low_fund.json。
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r54 = _load("r54", "script_r54_long_only.py")
r60 = _load("r60", "script_r60_fund_hold.py")
r57 = _load("r57", "script_r57_composites.py")
PHASES = r54.r40.r33.phase_dates(r54.r40.PHASE_TARGETS)
PHASES = [p.strftime("%Y-%m") for p in PHASES]
FACTOR = "fund_holding_ratio"


def _selector(d: pl.DataFrame) -> dict[str, float]:
    valid = d.filter(pl.col(FACTOR).is_not_null() & pl.col("fwd").is_not_null())
    if valid.height < 60:
        return {}
    k = max(1, int(valid.height * 0.20))
    picked = valid.sort([FACTOR, "symbol"]).head(k)["symbol"].to_list()
    return {s: 1.0 / len(picked) for s in picked}


def _baseline(d: pl.DataFrame) -> dict[str, float]:
    if d.height < 60:
        return {}
    return {s: 1.0 / d.height for s in d["symbol"].to_list()}


def main() -> int:
    _panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    reb = r60._attach(reb, r60._quarter_data())
    base, _ = r54._series(reb, _baseline)
    series, hold = r54._series(reb, _selector)
    base_m, base_roll = r54._phase(base, PHASES)
    metrics, rolling = r54._phase(series, PHASES)
    excess = r54._excess(series, base)
    hs = r57._hold_stats(hold)
    coverage = r60._coverage(reb, FACTOR, 80)
    d1 = bool(hs["months"] >= 48 and hs["avg"] is not None and 30 <= hs["avg"] <= 80 and coverage["mean"] >= 0.70)
    g1 = bool(metrics["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10)
    g2 = bool(metrics["annual_mean"] >= base_m["annual_mean"])
    g3 = bool(metrics["mdd_mean"] >= base_m["mdd_mean"] - 0.01)
    g4 = bool(metrics["last_year_min"] >= base_m["last_year_min"] and rolling["positive_rate"] >= base_roll["positive_rate"])
    g5 = bool(sum(1 for a, b in zip([p["sharpe"] for p in metrics["phases"]], [p["sharpe"] for p in base_m["phases"]], strict=False) if a is not None and b is not None and a > b) >= 5)
    g6 = bool(excess["discovery"]["mean"] is not None and excess["discovery"]["mean"] > 0 and excess["validation"]["mean"] is not None and excess["validation"]["mean"] > 0 and excess["validation"]["t"] is not None and excess["validation"]["t"] > 1.5)
    gates = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    gates["all_pass"] = all(gates.values())
    result = {
        "post_hoc": True,
        "factor": FACTOR,
        "holdings": hs,
        "coverage": coverage,
        "base": {"metrics": base_m, "rolling": base_roll},
        "candidate": {"metrics": metrics, "rolling": rolling, "excess": excess},
        "gates": gates,
        "passed": ["LOWFUND"] if gates["all_pass"] else [],
        "recommendation": "R62 portfolio/risk integration" if gates["all_pass"] else "negative knowledge; fund holding dimension closed",
        "caveats": ["高基金持仓负 IC 的反向假设", "季度末 +45 天可得", "当前流通股本用于历史比率", "历史名单为月度快照"],
    }
    path = EV / "r61_low_fund.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("base", base_m["sharpe_mean"], "lowfund", metrics["sharpe_mean"], "hold", hs, "excess", excess, "gates", gates, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
