from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
R40 = DATA / "research/r40_engine_data"
CACHE = DATA / "research/r366_cache"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY_MEMBERSHIP = DATA / "research/r157_membership/monthly_snapshots.parquet"
PRE_REG = EVIDENCE / "r366_neutralization_preregistration_v2.json"
PREFLIGHT = EVIDENCE / "r366_neutralization_preflight_v2.json"
RESULT = EVIDENCE / "r366_neutralization_backtest.json"
FINAL_STATUS = EVIDENCE / "r366_final_status.json"
RESULT_MD = EVIDENCE / "r366_result.md"
R365_RESULT = EVIDENCE / "r365_weak_reversal_20d_backtest.json"
R365_AUDIT = EVIDENCE / "r365_preregistered_gate_audit.json"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]
REGIME_FILTER = {"states": ["weak"]}
VARIANTS = {
    "industry": {
        "candidate": "ai_hs300_weak_reversal_blend_industry_h20_sl20",
        "control": "ai_hs300_quality_industry_h20_sl20",
    },
    "industry_size": {
        "candidate": "ai_hs300_weak_reversal_blend_industry_size_h20_sl20",
        "control": "ai_hs300_quality_industry_size_h20_sl20",
    },
}
COSTS = {
    2: {"fees_pct": 0.0004, "commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
    3: {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.0010, "slippage_bps": 15.0},
}

sys.path.insert(0, str(ROOT / "backend"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def input_fingerprints() -> dict:
    return module(EVIDENCE / "run_r366_neutralization_preflight.py", "r366_input_fingerprints").input_fingerprints()


def make_config(strategy_id: str, start: date, cost: int):
    from app.backtest.strategy import StrategyBacktestConfig

    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=END,
        params={},
        overrides={},
        matching="open_t+1",
        max_positions=10,
        max_exposure_pct=1.0,
        initial_capital=1_000_000.0,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        holding_days=20,
        regime_filter=dict(REGIME_FILTER),
        **COSTS[cost],
    )


def membership() -> pl.DataFrame:
    monthly = pl.read_parquet(MONTHLY_MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    daily = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    return pl.concat([monthly, daily], how="vertical_relaxed").unique().sort(["effective_date", "symbol"])


def curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def stats(curve: pl.DataFrame) -> dict:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    annual = (1.0 + total) ** (252.0 / len(returns)) - 1.0
    deviation = st.pstdev(returns)
    sharpe = st.mean(returns) / deviation * math.sqrt(252.0) if deviation else 0.0
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {"total_return": total, "annual": annual, "sharpe": sharpe, "mdd": drawdown, "days": len(values)}


def segment_return(curve: pl.DataFrame, start: date) -> float | None:
    part = curve.filter(pl.col("date").is_between(start, END)).sort("date")
    if part.height < 2:
        return None
    values = part["value"].to_list()
    return float(values[-1] / values[0] - 1.0)


def run_key(variant: str, label: str, start: date, cost: int) -> str:
    return f"{variant}_{label}_{start}_{cost}x"


def main() -> int:
    for output in (RESULT, RESULT.with_name(RESULT.name + ".sha256"), FINAL_STATUS, RESULT_MD, CACHE):
        if output.exists():
            raise RuntimeError(f"R366 output/cache exists; refusing overwrite: {output}")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight.get("all_checks") or preflight.get("no_return_or_backtest_evaluated") is not True:
        raise RuntimeError("R366 preflight failed or read returns")
    before = input_fingerprints()
    if before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R366 data fingerprints differ from preregistration")
    if preflight.get("preregistration_sha256") != sha(PRE_REG):
        raise RuntimeError("R366 preflight preregistration hash mismatch")
    preflight_checksum = PREFLIGHT.with_name(PREFLIGHT.name + ".sha256").read_text(encoding="ascii").split()[0]
    if preflight_checksum != sha(PREFLIGHT):
        raise RuntimeError("R366 preflight checksum mismatch")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R366 frozen code changed: {relative}")
    for relative, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R366 strategy changed: {relative}")

    from app.backtest import strategy as strategy_module
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services import regime_builder
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    previous_data_dir = Path(settings.data_dir)
    original_membership = strategy_module._hs300_membership_frame
    work = Path(tempfile.mkdtemp(prefix="r366_engine_"))
    curves: dict[str, pl.DataFrame] = {}
    trade_counts: dict[str, int] = {}
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        isolation = module(EVIDENCE / "r365_isolation.py", "r366_isolation")
        isolation_report = isolation.prepare_isolated_data_root(engine_root, DATA)
        if not isolation_report["isolated"] or isolation_report["remaining_top_level_symlinks"]:
            raise RuntimeError("R366 engine inputs are not isolated")
        settings.data_dir = engine_root
        from app.factors.ext_factors import invalidate_ext_caches
        invalidate_ext_caches(engine_root, keep_strategy_cache=True)
        regime_builder._REGIME_HISTORY_CACHE = None
        regime_builder._REGIME_HISTORY_SIG = None
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        strategy_engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r366_strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), strategy_engine
        )
        members = membership()
        strategy_module._hs300_membership_frame = lambda _start, _end: members
        CACHE.mkdir(parents=True, exist_ok=False)
        for variant, pair in VARIANTS.items():
            for label, strategy_id in (("candidate", pair["candidate"]), ("control", pair["control"])):
                for start in PHASES:
                    for cost in (2, 3):
                        key = run_key(variant, label, start, cost)
                        config = make_config(strategy_id, start, cost)
                        result = service.run(config)
                        if result.error:
                            raise RuntimeError(f"R366 {key}: {result.error}")
                        curve = curve_frame(result.equity_curve)
                        if curve["date"].min() != start or curve["date"].max() != END:
                            raise RuntimeError(f"R366 curve date coverage mismatch: {key}")
                        curve.write_parquet(CACHE / f"{key}.parquet", compression="zstd")
                        curves[key] = curve
                        trade_counts[key] = int(result.stats.get("n_trades", len(result.trades)))
                        print(f"R366 ran {key}: trades={trade_counts[key]}", flush=True)
    finally:
        settings.data_dir = previous_data_dir
        strategy_module._hs300_membership_frame = original_membership
        regime_builder._REGIME_HISTORY_CACHE = None
        regime_builder._REGIME_HISTORY_SIG = None
        pipeline.invalidate_custom_signals()
        from app.factors.ext_factors import ensure_synced, invalidate_ext_caches
        invalidate_ext_caches(previous_data_dir, keep_strategy_cache=True)
        ensure_synced(previous_data_dir)
        load_into_registry(previous_data_dir)
        shutil.rmtree(work, ignore_errors=True)

    after = input_fingerprints()
    if before != after:
        raise RuntimeError("R366 source data changed during backtest")

    metrics: dict = {}
    for variant, _pair in VARIANTS.items():
        metrics[variant] = {}
        for label in ("candidate", "control"):
            phase_curves = {str(start): curves[run_key(variant, label, start, 2)] for start in PHASES}
            main_curve = phase_curves[str(START)]
            metrics[variant][label] = {
                "2x": stats(main_curve),
                "3x": stats(curves[run_key(variant, label, START, 3)]),
                "recent_2x": segment_return(main_curve, RECENT_START),
                "phase_2x": {phase: stats(curve) for phase, curve in phase_curves.items()},
                "trades": {
                    run_key(variant, label, start, cost): trade_counts[run_key(variant, label, start, cost)]
                    for start in PHASES for cost in (2, 3)
                },
            }

    r365 = json.loads(R365_RESULT.read_text(encoding="utf-8"))
    comparisons = {}
    for variant, _pair in VARIANTS.items():
        candidate = metrics[variant]["candidate"]
        control = metrics[variant]["control"]
        comparisons[variant] = {
            "candidate_minus_control_annual_2x": candidate["2x"]["annual"] - control["2x"]["annual"],
            "candidate_minus_control_recent_2x": candidate["recent_2x"] - control["recent_2x"],
            "candidate_minus_control_sharpe_2x": candidate["2x"]["sharpe"] - control["2x"]["sharpe"],
            "candidate_minus_control_mdd_2x": candidate["2x"]["mdd"] - control["2x"]["mdd"],
            "candidate_minus_control_annual_edge_change_vs_r365": (
                candidate["2x"]["annual"] - control["2x"]["annual"]
                - (r365["metrics"]["candidate"]["2x"]["annual"] - r365["metrics"]["control"]["2x"]["annual"])
            ),
            "candidate_annual_change_vs_r365": candidate["2x"]["annual"] - r365["metrics"]["candidate"]["2x"]["annual"],
            "candidate_recent_change_vs_r365": candidate["recent_2x"] - r365["metrics"]["candidate"]["recent_2x"],
            "candidate_mdd_change_vs_r365": candidate["2x"]["mdd"] - r365["metrics"]["candidate"]["2x"]["mdd"],
            "candidate_sharpe_change_vs_r365": candidate["2x"]["sharpe"] - r365["metrics"]["candidate"]["2x"]["sharpe"],
        }

    primary = metrics["industry_size"]["candidate"]
    primary_control = metrics["industry_size"]["control"]
    r365_candidate = r365["metrics"]["candidate"]
    primary_edge = comparisons["industry_size"]["candidate_minus_control_annual_2x"]
    raw_edge = r365_candidate["2x"]["annual"] - r365["metrics"]["control"]["2x"]["annual"]
    gates = {
        "G1_candidate_annual_no_more_than_5pp_below_r365": primary["2x"]["annual"] >= r365_candidate["2x"]["annual"] - 0.05,
        "G2_candidate_sharpe_no_more_than_0_15_below_r365": primary["2x"]["sharpe"] >= r365_candidate["2x"]["sharpe"] - 0.15,
        "G3_candidate_mdd_no_more_than_3pp_worse_than_r365": primary["2x"]["mdd"] >= r365_candidate["2x"]["mdd"] - 0.03,
        "G4_candidate_control_edge_no_more_than_2pp_worse_than_r365": primary_edge >= raw_edge - 0.02,
        "G5_candidate_recent_no_more_than_2pp_below_r365": primary["recent_2x"] >= r365_candidate["recent_2x"] - 0.02,
        "G6_candidate_annual_within_1pp_of_neutral_control": primary["2x"]["annual"] >= primary_control["2x"]["annual"] - 0.01,
        "G7_at_least_4_of_6_candidate_phases_positive": sum(item["total_return"] > 0 for item in primary["phase_2x"].values()) >= 4,
        "G8_candidate_3x_cost_annual_nonnegative": primary["3x"]["annual"] >= 0,
        "G9_unlevered": True,
    }
    payload = {
        "round": "R366",
        "classification": "paired weak-regime 20-session factor sleeve neutralization test",
        "hypothesis": prereg["hypothesis"],
        "rule": prereg["rule"],
        "preflight_sha256": sha(PREFLIGHT),
        "preregistration_sha256": sha(PRE_REG),
        "input_hashes_before": before,
        "input_hashes_after": after,
        "input_isolation": isolation_report,
        "metrics": metrics,
        "comparison_vs_r365_raw_candidate_and_control": comparisons,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "selected": None,
        "decision": {
            "status": "research_only_no_candidate_after_r366" if not all(gates.values()) else "research_only_candidate_for_followup",
            "leverage": "none",
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "trade_counts_by_run": trade_counts,
        "limits": prereg["known_limits"],
    }
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    status = {
        "round": "R366",
        "status": payload["decision"]["status"],
        "all_gates_pass": all(gates.values()),
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "selected": None,
        "no_leverage": True,
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "result_sha256": sha(RESULT),
    }
    FINAL_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FINAL_STATUS.with_name(FINAL_STATUS.name + ".sha256").write_text(f"{sha(FINAL_STATUS)}  {FINAL_STATUS.name}\n", encoding="ascii")
    note = [
        "# R366 · 弱市情绪反转复合行业/规模中性化",
        "",
        f"主检验(行业+规模中性): 候选年化 {primary['2x']['annual']:.2%}, Sharpe {primary['2x']['sharpe']:.3f}, MDD {primary['2x']['mdd']:.2%}, 近一年 {primary['recent_2x']:.2%}; 配对质量对照年化 {primary_control['2x']['annual']:.2%}, Sharpe {primary_control['2x']['sharpe']:.3f}, MDD {primary_control['2x']['mdd']:.2%}, 近一年 {primary_control['recent_2x']:.2%}.",
        f"行业中性诊断候选年化 {metrics['industry']['candidate']['2x']['annual']:.2%}, Sharpe {metrics['industry']['candidate']['2x']['sharpe']:.3f}, MDD {metrics['industry']['candidate']['2x']['mdd']:.2%}, 近一年 {metrics['industry']['candidate']['recent_2x']:.2%}.",
        f"行业+规模中性相对 R365 原策略: 年化变化 {comparisons['industry_size']['candidate_annual_change_vs_r365']:+.2%}; Sharpe变化 {comparisons['industry_size']['candidate_sharpe_change_vs_r365']:+.3f}; 年化 edge 差分 {comparisons['industry_size']['candidate_minus_control_annual_edge_change_vs_r365']:+.2%}; 近一年变化 {comparisons['industry_size']['candidate_recent_change_vs_r365']:+.2%}.",
        f"门槛通过 {sum(gates.values())}/{len(gates)}; selected=null. 中性化结果仅作配对研究, 未启用生产。",
        "无杠杆、无纸面目标、无订单、生产未启用。",
        "",
        "证据：`r366_neutralization_preflight_v2.json`、`r366_neutralization_backtest.json`、`r366_final_status.json`。",
        "",
    ]
    RESULT_MD.write_text("\n".join(note), encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps({"metrics": metrics, "comparisons": comparisons, "gates": gates, "status": status}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
