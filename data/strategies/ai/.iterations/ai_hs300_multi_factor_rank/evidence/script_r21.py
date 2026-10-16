# 输入说明: 需本地后端 (127.0.0.1:3018) 与 API Token: 运行前 `export TF_TOKEN=<token>`。
# 结果 JSON: 与本脚本同目录的 r21_robustness_b75_b80.json。
# 本轮只做 R20 已选权重的滚动/分段确认, 不新增权重。
import importlib.util
import json
import os
import statistics as st
import time

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
spec = importlib.util.spec_from_file_location("r19", EV + "script_r19.py")
r19 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r19)

WEIGHTS = {"B75": 0.75, "B80": 0.80}
SPLIT = "2022-06-30"


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k != "curve"}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def blend_curves(phase_runs, weight):
    return [
        r19.blend_curve(x["curve"], y["curve"], weight)
        for x, y in zip(phase_runs["P2"], phase_runs["V20"], strict=True)
    ]


def rolling_one_year(curves):
    vals = []
    for curve in curves:
        values = [x["value"] for x in curve]
        for i in range(252, len(values)):
            vals.append(values[i] / values[i - 252] - 1)
    vals.sort()
    n = len(vals)
    return {
        "n": n,
        "positive_rate": sum(v > 0 for v in vals) / n if n else None,
        "median": st.median(vals) if n else None,
        "p05": vals[int(n * 0.05)] if n else None,
        "p10": vals[int(n * 0.10)] if n else None,
        "worst": vals[0] if n else None,
        "best": vals[-1] if n else None,
    }


def split_metrics(curves):
    first, second = [], []
    for curve in curves:
        a = [x for x in curve if x["date"] <= SPLIT]
        b = [x for x in curve if x["date"] > SPLIT]
        if len(a) > 2:
            first.append(r19.curve_metrics(a))
        if len(b) > 2:
            second.append(r19.curve_metrics(b))
    def agg(recs):
        return {
            "sharpe_mean": st.mean(x["sharpe"] for x in recs),
            "ret_mean": st.mean(x["total_return"] for x in recs),
            "mdd_mean": st.mean(x["max_drawdown"] for x in recs),
            "n": len(recs),
        }
    return {"first": agg(first), "second": agg(second)}


def yearly_absolute(curves):
    by_year = {}
    for curve in curves:
        for year, ret in r19.yearly_returns(curve).items():
            by_year.setdefault(year, []).append(ret)
    return {year: st.mean(vals) for year, vals in sorted(by_year.items())}


def corr_period(phase_runs, start=None, end=None):
    corrs = []
    for a, b in zip(phase_runs["P2"], phase_runs["V20"], strict=True):
        ca = [x for x in a["curve"] if (start is None or x["date"] >= start) and (end is None or x["date"] <= end)]
        cb = [x for x in b["curve"] if (start is None or x["date"] >= start) and (end is None or x["date"] <= end)]
        ra = r19.returns_from_curve(ca)
        rb = r19.returns_from_curve(cb)
        n = min(len(ra), len(rb))
        if n > 1:
            corrs.append(st.correlation(ra[:n], rb[:n]))
    return st.mean(corrs) if corrs else None


def evaluate(tag, base, cost):
    roll = rolling_one_year(base)
    split = split_metrics(base)
    years = yearly_absolute(base)
    cost_roll = rolling_one_year(cost)
    corr_first = corr_period(base_phase, start="2018-06-01", end=SPLIT)
    corr_second = corr_period(base_phase, start="2022-07-01", end="2026-10-14")
    gates = {
        "G1_roll_pos>=90%": roll["positive_rate"] >= 0.90,
        "G2_p05>=-10%": roll["p05"] >= -0.10,
        "G2_worst>=-15%": roll["worst"] >= -0.15,
        "G3_both_sharpe>=0.80": split["first"]["sharpe_mean"] >= 0.80 and split["second"]["sharpe_mean"] >= 0.80,
        "G3_both_ret>0": split["first"]["ret_mean"] > 0 and split["second"]["ret_mean"] > 0,
        "G4_years>=6/7": sum(1 for y in ("2019", "2020", "2021", "2022", "2023", "2024", "2025") if years.get(y, -1) > 0) >= 6,
        "G5_cost_roll>=85%": cost_roll["positive_rate"] >= 0.85,
        "G6_corr<0.80_both": corr_first < 0.80 and corr_second < 0.80,
    }
    return {
        "rolling_1y_base": roll,
        "rolling_1y_cost2x": cost_roll,
        "split": split,
        "yearly_absolute": years,
        "corr_first": corr_first,
        "corr_second": corr_second,
        "gates": gates,
        "all_pass": all(gates.values()),
    }


def main():
    global base_phase
    t0 = time.time()
    base_phase = r19.run_phase_set(r19.P10)
    cost_phase = r19.run_phase_set(r19.P10, cost=True)
    out = {
        "split": SPLIT,
        "weights": WEIGHTS,
        "candidates": {},
    }
    for tag, w in WEIGHTS.items():
        out["candidates"][tag] = evaluate(tag, blend_curves(base_phase, w), blend_curves(cost_phase, w))
        x = out["candidates"][tag]
        print(
            f"[{tag}] roll_pos={x['rolling_1y_base']['positive_rate']:.1%} "
            f"p05={x['rolling_1y_base']['p05']*100:.1f}% worst={x['rolling_1y_base']['worst']*100:.1f}% "
            f"split={x['split']['first']['sharpe_mean']:.3f}/{x['split']['second']['sharpe_mean']:.3f} "
            f"cost_pos={x['rolling_1y_cost2x']['positive_rate']:.1%} all={x['all_pass']}",
            flush=True,
        )
    with open(EV + "r21_robustness_b75_b80.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print(f"saved r21_robustness_b75_b80.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
