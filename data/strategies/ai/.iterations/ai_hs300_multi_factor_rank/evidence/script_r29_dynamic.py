# 输入说明: 进程内运行 (无需后端; 策略回测走 worker 子进程)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r29_dynamic.py
# 作用: R29 预注册 — R1 状态切换(R24 表) / R2 动量状态闸门 / R3 宽度仓位; 对照静态 C2/C6/V20。
# 结果 JSON: 同目录 r29_regime_positioning.json; 月收益序列缓存 data/research/r29_cache/ (不提交, 供 R30 复用)。
import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
CACHE = DATA / "research" / "r29_cache"
CACHE.mkdir(parents=True, exist_ok=True)
END = date(2026, 10, 14)
LAST_YEAR_YM = "2025-10"
ROUND_TRIP_2X = 0.0028
STRONG_STATES = ("strong", "range")          # R1 用 C2 的状态
MOMENTUM_STATES = ("weak", "lean_weak", "lean_strong")  # R2 允许持有 V20 的状态


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")
r28 = _load("r28_combos")

D1_LEGS = r28.D1_LEGS
D4_LEGS = r28.D4_LEGS


def get_runs(phase: date) -> dict:
    """回测 P2/V20 (1×/2×), 缓存月收益。"""
    key = str(phase)
    runs = {}
    for tag, sid, params, overrides in (
        ("P2", "ai_hs300_lowrisk_alpha", None, r28.P2_OVERRIDES),
        ("V20", "ai_hs300_multi_factor_rank", r28.V20_PARAMS, r28.V20_OVERRIDES),
    ):
        for cost_tag, comm, slip in (("1x", None, None), ("2x", 0.0004, 10.0)):
            cache_file = CACHE / f"{key}_{tag}_{cost_tag}.parquet"
            if cache_file.exists():
                runs[f"{tag}_{cost_tag}"] = pl.read_parquet(cache_file)
                continue
            res = r28.run_strategy(sid, phase, END, params=params, overrides=overrides, commission=comm, slippage=slip)
            frame = r28.monthly_from_curve(res.get("equity_curve") or [])
            frame.write_parquet(cache_file)
            runs[f"{tag}_{cost_tag}"] = frame
            print(f"[{key}] {tag} {cost_tag}: months={frame.height}", flush=True)
    return runs


def regime_monthly(index_dates: list[date]) -> pl.DataFrame:
    """每个调仓日的上一调仓日 state/breadth (严格早于当日)。"""
    reg = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .select(["date", "state", "above_ma20_pct"])
        .with_columns(pl.col("date").cast(pl.Date)).sort("date")
    )
    firsts, seen = [], set()
    for d in index_dates:
        if str(d)[:7] not in seen:
            seen.add(str(d)[:7])
            firsts.append(d)
    rows = []
    for d in firsts:
        prev = reg.filter(pl.col("date") < d)
        if prev.height == 0:
            continue
        last = prev.tail(1).row(0, named=True)
        rows.append({"ym": d.strftime("%Y-%m"), "prev_state": last["state"], "prev_breadth": last["above_ma20_pct"]})
    return pl.DataFrame(rows)


def stats(monthly: pl.DataFrame) -> dict:
    return r28.stats(monthly)


def rolling_pool(frames: list[pl.DataFrame], window: int = 12) -> dict:
    """相位内滚动窗口, 跨相位不拼接 (R28 相位口径)。"""
    vals = []
    for f in frames:
        series = f.sort("ym")["r"].to_list()
        for i in range(window, len(series) + 1):
            prod = 1.0
            for v in series[i - window:i]:
                prod *= 1.0 + v
            vals.append(prod - 1.0)
    if not vals:
        return {"n": 0, "positive_rate": None, "worst": None, "p10": None}
    vals.sort()
    return {
        "n": len(vals),
        "positive_rate": round(sum(v > 0 for v in vals) / len(vals), 4),
        "worst": round(vals[0], 4),
        "p10": round(vals[int(len(vals) * 0.1)], 4),
    }


def phase_metrics(frames: list[pl.DataFrame]) -> dict:
    recs = [stats(f) for f in frames if f is not None and f.height >= 6]
    if not recs:
        return {"sharpe_mean": None, "annual_mean": None, "mdd_mean": None, "last_year_min": None,
                "positive_years_mean": None, "n_phases": 0}
    import statistics as _st

    return {
        "sharpe_mean": round(_st.mean(r["sharpe"] for r in recs if r["sharpe"] is not None), 3),
        "annual_mean": round(_st.mean(r["annual"] for r in recs if r["annual"] is not None), 4),
        "mdd_mean": round(_st.mean(r["mdd"] for r in recs), 4),
        "last_year_min": round(min(r["last_year_return"] for r in recs), 4),
        "positive_years_mean": round(_st.mean(r["positive_years"] for r in recs), 2),
        "n_phases": len(recs),
    }


def evaluate(name: str, frames: list[pl.DataFrame], baseline: str, base_frames: list[pl.DataFrame],
             switches: list[float], frames_2x: list[pl.DataFrame], base_2x: list[pl.DataFrame],
             result: dict) -> None:
    s = phase_metrics(frames)
    b = phase_metrics(base_frames)
    r = rolling_pool(frames)
    br = rolling_pool(base_frames)
    s2 = phase_metrics(frames_2x)
    b2 = phase_metrics(base_2x)
    total_switches = sum(switches)
    total_months = sum(f.height for f in frames)
    switches_per_year = round(total_switches / max(total_months, 1) * 12, 2)
    g1 = s["sharpe_mean"] is not None and b["sharpe_mean"] is not None and s["sharpe_mean"] >= b["sharpe_mean"] - 0.05
    g2 = bool(r["n"] > 0 and br["n"] > 0 and r["positive_rate"] >= br["positive_rate"] and r["worst"] >= br["worst"] - 0.02)
    g3 = bool(s2["sharpe_mean"] is not None and b2["sharpe_mean"] is not None
              and s2["sharpe_mean"] >= 0.90 and s2["sharpe_mean"] >= b2["sharpe_mean"] - 0.05)
    g4 = switches_per_year <= 6.0
    g5 = s["last_year_min"] is not None and b["last_year_min"] is not None and s["last_year_min"] >= b["last_year_min"] - 0.01
    result[name] = {
        "stats": s, "rolling": r, "sharpe_2x": s2["sharpe_mean"], "mdd_2x": s2["mdd_mean"],
        "baseline": baseline, "baseline_stats": b, "baseline_rolling": br, "baseline_sharpe_2x": b2["sharpe_mean"],
        "switches_per_year": switches_per_year,
        "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5,
        "all_pass": bool(g1 and g2 and g3 and g4 and g5),
    }


def main() -> int:
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH").select(["date"]).collect().sort("date")
    )
    index_dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    starts_target = [date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    phases = [next(d for d in index_dates if d >= t) for t in starts_target]
    regime = regime_monthly(index_dates)
    print("phases:", [str(p) for p in phases], flush=True)

    d1 = r28.monthly_d_series(D1_LEGS)
    d4 = r28.monthly_d_series(D4_LEGS)

    out = {"phases": [str(p) for p in phases], "rules": {}}
    rule_frames: dict[str, list[pl.DataFrame]] = {"R1": [], "R2": [], "R3": [], "C2": [], "C6": [], "V20": []}
    rule_2x: dict[str, list[pl.DataFrame]] = {"R1": [], "R2": [], "R3": [], "C2": [], "C6": [], "V20": []}
    switches: dict[str, list[float]] = {"R1": [], "R2": [], "R3": []}

    for phase in phases:
        key = str(phase)
        runs = get_runs(phase)
        c2_1x = r28.build_combo(r28.COMBOS["C2_70B80_30D1"], phase, runs, d1, d4, "1x")
        c6_1x = r28.build_combo(r28.COMBOS["C6_50V20_25D1_25D4"], phase, runs, d1, d4, "1x")
        c2_2x = r28.build_combo(r28.COMBOS["C2_70B80_30D1"], phase, runs, d1, d4, "2x")
        c6_2x = r28.build_combo(r28.COMBOS["C6_50V20_25D1_25D4"], phase, runs, d1, d4, "2x")
        v20_1x = runs["V20_1x"].filter(pl.col("ym") >= phase.strftime("%Y-%m")).select(["ym", "r"])
        v20_2x = runs["V20_2x"].filter(pl.col("ym") >= phase.strftime("%Y-%m")).select(["ym", "r"])

        # R1: 上月末 state 选择 C2/C6
        r1_rows, r1_2x_rows, r1_switch = [], [], []
        r2_rows, r2_2x_rows, r2_switch = [], [], []
        r3_rows, r3_2x_rows, r3_switch = [], [], []
        prev_choice = None
        prev_mom, prev_expo = None, None
        months = c2_1x["ym"].to_list()
        for ym in months:
            sig = regime.filter(pl.col("ym") == ym)
            if sig.height == 0:
                continue
            state = sig["prev_state"][0]
            breadth = sig["prev_breadth"][0]
            pick = c2_1x if state in STRONG_STATES else c6_1x
            pick2 = c2_2x if state in STRONG_STATES else c6_2x
            row = pick.filter(pl.col("ym") == ym)
            row2 = pick2.filter(pl.col("ym") == ym)
            if row.height == 0 or row2.height == 0:
                continue
            r1_rows.append({"ym": ym, "r": row["r"][0]})
            r1_2x_rows.append({"ym": ym, "r": row2["r"][0]})
            choice = "C2" if state in STRONG_STATES else "C6"
            r1_switch.append(0.5 if prev_choice is not None and choice != prev_choice else 0.0)
            prev_choice = choice

            in_mom = state in MOMENTUM_STATES
            v_row = v20_1x.filter(pl.col("ym") == ym)
            v_row2 = v20_2x.filter(pl.col("ym") == ym)
            if v_row.height and v_row2.height:
                r2_rows.append({"ym": ym, "r": v_row["r"][0] if in_mom else 0.0})
                r2_2x_rows.append({"ym": ym, "r": v_row2["r"][0] if in_mom else 0.0})
                r2_switch.append(1.0 if prev_mom is not None and in_mom != prev_mom else (1.0 if prev_mom is None else 0.0))
                prev_mom = in_mom

            expo = 1.0 if (breadth is not None and breadth >= 0.5) else 0.5
            row6 = c6_1x.filter(pl.col("ym") == ym)
            row6b = c6_2x.filter(pl.col("ym") == ym)
            r3_rows.append({"ym": ym, "r": row6["r"][0] * expo})
            r3_2x_rows.append({"ym": ym, "r": row6b["r"][0] * expo})
            r3_switch.append(abs(expo - prev_expo) if prev_expo is not None else 1.0)
            prev_expo = expo

        rule_frames["R1"].append(pl.DataFrame(r1_rows))
        rule_frames["R2"].append(pl.DataFrame(r2_rows))
        rule_frames["R3"].append(pl.DataFrame(r3_rows))
        rule_2x["R1"].append(pl.DataFrame(r1_2x_rows))
        rule_2x["R2"].append(pl.DataFrame(r2_2x_rows))
        rule_2x["R3"].append(pl.DataFrame(r3_2x_rows))
        rule_frames["C2"].append(c2_1x)
        rule_frames["C6"].append(c6_1x)
        rule_frames["V20"].append(v20_1x)
        rule_2x["C2"].append(c2_2x)
        rule_2x["C6"].append(c6_2x)
        rule_2x["V20"].append(v20_2x)
        switches["R1"].append(sum(r1_switch))
        switches["R2"].append(sum(r2_switch))
        switches["R3"].append(sum(r3_switch))
        print(f"[{key}] rules built (C2/C6/V20 baseline months={c2_1x.height})", flush=True)

    res = {}
    for rule, base in (("R1", "C2"), ("R2", "V20"), ("R3", "C6")):
        evaluate(rule, rule_frames[rule], base, rule_frames[base], switches[rule],
                 rule_2x[rule], rule_2x[base], res)

    # R1 额外对照更优基线 (C2/C6 中 Sharpe 更高者)
    c2m = phase_metrics(rule_frames["C2"])
    c6m = phase_metrics(rule_frames["C6"])
    best = "C6" if (c6m["sharpe_mean"] or 0) > (c2m["sharpe_mean"] or 0) else "C2"
    if best != "C2":
        evaluate("R1_vs_best", rule_frames["R1"], best, rule_frames[best], switches["R1"],
                 rule_2x["R1"], rule_2x[best], res)

    out["rules"] = res
    out["baselines"] = {
        "C2": phase_metrics(rule_frames["C2"]),
        "C6": phase_metrics(rule_frames["C6"]),
        "V20": phase_metrics(rule_frames["V20"]),
    }
    out["adopt"] = sorted([k for k, v in res.items() if v.get("all_pass")])
    path = EV / "r29_regime_positioning.json"
    path.write_text(json.dumps(r22.compact(out), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path)
    for k, v in res.items():
        print(k, "sharpe", v["stats"]["sharpe_mean"], "base", v["baseline"], v["baseline_stats"]["sharpe_mean"],
              "roll_pos", v["rolling"]["positive_rate"], "base", v["baseline_rolling"]["positive_rate"],
              "2x", v["sharpe_2x"], "switch/yr", v["switches_per_year"], "all_pass", v.get("all_pass"))
    print("adopt:", out["adopt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
