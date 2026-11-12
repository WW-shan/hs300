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
CACHE = DATA / "research/r367_cache"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY_MEMBERSHIP = DATA / "research/r157_membership/monthly_snapshots.parquet"
PRE_REG = EVIDENCE / "r367_size_neutralization_preregistration.json"
PREFLIGHT = EVIDENCE / "r367_size_neutralization_preflight.json"
RESULT = EVIDENCE / "r367_size_neutralization_backtest.json"
FINAL_STATUS = EVIDENCE / "r367_final_status.json"
RESULT_MD = EVIDENCE / "r367_result.md"
R365_RESULT = EVIDENCE / "r365_weak_reversal_20d_backtest.json"
R366_RESULT = EVIDENCE / "r366_neutralization_backtest.json"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]
REGIME_FILTER = {"states": ["weak"]}
CANDIDATE = "ai_hs300_weak_reversal_blend_size_h20_sl20"
CONTROL = "ai_hs300_quality_size_h20_sl20"
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
    return module(EVIDENCE / "run_r367_size_neutralization_preflight.py", "r367_input_fingerprints").input_fingerprints()


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


def run_key(label: str, start: date, cost: int) -> str:
    return f"size_{label}_{start}_{cost}x"


def main() -> int:
    for output in (RESULT, RESULT.with_name(RESULT.name + ".sha256"), FINAL_STATUS, RESULT_MD, CACHE):
        if output.exists():
            raise RuntimeError(f"R367 output/cache exists; refusing overwrite: {output}")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight.get("all_checks") or preflight.get("no_return_or_backtest_evaluated") is not True:
        raise RuntimeError("R367 preflight failed or evaluated returns")
    before = input_fingerprints()
    if before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R367 data fingerprints differ from preregistration")
    if preflight.get("preregistration_sha256") != sha(PRE_REG):
        raise RuntimeError("R367 preflight preregistration hash mismatch")
    if PREFLIGHT.with_name(PREFLIGHT.name + ".sha256").read_text(encoding="ascii").split()[0] != sha(PREFLIGHT):
        raise RuntimeError("R367 preflight checksum mismatch")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R367 frozen implementation changed: {relative}")
    for relative, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R367 frozen strategy changed: {relative}")

    from app.backtest import strategy as strategy_module
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestService
    from app.config import settings
    from app.factors.ext_factors import invalidate_ext_caches
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services import regime_builder
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    old_data_dir = Path(settings.data_dir)
    old_membership_fn = strategy_module._hs300_membership_frame
    work = Path(tempfile.mkdtemp(prefix="r367_engine_"))
    curves: dict[str, pl.DataFrame] = {}
    trade_counts: dict[str, int] = {}
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        isolation_helper = module(EVIDENCE / "r365_isolation.py", "r367_isolation")
        isolation = isolation_helper.prepare_isolated_data_root(engine_root, DATA)
        if isolation["remaining_top_level_symlinks"]:
            raise RuntimeError("R367 engine inputs are not isolated")
        settings.data_dir = engine_root
        invalidate_ext_caches(engine_root, keep_strategy_cache=True)
        regime_builder._REGIME_HISTORY_CACHE = None
        regime_builder._REGIME_HISTORY_SIG = None
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        strategy_engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r367_strategies"],
            override_loader=lambda _sid: {},
        )
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), strategy_engine
        )
        members = membership()
        strategy_module._hs300_membership_frame = lambda _start, _end: members
        CACHE.mkdir(parents=True, exist_ok=False)
        for label, strategy_id in (("candidate", CANDIDATE), ("control", CONTROL)):
            for start in PHASES:
                for cost in (2, 3):
                    key = run_key(label, start, cost)
                    result = service.run(make_config(strategy_id, start, cost))
                    if result.error:
                        raise RuntimeError(f"R367 {key}: {result.error}")
                    curve = curve_frame(result.equity_curve)
                    if curve["date"].min() != start or curve["date"].max() != END:
                        raise RuntimeError(f"R367 curve date coverage mismatch: {key}")
                    curve.write_parquet(CACHE / f"{key}.parquet", compression="zstd")
                    curves[key] = curve
                    trade_counts[key] = int(result.stats.get("n_trades", len(result.trades)))
                    print(f"R367 ran {key}: trades={trade_counts[key]}", flush=True)
    finally:
        settings.data_dir = old_data_dir
        strategy_module._hs300_membership_frame = old_membership_fn
        regime_builder._REGIME_HISTORY_CACHE = None
        regime_builder._REGIME_HISTORY_SIG = None
        pipeline.invalidate_custom_signals()
        invalidate_ext_caches(old_data_dir, keep_strategy_cache=True)
        load_into_registry(old_data_dir)
        shutil.rmtree(work, ignore_errors=True)

    after = input_fingerprints()
    if before != after:
        raise RuntimeError("R367 source fingerprints changed during backtest")

    metrics = {"size": {}}
    for label in ("candidate", "control"):
        run_scorecard = {
            run_key(label, start, cost): {
                "metrics": stats(curves[run_key(label, start, cost)]),
                "trades": trade_counts[run_key(label, start, cost)],
            }
            for start in PHASES for cost in (2, 3)
        }
        phase_curves = {str(start): curves[run_key(label, start, 2)] for start in PHASES}
        main_curve = phase_curves[str(START)]
        metrics["size"][label] = {
            "2x": stats(main_curve),
            "3x": stats(curves[run_key(label, START, 3)]),
            "recent_2x": segment_return(main_curve, RECENT_START),
            "phase_2x": {phase: stats(curve) for phase, curve in phase_curves.items()},
            "scorecard": run_scorecard,
        }

    r365 = json.loads(R365_RESULT.read_text(encoding="utf-8"))
    r366 = json.loads(R366_RESULT.read_text(encoding="utf-8"))
    candidate, control = metrics["size"]["candidate"], metrics["size"]["control"]
    candidate_edge = candidate["2x"]["annual"] - control["2x"]["annual"]
    raw_edge = r365["metrics"]["candidate"]["2x"]["annual"] - r365["metrics"]["control"]["2x"]["annual"]
    industry_only = r366["metrics"]["industry"]["candidate"]
    industry_size = r366["metrics"]["industry_size"]["candidate"]
    comparisons = {
        "candidate_minus_control_annual_2x": candidate_edge,
        "candidate_minus_control_recent_2x": candidate["recent_2x"] - control["recent_2x"],
        "candidate_minus_control_sharpe_2x": candidate["2x"]["sharpe"] - control["2x"]["sharpe"],
        "candidate_minus_control_mdd_2x": candidate["2x"]["mdd"] - control["2x"]["mdd"],
        "candidate_minus_control_annual_edge_change_vs_r365": candidate_edge - raw_edge,
        "candidate_annual_change_vs_r365": candidate["2x"]["annual"] - r365["metrics"]["candidate"]["2x"]["annual"],
        "candidate_recent_change_vs_r365": candidate["recent_2x"] - r365["metrics"]["candidate"]["recent_2x"],
        "candidate_sharpe_change_vs_r365": candidate["2x"]["sharpe"] - r365["metrics"]["candidate"]["2x"]["sharpe"],
        "candidate_mdd_change_vs_r365": candidate["2x"]["mdd"] - r365["metrics"]["candidate"]["2x"]["mdd"],
        "candidate_annual_change_vs_r366_industry_only": candidate["2x"]["annual"] - industry_only["2x"]["annual"],
        "candidate_recent_change_vs_r366_industry_only": candidate["recent_2x"] - industry_only["recent_2x"],
        "candidate_annual_change_vs_r366_industry_size": candidate["2x"]["annual"] - industry_size["2x"]["annual"],
        "candidate_recent_change_vs_r366_industry_size": candidate["recent_2x"] - industry_size["recent_2x"],
    }
    gates = {
        "G1_size_only_annual_above_industry_plus_size": candidate["2x"]["annual"] > industry_size["2x"]["annual"],
        "G2_size_only_annual_above_industry_only": candidate["2x"]["annual"] > industry_only["2x"]["annual"],
        "G3_size_only_recent_improves_5pp_vs_industry_plus_size": comparisons["candidate_recent_change_vs_r366_industry_size"] >= 0.05,
        "G4_candidate_annual_not_more_than_15pp_below_raw_r365": candidate["2x"]["annual"] >= r365["metrics"]["candidate"]["2x"]["annual"] - 0.15,
        "G5_candidate_sharpe_within_0_15_of_raw_r365": candidate["2x"]["sharpe"] >= r365["metrics"]["candidate"]["2x"]["sharpe"] - 0.15,
        "G6_candidate_mdd_within_3pp_of_raw_r365": candidate["2x"]["mdd"] >= r365["metrics"]["candidate"]["2x"]["mdd"] - 0.03,
        "G7_candidate_annual_above_matched_control": candidate_edge > 0,
        "G8_at_least_4_of_6_candidate_phases_positive_and_3x_nonnegative": sum(item["total_return"] > 0 for item in candidate["phase_2x"].values()) >= 4 and candidate["3x"]["annual"] >= 0,
        "G9_unlevered": True,
    }
    payload = {
        "round": "R367",
        "classification": "paired weak-regime 20-session size-only neutralization test",
        "hypothesis": prereg["hypothesis"],
        "rule": prereg["rule"],
        "preflight_sha256": sha(PREFLIGHT),
        "preregistration_sha256": sha(PRE_REG),
        "input_hashes_before": before,
        "input_hashes_after": after,
        "input_isolation": isolation,
        "metrics": metrics,
        "comparisons": comparisons,
        "scorecard": {label: metrics["size"][label]["scorecard"] for label in ("candidate", "control")},
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "selected": None,
        "decision": {
            "status": "research_only_no_candidate_after_r367" if not all(gates.values()) else "research_only_candidate_for_followup",
            "leverage": "none",
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "limits": prereg["known_limits"],
    }
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    status = {
        "round": "R367",
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
        "# R367 · 弱市情绪反转复合仅规模中性化",
        "",
        f"仅规模中性候选年化 {candidate['2x']['annual']:.2%}, Sharpe {candidate['2x']['sharpe']:.3f}, MDD {candidate['2x']['mdd']:.2%}, 近一年 {candidate['recent_2x']:.2%}; 配对质量对照年化 {control['2x']['annual']:.2%}, Sharpe {control['2x']['sharpe']:.3f}, MDD {control['2x']['mdd']:.2%}, 近一年 {control['recent_2x']:.2%}.",
        f"相对 R365 raw：年化/Sharpe/MDD/近一年变化 {comparisons['candidate_annual_change_vs_r365']:+.2%}/{comparisons['candidate_sharpe_change_vs_r365']:+.3f}/{comparisons['candidate_mdd_change_vs_r365']:+.2%}/{comparisons['candidate_recent_change_vs_r365']:+.2%}.",
        f"相对 R366 行业+规模中性：年化/近期变化 {comparisons['candidate_annual_change_vs_r366_industry_size']:+.2%}/{comparisons['candidate_recent_change_vs_r366_industry_size']:+.2%}；相对 R366 行业单中性：年化/近期变化 {comparisons['candidate_annual_change_vs_r366_industry_only']:+.2%}/{comparisons['candidate_recent_change_vs_r366_industry_only']:+.2%}.",
        f"门槛通过 {sum(gates.values())}/{len(gates)}; selected=null. 中性化结果仅作配对研究, 未启用生产。",
        "无杠杆、无纸面目标、无订单、生产未启用。",
        "",
        "证据：`r367_size_neutralization_preflight.json`、`r367_size_neutralization_backtest.json`、`r367_final_status.json`。",
        "",
    ]
    RESULT_MD.write_text("\n".join(note), encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps({"metrics": metrics, "comparisons": comparisons, "gates": gates, "status": status}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
