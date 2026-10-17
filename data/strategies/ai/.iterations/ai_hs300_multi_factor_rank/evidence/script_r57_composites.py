# 输入说明: R57 — 历史 HS300 固定跨维度组合。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r57_composites.py
# 结果 JSON: 同目录 r57_composites.json。
from __future__ import annotations

import importlib.util
import json
import statistics as st
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
r50 = _load("r50", "script_r50_margin.py")
r53 = _load("r53", "script_r53_price_volume_zoo.py")
r54 = _load("r54", "script_r54_long_only.py")
r40 = r50.r40
COST = 0.0028
TOP_FRACTION = 0.20
MIN_SCORE_STOCKS = 60
MIN_HOLDINGS = 30
MAX_HOLDINGS = 80
PHASES = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]

# (factor, direction) pairs; high means higher is better.
CANDIDATES = {
    "VQ": [("pb_latest", "low"), ("roe_latest", "high"), ("ocf_ni", "high")],
    "QS": [("roe_latest", "high"), ("gross_margin_latest", "high"), ("debt_ratio_latest", "low"), ("accrual", "low")],
    "GQ": [("revenue_yoy_latest", "high"), ("net_income_yoy_latest", "high"), ("roe_latest", "high"), ("debt_ratio_latest", "low")],
    "LOWRISK": [("annual_vol_20d", "low"), ("downside_vol_20d", "low"), ("max_ret_20d", "low"), ("skew_60d", "low")],
    "MS": [("margin_chg_1m", "low"), ("skew_60d", "low")],
    "TV": [("momentum_20d", "high"), ("pb_latest", "low"), ("roe_latest", "high")],
}


def _score(reb: pl.DataFrame, components: list[tuple[str, str]]) -> pl.DataFrame:
    cols = []
    for factor, direction in components:
        r = pl.col(factor).rank().over("date") / pl.len().over("date")
        cols.append((1.0 - r if direction == "low" else r).alias(f"_{factor}_pct"))
    out = reb.select(["symbol", "date", *[factor for factor, _ in components]]).with_columns(cols)
    pcols = [f"_{factor}_pct" for factor, _ in components]
    return out.with_columns([
        pl.mean_horizontal(*[pl.col(c) for c in pcols], ignore_nulls=True).alias("score"),
        pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int64) for c in pcols]).alias("score_count"),
    ]).with_columns(
        pl.when(pl.col("score_count") >= 2).then(pl.col("score")).otherwise(None).alias("score")
    ).select(["symbol", "date", "score", "score_count"])


def _top_selector(score: pl.DataFrame):
    def selector(d: pl.DataFrame) -> dict[str, float]:
        valid = d.select(["symbol", "date", "fwd"]).join(score, on=["symbol", "date"], how="left").filter(
            pl.col("score").is_not_null() & pl.col("fwd").is_not_null()
        )
        if valid.height < MIN_SCORE_STOCKS:
            return {}
        k = max(1, int(valid.height * TOP_FRACTION))
        picked = valid.sort(["score", "symbol"], descending=[True, False]).head(k)["symbol"].to_list()
        return {s: 1.0 / len(picked) for s in picked}
    return selector


def _baseline_selector(d: pl.DataFrame) -> dict[str, float]:
    if d.height < MIN_SCORE_STOCKS:
        return {}
    return {s: 1.0 / d.height for s in d["symbol"].to_list()}


def _hold_stats(hold: list[dict]) -> dict:
    if not hold:
        return {"months": 0, "avg": None, "min": None, "max": None}
    vals = [row["holdings"] for row in hold]
    return {"months": len(vals), "avg": round(st.mean(vals), 2), "min": min(vals), "max": max(vals)}


def _excess_t(series: pl.DataFrame, base: pl.DataFrame) -> dict:
    return r54._excess(series, base)["validation"]


def main() -> int:
    panel, reb, _ = r42.build_panel()
    reb = r42._scores(reb)
    reb = reb.join(r53._factors(panel), on=["symbol", "date"], how="left")
    reb = r50._attach(reb, r50._snapshots(panel))
    base_series, _base_hold = r54._series(reb, _baseline_selector)
    base_metrics, base_roll = r54._phase(base_series, PHASES)
    records = {}
    for name, components in CANDIDATES.items():
        score = _score(reb, components)
        series, hold = r54._series(reb, _top_selector(score))
        metrics, rolling = r54._phase(series, PHASES)
        excess = r54._excess(series, base_series)
        valid_months = score.filter(pl.col("score").is_not_null()).group_by("date").len().height
        hs = _hold_stats(hold)
        d1 = bool(valid_months >= 48 and hs["avg"] is not None and MIN_HOLDINGS <= hs["avg"] <= MAX_HOLDINGS)
        g1 = bool(metrics["sharpe_mean"] >= base_metrics["sharpe_mean"] + 0.10)
        g2 = bool(metrics["annual_mean"] >= base_metrics["annual_mean"])
        g3 = bool(metrics["mdd_mean"] >= base_metrics["mdd_mean"] - 0.01)
        g4 = bool(metrics["last_year_min"] >= base_metrics["last_year_min"] and rolling["positive_rate"] >= base_roll["positive_rate"])
        g5 = bool(sum(1 for a, b in zip([p["sharpe"] for p in metrics["phases"]], [p["sharpe"] for p in base_metrics["phases"]], strict=False) if a is not None and b is not None and a > b) >= 5)
        g6 = bool(
            excess["discovery"]["mean"] is not None and excess["discovery"]["mean"] > 0
            and excess["validation"]["mean"] is not None and excess["validation"]["mean"] > 0
            and excess["validation"]["t"] is not None and excess["validation"]["t"] > 1.5
        )
        # G7: full composite must beat every single-component and leave-one-out variant
        # on validation excess t by at least 0.20. For 2-component composites, use singles.
        variants = []
        if len(components) > 2:
            for i in range(len(components)):
                subset = components[:i] + components[i + 1:]
                if len(subset) >= 2:
                    s = _score(reb, subset)
                    ss, _ = r54._series(reb, _top_selector(s))
                    variants.append(_excess_t(ss, base_series))
        for component in components:
            s = _score(reb, [component])
            ss, _ = r54._series(reb, _top_selector(s))
            variants.append(_excess_t(ss, base_series))
        variant_t = max((v["t"] for v in variants if v.get("t") is not None), default=None)
        full_t = excess["validation"]["t"]
        g7 = bool(full_t is not None and variant_t is not None and full_t > variant_t + 0.20)
        records[name] = {
            "components": [f"{factor}:{direction}" for factor, direction in components],
            "score_valid_months": valid_months,
            "holdings": hs,
            "metrics": metrics, "rolling": rolling, "excess": excess,
            "variant_validation_t": variant_t,
            "gates": {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7},
        }
        records[name]["gates"]["all_pass"] = all(records[name]["gates"].values())
        print(name, "hold", hs, "sharpe", metrics["sharpe_mean"], "excess", excess, "gates", records[name]["gates"], flush=True)
    passed = [name for name, rec in records.items() if rec["gates"]["all_pass"]]
    result = {
        "post_hoc": True,
        "rule": {"top_fraction": TOP_FRACTION, "cost_2x": COST, "candidates": list(CANDIDATES)},
        "base": {"metrics": base_metrics, "rolling": base_roll},
        "candidates": records,
        "passed": passed,
        "recommendation": "R58 robustness/cost/forward validation" if passed else "negative knowledge; fixed-composite line closed",
        "caveats": ["所有成分均来自同一历史样本, 组合是事后构造", "两融覆盖只含可融资融券标的", "未做随机组合 placebo", "历史名单为月度快照"],
    }
    path = EV / "r57_composites.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
