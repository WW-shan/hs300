"""R111: v0.3.7 regression of the frozen C6_IND configuration.

Run from backend:
  uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r111_c6_ind_v037_regression.py
"""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r111_c6_cache"
END = date(2026, 10, 16)
PHASE_TARGETS = [
    date(2022, 5, 5),
    date(2022, 11, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
    date(2024, 7, 1),
    date(2025, 1, 2),
]
SCENARIOS = {
    "1x_tax05": {
        "commission_pct": 0.0002,
        "slippage_bps": 5.0,
        "stamp_tax_pct": 0.0005,
        "d_round_trip": 0.0019,
    },
    "2x_tax05": {
        "commission_pct": 0.0004,
        "slippage_bps": 10.0,
        "stamp_tax_pct": 0.0005,
        "d_round_trip": 0.0033,
    },
    "3x_tax10": {
        "commission_pct": 0.0006,
        "slippage_bps": 15.0,
        "stamp_tax_pct": 0.0010,
        "d_round_trip": 0.0052,
    },
}


def _load(name: str):
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


def run_v20(phase: date, scenario: dict, suffix: str) -> pl.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{phase}_{suffix}.parquet"
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
        fees_pct=scenario["commission_pct"],
        commission_pct=scenario["commission_pct"],
        stamp_tax_pct=scenario["stamp_tax_pct"],
        slippage_bps=scenario["slippage_bps"],
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


def run_scenario(
    phases: list[date],
    d1_rows: list[dict],
    d4_rows: list[dict],
    name: str,
    scenario: dict,
) -> dict:
    d1 = r36.cost_series(d1_rows, scenario["d_round_trip"])
    d4 = r36.cost_series(d4_rows, scenario["d_round_trip"])
    frames = []
    v20_frames = []
    v20_metrics = []
    for phase in phases:
        v20 = run_v20(phase, scenario, name)
        v20_frames.append(v20)
        v20_metrics.append(r28.stats(v20))
        # build_combo eagerly constructs the B80 sleeve even when only V20 is used.
        dummy_p2 = v20.select("ym").with_columns(pl.lit(0.0).alias("r"))
        runs = {
            f"P2_{name}": dummy_p2,
            f"V20_{name}": v20,
        }
        frames.append(
            r28.build_combo(
                {"V20": 0.5, "D1": 0.25, "D4": 0.25},
                phase,
                runs,
                d1,
                d4,
                name,
            )
        )
        print(
            f"[{name}] {phase}: C6 months={frames[-1].height} "
            f"V20={v20_metrics[-1]['sharpe']}",
            flush=True,
        )
    return {
        "metrics": r30.phase_metrics(frames),
        "rolling": r30.rolling_windows(frames),
        "v20_phase_metrics": v20_metrics,
        "_frames": frames,
        "_v20_frames": v20_frames,
    }


def correlation_with_v20(frames: list[pl.DataFrame], v20_frames: list[pl.DataFrame]) -> float | None:
    corrs = []
    for cand, base in zip(frames, v20_frames, strict=True):
        joined = (
            cand.rename({"r": "candidate"})
            .join(base.rename({"r": "v20"}), on="ym", how="inner")
        )
        if (
            joined.height > 6
            and st.pstdev(joined["candidate"].to_list()) > 0
            and st.pstdev(joined["v20"].to_list()) > 0
        ):
            corrs.append(st.correlation(joined["candidate"].to_list(), joined["v20"].to_list()))
    return round(st.mean(corrs), 3) if corrs else None


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    reb, _snapshot = r32.build_engine_rebalances()
    d1_ind = r31.select_members(reb, r31.D1_IND_LEGS, group=["date", "industry_l1"], min_size=8)
    d4 = r31.select_members(reb, r28.D4_LEGS)
    panel = r36.load_execution_panel(symbols)
    d1_rows, d1_exec = r36.t1_gross(d1_ind, panel)
    d4_rows, d4_exec = r36.t1_gross(d4, panel)
    phases = r33.phase_dates(PHASE_TARGETS)
    print("phases:", phases, flush=True)
    print("execution:", {"D1_IND": d1_exec, "D4": d4_exec}, flush=True)

    results = {}
    for name, scenario in SCENARIOS.items():
        result = run_scenario(phases, d1_rows, d4_rows, name, scenario)
        results[name] = result
        print(
            f"[{name}] C6 sharpe={result['metrics']['sharpe_mean']} "
            f"annual={result['metrics']['annual_mean']} "
            f"mdd={result['metrics']['mdd_mean']} rolling={result['rolling']}",
            flush=True,
        )

    _, d1_stats = r31.series_and_stats(d1_ind, SCENARIOS["2x_tax05"]["d_round_trip"])
    _, d4_stats = r31.series_and_stats(d4, SCENARIOS["2x_tax05"]["d_round_trip"])
    industry_null_share = round(reb["industry_l1"].null_count() / max(reb.height, 1), 6)
    corr = correlation_with_v20(results["1x_tax05"]["_frames"], results["1x_tax05"]["_v20_frames"])

    old = json.loads((EV / "r38_all_in_cost.json").read_text(encoding="utf-8"))
    old_three = old["scenarios"]["3x_stamp_10"]
    new_three = results["3x_tax10"]
    deltas = {
        "sharpe": round(new_three["metrics"]["sharpe_mean"] - old_three["metrics"]["sharpe_mean"], 4),
        "annual": round(new_three["metrics"]["annual_mean"] - old_three["metrics"]["annual_mean"], 4),
        "mdd": round(new_three["metrics"]["mdd_mean"] - old_three["metrics"]["mdd_mean"], 4),
        "rolling_positive": round(new_three["rolling"]["positive_rate"] - old_three["rolling"]["positive_rate"], 4),
        "rolling_worst": round(new_three["rolling"]["worst"] - old_three["rolling"]["worst"], 4),
    }

    d1_ok = (
        15 <= d1_stats["members_mean"] <= 35
        and 15 <= d4_stats["members_mean"] <= 35
        and d1_stats["top_industry_share_mean"] <= 0.40
        and d4_stats["top_industry_share_mean"] <= 0.40
        and d1_stats["top_industry_share_max"] <= 0.40
        and d4_stats["top_industry_share_max"] <= 0.40
        and d1_stats["turnover_mean"] <= 0.25
        and d4_stats["turnover_mean"] <= 0.25
        and industry_null_share == 0.0
    )
    p1 = (
        new_three["metrics"]["sharpe_mean"] >= 1.05
        and new_three["metrics"]["annual_mean"] >= 0.20
        and new_three["metrics"]["mdd_mean"] >= -0.15
        and new_three["rolling"]["positive_rate"] >= 0.90
        and new_three["rolling"]["worst"] >= -0.08
    )
    p2 = (
        abs(deltas["sharpe"]) <= 0.05
        and abs(deltas["annual"]) <= 0.02
        and abs(deltas["mdd"]) <= 0.01
        and abs(deltas["rolling_positive"]) <= 0.02
        and abs(deltas["rolling_worst"]) <= 0.02
    )
    p3 = new_three["metrics"]["last_year_min"] > 0
    c1 = corr is not None and corr < 0.95
    decision = {
        "D1": d1_ok,
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "C1": c1,
        "all_pass": bool(d1_ok and p1 and p2 and p3 and c1),
    }

    for result in results.values():
        result.pop("_frames", None)
        result.pop("_v20_frames", None)

    output = {
        "window": {
            "start": str(phases[0]),
            "end": str(END),
            "phases": [str(p) for p in phases],
            "rebalance": "monthly",
        },
        "engine": {"version": "0.3.7", "execution": "open_t+1", "hs300": "current members"},
        "scenarios": results,
        "execution": {"D1_IND": d1_exec, "D4": d4_exec},
        "d1_stats": d1_stats,
        "d4_stats": d4_stats,
        "industry_null_share": industry_null_share,
        "corr_with_v20_1x": corr,
        "r38_comparison": {
            "old": {
                "sharpe": old_three["metrics"]["sharpe_mean"],
                "annual": old_three["metrics"]["annual_mean"],
                "mdd": old_three["metrics"]["mdd_mean"],
                "rolling_positive": old_three["rolling"]["positive_rate"],
                "rolling_worst": old_three["rolling"]["worst"],
            },
            "delta": deltas,
        },
        "decision": decision,
        "final_recommendation": (
            "C6_IND current-members research candidate (not deployable)"
            if decision["all_pass"]
            else None
        ),
        "production_state": "pause_production",
        "survivorship_note": (
            "R111 uses current HS300 members and current industry snapshot. "
            "R40/R79 historical-membership failures remain binding; this run cannot restore production."
        ),
    }
    path = EV / "r111_c6_ind_v037_regression.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("deltas:", deltas, flush=True)
    print("decision:", decision, "| corr:", corr, flush=True)
    print("final:", output["final_recommendation"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())