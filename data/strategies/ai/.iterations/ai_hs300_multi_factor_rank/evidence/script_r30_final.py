# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r30_final.py
# 作用: R30 收官 — C2/C6 在 1×/2×/3× 成本下的相位平均 + 相位内滚动 12 月窗口 + 集中度/换手 + 终选。
# 结果 JSON: 同目录 r30_final_audit.json。
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
CACHE = DATA / "research" / "r29_cache"
END = date(2026, 10, 14)
COSTS = {
    "1x": (None, None, 2 * 0.0002 + 2 * 0.0005),
    "2x": (0.0004, 10.0, 2 * 0.0004 + 2 * 0.0010),
    "3x": (0.0006, 15.0, 2 * 0.0006 + 2 * 0.0015),
}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r28 = _load("r28_combos")


def runs_for(phase: date, suffix: str) -> dict:
    key = str(phase)
    comm, slip, _ = COSTS[suffix]
    out = {}
    for tag, sid, params, overrides in (
        ("P2", "ai_hs300_lowrisk_alpha", None, r28.P2_OVERRIDES),
        ("V20", "ai_hs300_multi_factor_rank", r28.V20_PARAMS, r28.V20_OVERRIDES),
    ):
        cache_file = CACHE / f"{key}_{tag}_{suffix}.parquet"
        if cache_file.exists():
            out[f"{tag}_{suffix}"] = pl.read_parquet(cache_file)
            continue
        res = r28.run_strategy(sid, phase, END, params=params, overrides=overrides, commission=comm, slippage=slip)
        frame = r28.monthly_from_curve(res.get("equity_curve") or [])
        frame.write_parquet(cache_file)
        out[f"{tag}_{suffix}"] = frame
        print(f"[{key}] {tag} {suffix}: months={frame.height}", flush=True)
    return out


def rolling_windows(frames: list[pl.DataFrame], window: int = 12) -> dict:
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
    recs = [r28.stats(f) for f in frames if f.height >= 6]
    return {
        "sharpe_mean": round(st.mean(r["sharpe"] for r in recs), 3),
        "annual_mean": round(st.mean(r["annual"] for r in recs), 4),
        "mdd_mean": round(st.mean(r["mdd"] for r in recs), 4),
        "last_year_min": round(min(r["last_year_return"] for r in recs), 4),
        "positive_years_mean": round(st.mean(r["positive_years"] for r in recs), 2),
        "phases": [{"sharpe": r["sharpe"], "annual": r["annual"], "mdd": r["mdd"], "last_year": r["last_year_return"], "months": r["months"]} for r in recs],
    }


def concentration(legs, cost: float) -> dict:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r23_attach(symbols)
    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    d = reb
    for col, direction, q in legs:
        d = d.filter(pl.col(col).is_not_null()).with_columns((pl.col(col).rank().over("date") / pl.len().over("date")).alias("_pct"))
        d = d.filter(pl.col("_pct") <= q) if direction == "low" else d.filter(pl.col("_pct") >= 1 - q)
    per_month = d.filter(pl.col("fwd").is_not_null()).group_by("date").agg(pl.len().alias("n"), pl.col("symbol").alias("symbols")).sort("date")
    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    max_share, sizes = [], []
    for row in per_month.iter_rows(named=True):
        syms = row["symbols"]
        sizes.append(len(syms))
        joined = industry.filter(pl.col("symbol").is_in(syms))
        counts = joined.group_by("industry_l1").len().sort("len", descending=True)
        if counts.height:
            max_share.append(counts["len"][0] / max(len(syms), 1))
    return {
        "members_mean": round(st.mean(sizes), 1) if sizes else None,
        "members_min": min(sizes) if sizes else None,
        "top_industry_share_mean": round(st.mean(max_share), 3) if max_share else None,
        "months": len(sizes),
    }


def r23_attach(symbols):
    spec = importlib.util.spec_from_file_location("r23", EV / "script_r23_screen.py")
    r23 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r23)
    return r23.attach_pit(
        r23.load_panel(symbols),
        r23.derived(r23.to_quarterly(r23.merge_tables(symbols))).with_columns(pl.col("announce").alias("_eff")),
    )


def main() -> int:
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH").select(["date"]).collect().sort("date")
    )
    index_dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in idx["date"].to_list()]
    starts_target = [date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2)]
    phases = [next(d for d in index_dates if d >= t) for t in starts_target]

    out = {"phases": [str(p) for p in phases], "candidates": {}}
    for tag in ("C2_70B80_30D1", "C6_50V20_25D1_25D4"):
        out["candidates"][tag] = {}

    for suffix in ("1x", "2x", "3x"):
        d1 = r28.monthly_d_series(r28.D1_LEGS, cost=COSTS[suffix][2])
        d4 = r28.monthly_d_series(r28.D4_LEGS, cost=COSTS[suffix][2])
        for phase in phases:
            runs = runs_for(phase, suffix)
            for tag in out["candidates"]:
                frame = r28.build_combo(r28.COMBOS[tag], phase, runs, d1, d4, suffix)
                out["candidates"][tag].setdefault(suffix, []).append(frame)
        print(f"[{suffix}] phases built", flush=True)

    report = {}
    for tag in out["candidates"]:
        rec = {}
        for suffix in ("1x", "2x", "3x"):
            frames = out["candidates"][tag][suffix]
            rec[suffix] = {"metrics": phase_metrics(frames), "rolling": rolling_windows(frames)}
        report[tag] = rec

    # 与核心腿的相关 (1×, 相位内平均)
    d1_1x = r28.monthly_d_series(r28.D1_LEGS, cost=COSTS["1x"][2])
    d4_1x = r28.monthly_d_series(r28.D4_LEGS, cost=COSTS["1x"][2])
    corr_map = {}
    for tag in out["candidates"]:
        corrs = []
        for phase in phases:
            runs = runs_for(phase, "1x")
            role = "B80" if tag == "C2_70B80_30D1" else "V20"
            base = r28.build_combo({role: 1.0}, phase, runs, d1_1x, d4_1x, "1x")
            cand = r28.build_combo(r28.COMBOS[tag], phase, runs, d1_1x, d4_1x, "1x")
            j = cand.rename({"r": "rc"}).join(base.rename({"r": "rb"}), on="ym", how="inner")
            if j.height > 6 and st.pstdev(j["rc"].to_list()) > 0 and st.pstdev(j["rb"].to_list()) > 0:
                corrs.append(st.correlation(j["rc"].to_list(), j["rb"].to_list()))
        corr_map[tag] = round(st.mean(corrs), 3) if corrs else None

    conc = {
        "D1": concentration(r28.D1_LEGS, COSTS["2x"][2]),
        "D4": concentration(r28.D4_LEGS, COSTS["2x"][2]),
    }
    d_turnover = {"D1": 0.178, "D4": 0.102}  # R23 实测 (含 2× 费率口径)

    decision = {}
    for tag, rec in report.items():
        three = rec["3x"]
        s1 = three["metrics"]["sharpe_mean"] >= 0.90
        s2 = three["rolling"]["positive_rate"] is not None and three["rolling"]["positive_rate"] >= 0.90 and three["rolling"]["worst"] >= -0.08
        s3 = bool(
            15 <= (conc["D1"]["members_mean"] or 0) <= 35
            and 15 <= (conc["D4"]["members_mean"] or 0) <= 35
            and (conc["D1"]["top_industry_share_mean"] or 1) <= 0.40
            and (conc["D4"]["top_industry_share_mean"] or 1) <= 0.40
        )
        s4 = bool(max(d_turnover["D1"], d_turnover["D4"]) <= 0.25)
        s5 = corr_map[tag] is not None and corr_map[tag] < 0.95
        decision[tag] = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5,
                         "sharpe_3x": three["metrics"]["sharpe_mean"], "mdd_3x": three["metrics"]["mdd_mean"],
                         "all_pass": bool(s1 and s2 and s3 and s4 and s5)}

    eligible = [t for t, d in decision.items() if d["all_pass"]]
    final = None
    if eligible:
        eligible.sort(key=lambda t: -report[t]["3x"]["metrics"]["sharpe_mean"])
        top = eligible[0]
        if len(eligible) > 1 and abs(report[eligible[0]]["3x"]["metrics"]["sharpe_mean"] - report[eligible[1]]["3x"]["metrics"]["sharpe_mean"]) <= 0.02:
            top = min(eligible[:2], key=lambda t: report[t]["3x"]["metrics"]["mdd_mean"] * -1)  # MDD 更浅者 (mdd 为负, 更浅=更大)
        final = top

    result = {
        "candidates": report,
        "corr_with_core": corr_map,
        "concentration": conc,
        "d_turnover": d_turnover,
        "stress_checks": decision,
        "eligible": eligible,
        "final_recommendation": final,
        "final_note": "终选按 R30 预注册规则; 若 final 为 None → 维持 B80 默认",
    }
    path = EV / "r30_final_audit.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path)
    for tag, rec in report.items():
        m1, m2, m3 = rec["1x"]["metrics"], rec["2x"]["metrics"], rec["3x"]["metrics"]
        r3 = rec["3x"]["rolling"]
        print(f"{tag}: 1x sharpe={m1['sharpe_mean']} mdd={m1['mdd_mean']} | 2x={m2['sharpe_mean']} | 3x={m3['sharpe_mean']} mdd={m3['mdd_mean']} | 3x rolling pos={r3['positive_rate']} worst={r3['worst']} | corr={corr_map[tag]}")
    print("concentration:", conc)
    print("decision:", decision)
    print("final:", final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
