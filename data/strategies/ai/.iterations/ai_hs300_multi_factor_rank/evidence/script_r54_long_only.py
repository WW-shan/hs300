# 输入说明: R54 — 低偏度 / 低融资增长 long-only 20% 组合检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r54_long_only.py
# 结果 JSON: 同目录 r54_long_only.json。
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
r49 = _load("r49", "script_r49_index_overlay.py")
r40 = r50.r40
r30 = r49.r30
COST_2X = 0.0028
FRACTION = 0.20
MIN_VALID = 60


def _select(d: pl.DataFrame, col: str) -> dict[str, float]:
    valid = d.filter(pl.col(col).is_not_null())
    if valid.height < MIN_VALID:
        return {}
    k = max(1, int(valid.height * FRACTION))
    picked = valid.sort([col, "symbol"]).head(k)["symbol"].to_list()
    return {s: 1.0 / len(picked) for s in picked}


def _combine(s: dict[str, float], m: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for weights in (s, m):
        for symbol, weight in weights.items():
            out[symbol] = out.get(symbol, 0.0) + 0.5 * weight
    out["__cash__"] = max(0.0, 1.0 - sum(out.values()))
    return out


def _series(reb: pl.DataFrame, selector) -> tuple[pl.DataFrame, list[dict]]:
    prev = {}
    rows, holdings = [], []
    for day in sorted(reb["date"].unique().to_list()):
        d = reb.filter((pl.col("date") == day) & pl.col("fwd").is_not_null())
        weights = selector(d)
        if not weights:
            weights = {"__cash__": 1.0}
        gross = sum(weight * float(d.filter(pl.col("symbol") == symbol)["fwd"][0]) for symbol, weight in weights.items() if symbol != "__cash__")
        if not prev:
            turn = 1.0
        else:
            keys = set(weights) | set(prev)
            turn = 0.5 * sum(abs(weights.get(k, 0.0) - prev.get(k, 0.0)) for k in keys)
        rows.append({"date": day, "ym": day.strftime("%Y-%m"), "gross": gross, "turnover": turn, "r": gross - COST_2X * turn})
        holdings.append({"date": day, "holdings": sum(1 for k in weights if k != "__cash__"), "weight": sum(v for k, v in weights.items() if k != "__cash__")})
        prev = weights
    return pl.DataFrame(rows).sort("date"), holdings


def _phase(frame: pl.DataFrame, phases: list[str]) -> tuple[dict, dict]:
    frames = [frame.filter(pl.col("ym") >= p) for p in phases]
    return r30.phase_metrics(frames), r30.rolling_windows(frames)


def _excess(candidate: pl.DataFrame, base: pl.DataFrame) -> dict:
    j = candidate.select(["ym", "r"]).join(base.select(["ym", "r"]), on="ym", how="inner", suffix="_base").with_columns(
        (pl.col("r") - pl.col("r_base")).alias("excess")
    )
    disc = j.filter(pl.col("ym") <= "2023-12")["excess"].to_list()
    val = j.filter(pl.col("ym") >= "2024-01")["excess"].to_list()
    def stats(vals):
        if not vals:
            return {"n": 0, "mean": None, "t": None}
        mean = st.mean(vals)
        sd = st.pstdev(vals)
        return {"n": len(vals), "mean": round(mean, 5), "t": round(mean / (sd / len(vals) ** 0.5), 2) if sd > 0 else None}
    return {"discovery": stats(disc), "validation": stats(val)}


def _corr(a: pl.DataFrame, b: pl.DataFrame) -> float | None:
    j = a.select(["ym", "r"]).join(b.select(["ym", "r"]), on="ym", how="inner", suffix="_b")
    if j.height < 6:
        return None
    x, y = j["r"].to_list(), j["r_b"].to_list()
    if st.pstdev(x) == 0 or st.pstdev(y) == 0:
        return None
    return round(st.correlation(x, y), 3)


def main() -> int:
    panel, reb, _ = r42.build_panel()
    reb = reb.join(r53._factors(panel), on=["symbol", "date"], how="left")
    reb = r50._attach(reb, r50._snapshots(panel))
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]

    def select_s(d):
        return _select(d, "skew_60d")

    def select_m(d):
        return _select(d, "margin_chg_1m")

    def select_sm(d):
        return _combine(select_s(d), select_m(d))

    # 基准为全部 eligible 等权。
    def select_base(d):
        if d.height < MIN_VALID:
            return {}
        return {s: 1.0 / d.height for s in d["symbol"].to_list()}

    base, _base_h = _series(reb, select_base)
    s_series, s_h = _series(reb, select_s)
    m_series, m_h = _series(reb, select_m)
    sm_series, sm_h = _series(reb, select_sm)
    base_m, base_roll = _phase(base, phases)
    candidates = {"S": (s_series, s_h), "M": (m_series, m_h), "SM": (sm_series, sm_h)}
    reports = {}
    for name, (series, hold) in candidates.items():
        metrics, rolling = _phase(series, phases)
        excess = _excess(series, base)
        d1 = bool(
            len(hold) >= 48
            and 30 <= st.mean(row["holdings"] for row in hold) <= 80
            and all(row["holdings"] > 0 for row in hold[-48:])
        ) if hold else False
        g1 = bool(metrics["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10)
        g2 = bool(metrics["annual_mean"] >= base_m["annual_mean"])
        g3 = bool(metrics["mdd_mean"] >= base_m["mdd_mean"] - 0.01)
        g4 = bool(metrics["last_year_min"] >= base_m["last_year_min"] and rolling["positive_rate"] >= base_roll["positive_rate"])
        g5 = bool(sum(1 for x, y in zip([p["sharpe"] for p in metrics["phases"]], [p["sharpe"] for p in base_m["phases"]], strict=False) if x is not None and y is not None and x > y) >= 5)
        g6 = bool(excess["discovery"]["mean"] is not None and excess["discovery"]["mean"] > 0 and excess["validation"]["mean"] is not None and excess["validation"]["mean"] > 0 and excess["validation"]["t"] is not None and excess["validation"]["t"] > 1.5)
        reports[name] = {
            "metrics": metrics, "rolling": rolling, "excess": excess,
            "holdings": {"months": len(hold), "avg": round(st.mean(row["holdings"] for row in hold), 2) if hold else None, "min": min((row["holdings"] for row in hold), default=None), "max": max((row["holdings"] for row in hold), default=None)},
            "gates": {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6},
        }
        reports[name]["gates"]["all_pass"] = all(reports[name]["gates"].values())
    sm_extra = bool(
        reports["SM"]["metrics"]["sharpe_mean"] >= max(reports["S"]["metrics"]["sharpe_mean"], reports["M"]["metrics"]["sharpe_mean"]) + 0.02
        and reports["SM"]["metrics"]["annual_mean"] >= max(reports["S"]["metrics"]["annual_mean"], reports["M"]["metrics"]["annual_mean"])
    )
    reports["SM"]["gates"]["G7"] = sm_extra
    reports["SM"]["gates"]["all_pass"] = bool(reports["SM"]["gates"]["all_pass"] and sm_extra)
    passed = [name for name in ("S", "M", "SM") if reports[name]["gates"]["all_pass"]]
    result = {
        "post_hoc": True,
        "rule": {"fraction": FRACTION, "min_valid": MIN_VALID, "cost_2x": COST_2X, "candidates": ["S", "M", "SM"]},
        "base": {"metrics": base_m, "rolling": base_roll},
        "candidates": reports,
        "correlations": {"S_M": _corr(s_series, m_series), "S_SM": _corr(s_series, sm_series), "M_SM": _corr(m_series, sm_series)},
        "passed": passed,
        "decision": {name: reports[name]["gates"] for name in ("S", "M", "SM")},
        "recommendation": "R55 portfolio integration" if passed else "negative knowledge; do not deploy",
        "caveats": ["低偏度和低融资增长均由同一历史样本筛选, 这是事后策略检验", "两融覆盖只含可融资融券标的", "未做随机组合 placebo", "历史名单为月度快照"],
    }
    path = EV / "r54_long_only.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("base", base_m["sharpe_mean"], {name: reports[name]["metrics"]["sharpe_mean"] for name in candidates}, flush=True)
    print("passed", passed, "decision", result["decision"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
