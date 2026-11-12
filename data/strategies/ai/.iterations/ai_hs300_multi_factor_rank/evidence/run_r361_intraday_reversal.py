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
CACHE = DATA / "research/r361_cache"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
PRE_REG = EVIDENCE / "r361_intraday_reversal_preregistration.json"
PREFLIGHT = EVIDENCE / "r361_intraday_reversal_preflight.json"
RESULT = EVIDENCE / "r361_intraday_reversal_backtest.json"
FINAL_STATUS = EVIDENCE / "r361_final_status.json"
RESULT_MD = EVIDENCE / "r361_result.md"
CANDIDATE = "ai_hs300_industry_relative_strength_intraday_reversal_weak_h20_sl20"
CONTROL = "ai_hs300_industry_relative_strength_quality_weak_h20_sl20"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]
REGIME_FILTER = {"states": ["weak"]}
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


def tree_hash(root: Path, *, follow_symlinks: bool = False) -> dict:
    files = sorted(root.rglob("*"))
    files = [file for file in files if file.is_file() and (follow_symlinks or not file.is_symlink())]
    digest = hashlib.sha256()
    size = 0
    for file in files:
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(file).encode())
        digest.update(b"\n")
        size += file.stat().st_size
    return {"files": len(files), "bytes": size, "sha256": digest.hexdigest()}


def make_backtest_config(strategy_id: str, start: date, cost: int):
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
        regime_filter=dict(REGIME_FILTER),
        **COSTS[cost],
    )


def membership() -> pl.DataFrame:
    monthly = (
        pl.read_parquet(DATA / "research/r157_membership/monthly_snapshots.parquet", columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
    )
    daily = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
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


def segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    part = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        return None
    values = part["value"].to_list()
    return float(values[-1] / values[0] - 1.0)


def _date_count_by_year(curve: pl.DataFrame) -> dict[str, int]:
    return {
        str(year): group.height
        for year, group in curve.group_by_dynamic("date", every="1y", closed="left", label="left")
    }


def main() -> int:
    for output in (RESULT, RESULT.with_name(RESULT.name + ".sha256"), FINAL_STATUS, RESULT_MD):
        if output.exists():
            raise RuntimeError(f"R361 output exists; refusing overwrite: {output.name}")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight.get("all_checks") or preflight.get("no_return_or_backtest_evaluated") is not True:
        raise RuntimeError("R361 preflight failed or has already read returns")
    for name, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError(f"R361 frozen artifact changed: {name}")
    if preflight.get("preregistration_sha256") != sha(PRE_REG):
        raise RuntimeError("R361 preflight registration hash mismatch")
    if preflight.get("backtest_runner_sha256") != sha(Path(__file__).resolve()):
        raise RuntimeError("R361 runner changed after preflight")
    checksum = PREFLIGHT.with_name(PREFLIGHT.name + ".sha256").read_text(encoding="ascii").split()[0]
    if checksum != sha(PREFLIGHT):
        raise RuntimeError("R361 preflight checksum mismatch")

    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r361")
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

    before = {
        "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
        "production_enriched": tree_hash(DATA / "kline_daily_enriched", follow_symlinks=True),
        "regime_history": sha(DATA / "regime_history/part.parquet"),
        "membership": sha(MEMBERSHIP),
        "alt_factors": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
    }
    work = Path(tempfile.mkdtemp(prefix="r361_engine_"))
    previous_data_dir = Path(settings.data_dir)
    original_membership = strategy_module._hs300_membership_frame
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        isolation = module(EVIDENCE / "r361_isolation.py", "r361_isolation_runner")
        isolation_report = isolation.prepare_isolated_data_root(engine_root, DATA)
        if not isolation_report["isolated"] or isolation_report["remaining_top_level_symlinks"]:
            raise RuntimeError("R361 engine data contains source-linked inputs")
        settings.data_dir = engine_root
        from app.factors.ext_factors import invalidate_ext_caches
        invalidate_ext_caches(engine_root, keep_strategy_cache=True)
        regime_builder._REGIME_HISTORY_CACHE = None
        regime_builder._REGIME_HISTORY_SIG = None
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()

        strategy_engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r361_strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), strategy_engine,
        )
        members = membership()
        strategy_module._hs300_membership_frame = lambda _start, _end: members
        CACHE.mkdir(parents=True, exist_ok=True)
        curves: dict[str, dict[str, pl.DataFrame]] = {"candidate": {}, "control": {}}
        trade_counts: dict[str, dict[str, int]] = {"candidate": {}, "control": {}}
        gate_counts: dict[str, dict[str, int]] = {"candidate": {}, "control": {}}
        for label, strategy_id in (("candidate", CANDIDATE), ("control", CONTROL)):
            for start in PHASES:
                for cost in (2, 3):
                    key = f"{label}_{start}_{cost}x"
                    curve_path = CACHE / f"{key}.parquet"
                    if curve_path.exists():
                        raise RuntimeError(f"R361 cache exists; refusing overwrite: {curve_path.name}")
                    config = make_backtest_config(strategy_id, start, cost)
                    result = service.run(config)
                    if result.error:
                        raise RuntimeError(f"R361 {key}: {result.error}")
                    curve = curve_frame(result.equity_curve)
                    if curve["date"].min() != start or curve["date"].max() != END:
                        raise RuntimeError(f"R361 curve date coverage mismatch: {key}")
                    curve.write_parquet(curve_path, compression="zstd")
                    curves[label][key] = curve
                    trade_counts[label][key] = int(result.stats.get("n_trades", len(result.trades)))
                    gate_counts[label][key] = int(result.stats.get("n_trades", len(result.trades)))
                    print(f"R361 ran {key}: trades={trade_counts[label][key]}", flush=True)
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

    after = {
        "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
        "production_enriched": tree_hash(DATA / "kline_daily_enriched", follow_symlinks=True),
        "regime_history": sha(DATA / "regime_history/part.parquet"),
        "membership": sha(MEMBERSHIP),
        "alt_factors": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
    }
    if before != after:
        raise RuntimeError("R361 source data changed during run")

    main_curves = {label: curves[label][f"{label}_{START}_2x"] for label in ("candidate", "control")}
    metrics = {
        label: {
            "2x": stats(curves[label][f"{label}_{START}_2x"]),
            "3x": stats(curves[label][f"{label}_{START}_3x"]),
            "recent_2x": segment_return(curves[label][f"{label}_{START}_2x"], RECENT_START),
            "yearly_2x": r315.year_returns(curves[label][f"{label}_{START}_2x"]),
            "phase_2x": {str(phase): stats(curves[label][f"{label}_{phase}_2x"]) for phase in PHASES},
            "trades": trade_counts[label],
        }
        for label in ("candidate", "control")
    }
    gates = {
        "G1_candidate_2x_annual_at_least_control_minus_1pp": metrics["candidate"]["2x"]["annual"] >= metrics["control"]["2x"]["annual"] - 0.01,
        "G2_candidate_2x_sharpe_at_least_control_minus_0_10": metrics["candidate"]["2x"]["sharpe"] >= metrics["control"]["2x"]["sharpe"] - 0.10,
        "G3_candidate_2x_mdd_not_deeper_than_control": metrics["candidate"]["2x"]["mdd"] >= metrics["control"]["2x"]["mdd"],
        "G4_candidate_recent_2x_nonnegative": metrics["candidate"]["recent_2x"] is not None and metrics["candidate"]["recent_2x"] >= 0,
        "G5_2022_segment_nonnegative": segment_return(main_curves["candidate"], START, date(2022, 12, 30)) >= 0,
        "G6_at_least_4_of_6_candidate_phases_positive": sum(metrics["candidate"]["phase_2x"][str(phase)]["total_return"] > 0 for phase in PHASES) >= 4,
        "G7_3x_annual_nonnegative": metrics["candidate"]["3x"]["annual"] >= 0,
        "G8_unlevered": True,
    }
    result_payload = {
        "round": "R361",
        "classification": "preregistered weak-regime matched B-sleeve factor replacement test",
        "hypothesis": prereg["hypothesis"],
        "rule": prereg["rule"],
        "preflight_sha256": sha(PREFLIGHT),
        "preregistration_sha256": sha(PRE_REG),
        "input_hashes_before": before,
        "input_hashes_after": after,
        "input_isolation": isolation_report,
        "metrics": metrics,
        "gate_counts": gate_counts,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "selected": None,
        "decision": {"status": "research_only_no_candidate_after_r361" if not all(gates.values()) else "research_only_candidate_for_followup", "leverage": "none", "paper_target_generated": False, "orders_generated": False, "production_enabled": False},
        "limits": prereg["known_limits"],
    }
    RESULT.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    final_status = {
        "round": "R361",
        "status": result_payload["decision"]["status"],
        "all_gates_pass": all(gates.values()),
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "selected": None,
        "no_leverage": True,
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "result_sha256": sha(RESULT),
    }
    FINAL_STATUS.write_text(json.dumps(final_status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FINAL_STATUS.with_name(FINAL_STATUS.name + ".sha256").write_text(f"{sha(FINAL_STATUS)}  {FINAL_STATUS.name}\n", encoding="ascii")
    note = [
        "# R361 · 弱市条件下的日内反转质量腿",
        "",
        "规则冻结：弱市条件读取 T-1 state；T 日按 B 策略评分，T+1 开盘成交。两组持仓数、筛选、20 日持有、-20%止损一致。",
        f"2x 候选年化 {metrics['candidate']['2x']['annual']:.2%}、Sharpe {metrics['candidate']['2x']['sharpe']:.3f}、MDD {metrics['candidate']['2x']['mdd']:.2%}；对照年化 {metrics['control']['2x']['annual']:.2%}、Sharpe {metrics['control']['2x']['sharpe']:.3f}、MDD {metrics['control']['2x']['mdd']:.2%}。",
        f"候选近期一年 {metrics['candidate']['recent_2x']:.2%}；门槛通过 {sum(gates.values())}/{len(gates)}；selected=null。",
        "非独立样本，弱市 regime 与情绪变量仍有机械依赖；无杠杆、无纸面目标、无订单、生产未启用。",
        "",
        "证据：`r361_intraday_reversal_preflight.json`、`r361_intraday_reversal_backtest.json`、`r361_final_status.json`。",
        "",
    ]
    RESULT_MD.write_text("\n".join(note), encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps({"metrics": metrics, "gates": gates, "status": final_status}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
