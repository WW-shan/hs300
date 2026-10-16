# 输入说明: 需本地后端 (127.0.0.1:3018) 与 API Token: 运行前 `export TF_TOKEN=<token>`。
# 结果 JSON: 与本脚本同目录的 r20_weight_frontier.json。
# 本轮复用 script_r19.py 的回测与曲线工具, 但权重前沿是独立预注册实验。
import importlib.util
import json
import math
import os
import statistics as st
import time

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
spec = importlib.util.spec_from_file_location("r19", EV + "script_r19.py")
r19 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r19)


def max_drawdown(vals):
    peak = vals[0]
    worst = 0.0
    for v in vals:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def metrics(curve):
    vals = [x["value"] for x in curve]
    rets = r19.returns_from_curve(curve)
    sharpe = None
    if len(rets) > 1 and st.stdev(rets) > 0:
        sharpe = st.mean(rets) / st.stdev(rets) * math.sqrt(252)
    return {
        "total_return": vals[-1] / vals[0] - 1,
        "max_drawdown": max_drawdown(vals),
        "sharpe": sharpe,
        "daily_vol": st.stdev(rets) if len(rets) > 1 else None,
    }


def aggregate(records):
    return {
        "sharpe_mean": st.mean(x["sharpe"] for x in records),
        "mdd_mean": st.mean(x["max_drawdown"] for x in records),
        "ret_mean": st.mean(x["total_return"] for x in records),
        "records": records,
    }


def add_yearly(recs):
    years = sorted({y for r in recs for y in r19.yearly_returns(r["curve"])})
    excess = {}
    for year in years:
        vals = []
        for r in recs:
            yr = r19.yearly_returns(r["curve"])
            b = r19.benchmark_yearly(r["curve"]).get(year)
            if year in yr and b is not None:
                vals.append(yr[year] - b)
        excess[year] = st.mean(vals) if vals else None
    full = ("2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025")
    return excess, sum(1 for y in full if excess.get(y) is not None and excess[y] > 0)


def summarize_weight(phase_runs, w):
    p2 = [x["curve"] for x in phase_runs["P2"]]
    v20 = [x["curve"] for x in phase_runs["V20"]]
    recs = []
    for i, phase in enumerate(x["phase"] for x in phase_runs["P2"]):
        curve = r19.blend_curve(p2[i], v20[i], w)
        recs.append({"phase": phase, "curve": curve, **metrics(curve)})
    out = aggregate(recs)
    out["yearly_excess"], out["pos_excess_full_years"] = add_yearly(recs)
    return out


def mechanism(phase_runs):
    p2 = [x["curve"] for x in phase_runs["P2"]]
    v20 = [x["curve"] for x in phase_runs["V20"]]
    corrs = []
    for a, b in zip(p2, v20, strict=True):
        ra = r19.returns_from_curve(a)
        rb = r19.returns_from_curve(b)
        n = min(len(ra), len(rb))
        if n > 1:
            corrs.append(st.correlation(ra[:n], rb[:n]))
    return {
        "corr_mean": st.mean(corrs) if corrs else None,
        "p2_daily_vol_mean": st.mean(st.stdev(r19.returns_from_curve(x)) for x in p2),
        "v20_daily_vol_mean": st.mean(st.stdev(r19.returns_from_curve(x)) for x in v20),
    }


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k != "curve"}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def main():
    out = {
        "phases_10": r19.P10,
        "phases_1y": r19.P1Y,
        "weights": {"B50": 0.50, "B70": 0.70, "B75": 0.75, "B80": 0.80, "B85": 0.85, "B90": 0.90},
        "base_long10": {},
        "base_recent1y": {},
        "cost2x_long10": {},
    }
    t0 = time.time()
    b10 = r19.run_phase_set(r19.P10)
    b1y = r19.run_phase_set(r19.P1Y)
    c10 = r19.run_phase_set(r19.P10, cost=True)
    out["mechanism_long10"] = mechanism(b10)
    for tag, w in out["weights"].items():
        out["base_long10"][tag] = summarize_weight(b10, w)
        out["base_recent1y"][tag] = summarize_weight(b1y, w)
        out["cost2x_long10"][tag] = summarize_weight(c10, w)
        recent = out["base_recent1y"][tag]["records"]
        vals = [x["total_return"] for x in recent]
        out["base_recent1y"][tag]["recent_median"] = st.median(vals)
        out["base_recent1y"][tag]["recent_all_pos"] = all(v > 0 for v in vals)
        out["base_recent1y"][tag]["recent_returns"] = vals
        print(
            f"[frontier] {tag}: longSharpe={out['base_long10'][tag]['sharpe_mean']:.3f} "
            f"longMDD={out['base_long10'][tag]['mdd_mean']*100:.1f}% "
            f"recentMed={out['base_recent1y'][tag]['recent_median']*100:+.2f}% "
            f"allPos={out['base_recent1y'][tag]['recent_all_pos']} "
            f"costSharpe={out['cost2x_long10'][tag]['sharpe_mean']:.3f}",
            flush=True,
        )
    with open(EV + "r20_weight_frontier.json", "w", encoding="utf-8") as f:
        json.dump(compact(out), f, ensure_ascii=False, indent=1)
    print(f"saved r20_weight_frontier.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
