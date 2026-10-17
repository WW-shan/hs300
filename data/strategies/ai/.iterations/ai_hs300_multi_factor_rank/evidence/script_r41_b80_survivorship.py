# 输入说明: R41 — B80/P2 历史官方 HS300 成分复验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r41_b80_survivorship.py
# 复用 R40 历史名单/价格仓与 V20 缓存, 只新增 P2 历史/当前回测。
# 结果 JSON: 同目录 r41_b80_survivorship.json。
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
R40 = None


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
r28 = r40.r28
r30 = r40.r30
r33 = r40.r33
R40_JSON = EV / "r40_survivorship.json"


def run_p2_phases(
    phases: list[date],
    membership: pl.DataFrame,
    label: str,
) -> tuple[dict[str, pl.DataFrame], dict[str, dict]]:
    from app.backtest.strategy import StrategyBacktestConfig

    service = r40.make_service()
    outputs: dict[str, pl.DataFrame] = {}
    stats: dict[str, dict] = {}
    for phase in phases:
        cache = r40.RUN_CACHE / f"{label}_{phase.isoformat()}.parquet"
        stats_cache = r40.RUN_CACHE / f"{label}_{phase.isoformat()}_stats.json"
        if cache.exists() and stats_cache.exists():
            outputs[str(phase)] = pl.read_parquet(cache)
            stats[str(phase)] = json.loads(stats_cache.read_text(encoding="utf-8"))
            continue
        config = StrategyBacktestConfig(
            strategy_id="ai_hs300_lowrisk_alpha",
            symbols=None,
            start=phase,
            end=r40.END,
            params=None,
            overrides=r28.P2_OVERRIDES,
            matching="open_t+1",
            fees_pct=0.0006,
            stamp_tax_pct=0.001,
            slippage_bps=15.0,
            max_positions=20,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        with r40.patched_membership(membership):
            result = service.run(config)
        if result.error:
            raise RuntimeError(f"{label} {phase}: {result.error}")
        frame = r28.monthly_from_curve(result.equity_curve)
        frame.write_parquet(cache)
        stats[str(phase)] = result.stats
        stats_cache.write_text(json.dumps(result.stats, ensure_ascii=False, indent=1), encoding="utf-8")
        outputs[str(phase)] = frame
        print(f"[{label}] {phase}: months={frame.height}", flush=True)
    return outputs, stats


def combo_frames(
    phases: list[date],
    p2_runs: dict[str, pl.DataFrame],
    v20_runs: dict[str, pl.DataFrame],
) -> list[pl.DataFrame]:
    empty = pl.DataFrame({"ym": [], "r": []})
    frames = []
    for phase in phases:
        runs = {
            "P2_3x_stamp": p2_runs[str(phase)],
            "V20_3x_stamp": v20_runs[str(phase)],
        }
        frames.append(
            r28.build_combo({"B80": 1.0}, phase, runs, empty, empty, "3x_stamp")
        )
    return frames


def summary(frames: list[pl.DataFrame]) -> dict:
    return {
        "metrics": r30.phase_metrics(frames),
        "rolling": r30.rolling_windows(frames),
        "yearly": [r28.stats(frame)["yearly"] for frame in frames],
    }


def compact_stats(stats: dict[str, dict]) -> dict:
    keys = (
        "total_return", "annual_return", "sharpe", "max_drawdown",
        "n_trades", "win_rate", "profit_factor", "turnover", "avg_positions",
        "avg_holding_days", "exposure",
    )
    return {
        phase: {key: value for key, value in payload.items() if key in keys}
        for phase, payload in stats.items()
    }


def main() -> int:
    r40_evidence = json.loads(R40_JSON.read_text(encoding="utf-8"))
    if not all(r40_evidence["decision"][key] for key in ("D1", "D2", "D3", "D4")):
        raise RuntimeError("R40 数据质量门槛未通过, 不能启动 R41")
    memberships, _ = r40.membership_frame()
    current_symbols = r40._current_members()
    current_frame = pl.DataFrame(
        {"effective_date": [date(1900, 1, 1)] * len(current_symbols), "symbol": current_symbols},
        schema={"effective_date": pl.Date, "symbol": pl.String},
    )
    phases = r33.phase_dates(r40.PHASE_TARGETS)

    v20_hist = r40.run_v20_phases(phases, memberships, "historical")
    v20_curr = r40.run_v20_phases(phases, current_frame, "current")
    p2_hist, p2_hist_stats = run_p2_phases(phases, memberships, "r41_p2_historical")
    p2_curr, p2_curr_stats = run_p2_phases(phases, current_frame, "r41_p2_current")

    b80_hist = summary(combo_frames(phases, p2_hist, v20_hist))
    b80_curr = summary(combo_frames(phases, p2_curr, v20_curr))
    p2_hist_summary = summary([p2_hist[str(phase)] for phase in phases])
    p2_curr_summary = summary([p2_curr[str(phase)] for phase in phases])
    v20_hist_summary = summary([v20_hist[str(phase)] for phase in phases])
    v20_curr_summary = summary([v20_curr[str(phase)] for phase in phases])

    c1 = bool(
        b80_curr["metrics"]["sharpe_mean"] >= 1.05
        and b80_curr["metrics"]["annual_mean"] >= 0.18
    )
    e1 = bool(
        b80_hist["metrics"]["sharpe_mean"] >= 1.05
        and b80_hist["metrics"]["annual_mean"] >= 0.20
        and b80_hist["metrics"]["mdd_mean"] >= -0.15
        and b80_hist["rolling"]["positive_rate"] >= 0.90
        and b80_hist["rolling"]["worst"] >= -0.08
    )
    e2 = bool(
        b80_hist["metrics"]["sharpe_mean"] >= v20_hist_summary["metrics"]["sharpe_mean"] + 0.20
        and b80_hist["metrics"]["annual_mean"] >= v20_hist_summary["metrics"]["annual_mean"] + 0.05
    )
    e3 = bool(
        p2_hist_summary["metrics"]["sharpe_mean"] >= 0.90
        and p2_hist_summary["metrics"]["annual_mean"] >= 0.10
        and p2_hist_summary["metrics"]["mdd_mean"] >= -0.15
        and p2_hist_summary["rolling"]["positive_rate"] >= 0.85
        and p2_hist_summary["rolling"]["worst"] >= -0.10
    )

    if e1 and e2:
        recommendation = "B80 remains fallback; survivorship-robust"
        production_state = "deploy_candidate"
    elif e3:
        recommendation = "P2 defensive candidate; B80 not robust"
        production_state = "defensive_candidate"
    else:
        recommendation = "No existing long-only portfolio is production-ready"
        production_state = "pause_production"

    result = {
        "window": {"phases": [str(phase) for phase in phases], "end": str(r40.END)},
        "data": {
            "r40_decision": r40_evidence["decision"],
            "membership_union": r40_evidence["membership_audit"]["union_symbols"],
            "phase_membership": r40_evidence["phase_membership"],
        },
        "historical": {
            "P2": p2_hist_summary,
            "V20": v20_hist_summary,
            "B80": b80_hist,
        },
        "current_control": {
            "P2": p2_curr_summary,
            "V20": v20_curr_summary,
            "B80": b80_curr,
        },
        "p2_stats_historical": compact_stats(p2_hist_stats),
        "p2_stats_current": compact_stats(p2_curr_stats),
        "decision": {
            "D0": True,
            "C1_current_control": c1,
            "E1_B80_historical": e1,
            "E2_B80_incremental": e2,
            "E3_P2_fallback": e3,
        },
        "adopted": bool(e1 and e2),
        "production_state": production_state,
        "final_recommendation": recommendation,
        "caveats": [
            "复用 R40 月度官方名单快照, 非逐日调样流水",
            "P2 的行业中性化使用当前行业快照, 仍有行业前视偏差",
            "历史成员财务字段来自本地当前快照, 存在报表重述风险",
            "B80 按月度 80/20 组合, 与 R33/R38 口径一致",
        ],
    }
    path = EV / "r41_b80_survivorship.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("B80 historical:", b80_hist, flush=True)
    print("B80 current:", b80_curr, flush=True)
    print("P2 historical:", p2_hist_summary, flush=True)
    print("V20 historical:", v20_hist_summary, flush=True)
    print("decision:", result["decision"], "state:", production_state, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
