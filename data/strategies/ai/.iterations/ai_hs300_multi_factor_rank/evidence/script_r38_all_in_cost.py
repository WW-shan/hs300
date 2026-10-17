# 输入说明: 进程内运行 (无需后端; V20 新回测写 R29 缓存)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r38_all_in_cost.py
# 作用: R38 — C6_IND 在 3× + 卖出印花税下的全成本压力。
# 结果 JSON: 同目录 r38_all_in_cost.json。
import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r29_cache"
END = date(2026, 10, 14)
PHASE_TARGETS = [
    date(2022, 5, 5), date(2022, 11, 1), date(2023, 1, 3), date(2023, 7, 3),
    date(2024, 1, 2), date(2024, 7, 1), date(2025, 1, 2),
]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r28 = _load("r28_combos")
r30 = _load("r30_final")
r31 = _load("r31_d1_industry")
r32 = _load("r32_pit_recheck")
r33 = _load("r33_parity_7phase")
r36 = _load("r36_execution")


def run_v20_all_in(phase: date, stamp_tax: float, suffix: str) -> pl.DataFrame:
    cache_file = CACHE / f"{phase}_V20_{suffix}.parquet"
    if cache_file.exists():
        return pl.read_parquet(cache_file)
    from app.backtest.strategy import StrategyBacktestConfig
    from app.backtest.worker import make_worker_task, run_worker_task
    from app.config import settings

    cfg = StrategyBacktestConfig(
        strategy_id="ai_hs300_multi_factor_rank",
        symbols=None,
        start=phase,
        end=END,
        params=r28.V20_PARAMS,
        overrides=r28.V20_OVERRIDES,
        matching="open_t+1",
        fees_pct=0.0006,
        commission_pct=0.0006,
        stamp_tax_pct=stamp_tax,
        slippage_bps=15.0,
        max_positions=20,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    result = run_worker_task(make_worker_task("backtest", settings.data_dir, cfg))
    frame = r28.monthly_from_curve(result.get("equity_curve") or [])
    frame.write_parquet(cache_file)
    return frame


def scenario(phases: list[date], d1: pl.DataFrame, d4: pl.DataFrame, suffix: str, stamp_tax: float) -> dict:
    frames = []
    v20_metrics = []
    for phase in phases:
        v20 = run_v20_all_in(phase, stamp_tax, suffix)
        v20_metrics.append(r28.stats(v20))
        # build_combo 会急切构造 B80 (P2+V20) sleeve, 即使权重只用 V20;
        # 这里给 P2 一个同 ym 的占位序列, 避免为未使用的 sleeve 多跑 7 个回测。
        dummy_p2 = v20.select(["ym"]).with_columns(pl.lit(0.0).alias("r"))
        runs = {f"P2_{suffix}": dummy_p2, f"V20_{suffix}": v20}
        frames.append(r28.build_combo(
            {"V20": 0.5, "D1": 0.25, "D4": 0.25}, phase, runs, d1, d4, suffix
        ))
    return {
        "metrics": r30.phase_metrics(frames),
        "rolling": r30.rolling_windows(frames),
        "v20_phase_metrics": v20_metrics,
    }


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    reb, _ = r32.build_engine_rebalances()
    d1_ind = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    panel = r36.load_execution_panel(symbols)
    d1_rows, d1_exec = r36.t1_gross(d1_ind, panel)
    d4_rows, d4_exec = r36.t1_gross(d4, panel)
    phases = r33.phase_dates(PHASE_TARGETS)

    costs = {
        "3x_stamp_05": 0.0042 + 0.0005,
        "3x_stamp_10": 0.0042 + 0.0010,
    }
    d1_sets = {name: r36.cost_series(d1_rows, cost) for name, cost in costs.items()}
    d4_sets = {name: r36.cost_series(d4_rows, cost) for name, cost in costs.items()}

    results = {}
    for name, stamp_tax in (("3x_stamp_05", 0.0005), ("3x_stamp_10", 0.0010)):
        results[name] = scenario(phases, d1_sets[name], d4_sets[name], name, stamp_tax)
        m = results[name]["metrics"]
        print(f"[{name}] sharpe={m['sharpe_mean']} annual={m['annual_mean']} mdd={m['mdd_mean']} "
              f"rolling={results[name]['rolling']}", flush=True)

    r37 = json.loads((EV / "r37_execution_gate.json").read_text(encoding="utf-8"))
    base_sharpe = r37["t1_3x_metrics"]["sharpe_mean"]
    ten = results["3x_stamp_10"]
    m10 = ten["metrics"]
    r10 = ten["rolling"]
    decision = {
        "C1": m10["sharpe_mean"] >= 1.05,
        "C2": m10["annual_mean"] >= 0.20 and m10["mdd_mean"] >= -0.15,
        "C3": r10["positive_rate"] >= 0.90 and r10["worst"] >= -0.08,
        "C4": m10["sharpe_mean"] / base_sharpe >= 0.75,
        "C5": bool(
            15 <= r37["d1_stats"]["members_mean"] <= 35
            and 15 <= r37["d4_stats"]["members_mean"] <= 35
            and r37["d1_stats"]["top_industry_share_mean"] <= 0.40
            and r37["d4_stats"]["top_industry_share_mean"] <= 0.40
            and max(r37["d1_stats"]["turnover_mean"], r37["d4_stats"]["turnover_mean"]) <= 0.25
        ),
    }
    decision["all_pass"] = all(decision.values())
    result = {
        "window": {"start": "2022-05", "end": str(END), "phases": [str(p) for p in phases]},
        "costs": {"3x_stamp_05": 0.0047, "3x_stamp_10": 0.0052},
        "execution": {"D1_IND": d1_exec, "D4": d4_exec},
        "scenarios": results,
        "r37_base": {
            "sharpe": base_sharpe,
            "annual": r37["t1_3x_metrics"]["annual_mean"],
            "mdd": r37["t1_3x_metrics"]["mdd_mean"],
            "rolling": r37["t1_3x_rolling"],
        },
        "decision": decision,
        "final_recommendation": "C6_IND all-in cost feasible" if decision["all_pass"] else None,
        "final_note": "3x+0.1% 卖出印花税全过 → 进入生产策略实现; 否则保持研究候选",
    }
    path = EV / "r38_all_in_cost.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision:", decision, "| base sharpe:", base_sharpe, "| ten sharpe:", m10["sharpe_mean"], flush=True)
    print("saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
