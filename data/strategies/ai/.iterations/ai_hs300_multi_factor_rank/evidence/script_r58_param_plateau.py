# 输入说明: R58 — VQ/TV/MS 的 top10/20/30% 参数平台检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r58_param_plateau.py
# 结果 JSON: 同目录 r58_param_plateau.json。
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


r57 = _load("r57", "script_r57_composites.py")
r50 = _load("r50", "script_r50_margin.py")
r53 = _load("r53", "script_r53_price_volume_zoo.py")
r54 = _load("r54", "script_r54_long_only.py")
r42 = r57.r42
PHASES = r57.PHASES
FRACTIONS = (0.10, 0.20, 0.30)
CANDIDATES = ("VQ", "TV", "MS")


def _selector(score: pl.DataFrame, fraction: float):
    def select(d: pl.DataFrame) -> dict[str, float]:
        valid = d.select(["symbol", "date", "fwd"]).join(score, on=["symbol", "date"], how="left").filter(
            pl.col("score").is_not_null() & pl.col("fwd").is_not_null()
        )
        if valid.height < 60:
            return {}
        k = max(1, int(valid.height * fraction))
        picked = valid.sort(["score", "symbol"], descending=[True, False]).head(k)["symbol"].to_list()
        return {s: 1.0 / len(picked) for s in picked}
    return select


def _base_selector(d: pl.DataFrame) -> dict[str, float]:
    if d.height < 60:
        return {}
    return {s: 1.0 / d.height for s in d["symbol"].to_list()}


def main() -> int:
    panel, reb, _ = r42.build_panel()
    reb = r42._scores(reb)
    reb = reb.join(r53._factors(panel), on=["symbol", "date"], how="left")
    reb = r50._attach(reb, r50._snapshots(panel))
    base_series, _ = r54._series(reb, _base_selector)
    base_metrics, base_roll = r54._phase(base_series, PHASES)
    records = {}
    for name in CANDIDATES:
        components = r57.CANDIDATES[name]
        points = {}
        for fraction in FRACTIONS:
            score = r57._score(reb, components)
            series, hold = r54._series(reb, _selector(score, fraction))
            metrics, rolling = r54._phase(series, PHASES)
            excess = r54._excess(series, base_series)
            hs = r57._hold_stats(hold)
            points[f"{fraction:.2f}"] = {
                "fraction": fraction,
                "holdings": hs,
                "metrics": metrics,
                "rolling": rolling,
                "excess": excess,
            }
        vals = list(points.values())
        d1 = all(
            p["holdings"]["months"] >= 48 and p["holdings"]["avg"] is not None and 20 <= p["holdings"]["avg"] <= 100
            for p in vals
        )
        g1 = all(p["metrics"]["sharpe_mean"] >= base_metrics["sharpe_mean"] + 0.10 for p in vals)
        g2 = all(p["metrics"]["annual_mean"] >= base_metrics["annual_mean"] for p in vals)
        g3 = all(p["metrics"]["mdd_mean"] >= base_metrics["mdd_mean"] - 0.01 for p in vals)
        g4 = all(
            sum(1 for a, b in zip([x["sharpe"] for x in p["metrics"]["phases"]], [x["sharpe"] for x in base_metrics["phases"]], strict=False) if a is not None and b is not None and a > b) >= 5
            for p in vals
        )
        g5 = all(
            p["excess"]["discovery"]["mean"] is not None and p["excess"]["discovery"]["mean"] > 0
            and p["excess"]["validation"]["t"] is not None and p["excess"]["validation"]["t"] > 1.5
            for p in vals
        )
        sharpes = [p["metrics"]["sharpe_mean"] for p in vals]
        g6 = bool(max(sharpes) - min(sharpes) <= 0.15)
        gates = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
        gates["all_pass"] = all(gates.values())
        records[name] = {"components": [f"{f}:{d}" for f, d in components], "points": points, "gates": gates}
        print(name, "sharpes", sharpes, "gates", gates, flush=True)
    passed = [n for n in CANDIDATES if records[n]["gates"]["all_pass"]]
    result = {
        "post_hoc": True,
        "rule": {"fractions": list(FRACTIONS), "candidates": list(CANDIDATES), "cost_2x": 0.0028},
        "base": {"metrics": base_metrics, "rolling": base_roll},
        "candidates": records,
        "passed": passed,
        "recommendation": "R59 walk-forward/external validation" if passed else "negative knowledge; fixed-composite line closed",
        "caveats": ["参数平台来自同一历史样本", "未做独立样本外验证", "两融覆盖只含可融资融券标的", "历史名单为月度快照"],
    }
    path = EV / "r58_param_plateau.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
