#!/usr/bin/env python3
"""R-GBM-001: comprehensive synthetic-market stress through the project matcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import synthetic_gbm as gbm  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "synthetic_market_stress"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r_gbm_001_preregistration.json"
)
RESEARCH_ID = "R-GBM-001"
RUNNER = "scripts/research_gbm_market_stress.py"
GENERATOR = "backend/app/backtest/synthetic_gbm.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = ROOT / RESEARCH_PREREGISTRATION
RESULT = RESEARCH / "r_gbm_001_result.json"
MARKDOWN = RESEARCH / "r_gbm_001_result.md"
START = date(2024, 1, 2)
PATH_COUNT = 100
DEFAULT_WORKERS = min(8, max(1, os.cpu_count() or 1))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"immutable research artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_sidecar(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n",
        encoding="ascii",
    )


def scientific_design() -> dict[str, Any]:
    end = gbm.trading_days(START, gbm.EVALUATION_DAYS)[-1]
    expected_cells = (
        len(gbm.SCENARIOS)
        * len(gbm.CORRELATIONS)
        * len(gbm.SIGNAL_MODES)
        * len(gbm.STOP_VARIANTS)
        * len(gbm.COST_MULTIPLIERS)
    )
    return {
        "model": "geometric_brownian_motion",
        "master_seed": gbm.DEFAULT_MASTER_SEED,
        "path_count": PATH_COUNT,
        "start": START.isoformat(),
        "end": end.isoformat(),
        "warmup_days": gbm.WARMUP_DAYS,
        "evaluation_days": gbm.EVALUATION_DAYS,
        "asset_count": gbm.ASSET_COUNT,
        "industry_count": gbm.INDUSTRY_COUNT,
        "industry_size": gbm.INDUSTRY_SIZE,
        "scenarios": [
            {
                "scenario_id": scenario.scenario_id,
                "annual_drift": scenario.annual_drift,
                "annual_volatility": scenario.annual_volatility,
            }
            for scenario in gbm.SCENARIOS.values()
        ],
        "regime_count": len(gbm.SCENARIOS),
        "correlations": list(gbm.CORRELATIONS),
        "correlation_count": len(gbm.CORRELATIONS),
        "signal_modes": [
            {
                "name": "random_null",
                "definition": "deterministic uniform random score independent of prices",
            },
            {
                "name": "momentum_20",
                "definition": "cross-sectional percentile of 20-day return",
            },
            {
                "name": "momentum_60",
                "definition": "cross-sectional percentile of 60-day return",
            },
            {
                "name": "reversal_5",
                "definition": "cross-sectional percentile of negative 5-day return",
            },
            {
                "name": "low_volatility_20",
                "definition": "cross-sectional percentile of negative 20-day volatility",
            },
            {
                "name": "momentum_low_vol_20",
                "definition": "equal blend of 20-day momentum and low-volatility scores",
            },
        ],
        "signal_mode_count": len(gbm.SIGNAL_MODES),
        "entry_score": gbm.ENTRY_SCORE,
        "exit_score": gbm.EXIT_SCORE,
        "top_rank": gbm.TOP_RANK,
        "industry_cap": gbm.INDUSTRY_CAP,
        "stop_variants": [
            {"name": name, "stop_loss_pct": value}
            for name, value in gbm.STOP_VARIANTS
        ],
        "stop_variant_count": len(gbm.STOP_VARIANTS),
        "cost_multipliers": list(gbm.COST_MULTIPLIERS),
        "cost_multiplier_count": len(gbm.COST_MULTIPLIERS),
        "cost_table": {
            str(multiplier): gbm.cost_parameters(multiplier)
            for multiplier in gbm.COST_MULTIPLIERS
        },
        "execution": {
            "matching": "open_t+1",
            "entry_delay_bars": 1,
            "exit_delay_bars": 1,
            "max_positions": gbm.MAX_POSITIONS,
            "max_hold_days": gbm.MAX_HOLD_DAYS,
            "initial_capital": gbm.INITIAL_CAPITAL,
            "position_sizing": "equal",
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "baseline_cost_multiplier": 2,
            "stress_cost_multiplier": 3,
            "five_x_policy": "warning_only",
        },
        "benchmark": "same_path_equal_weight_buy_and_hold",
        "expected_cells": expected_cells,
        "expected_strategy_observations": expected_cells * PATH_COUNT,
        "expected_benchmark_observations": (
            len(gbm.SCENARIOS)
            * len(gbm.CORRELATIONS)
            * len(gbm.COST_MULTIPLIERS)
            * PATH_COUNT
        ),
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    prereg = root / RESEARCH_PREREGISTRATION
    if prereg.exists() or prereg.with_name(prereg.name + ".sha256").exists():
        raise FileExistsError(f"R-GBM-001 preregistration already exists: {prereg}")
    design = scientific_design()
    generator = root / GENERATOR
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Under a frozen GBM world, how do the project execution skeleton, "
            "price-based signal modes, stop loss, costs, and market correlation "
            "change paired performance versus the same-path equal-weight control?"
        ),
        "hypothesis": (
            "Completing every synthetic cell will distinguish execution, turnover, "
            "cost, stop, and correlation effects from a random-null benchmark, but "
            "will not establish real-factor alpha."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "synthetic_gbm",
        "input_hashes": {
            "generator": sha256(generator),
        },
        "parameters": design,
        "sample": {
            "start": design["start"],
            "end": design["end"],
            "warmup_days": design["warmup_days"],
            "evaluation_days": design["evaluation_days"],
            "asset_count": design["asset_count"],
            "industry_count": design["industry_count"],
            "path_count": design["path_count"],
            "expected_cells": design["expected_cells"],
            "expected_strategy_observations": design["expected_strategy_observations"],
            "expected_benchmark_observations": design["expected_benchmark_observations"],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "synthetic_model": {
            "model": "geometric_brownian_motion",
            "parameters": design,
            "seed": gbm.DEFAULT_MASTER_SEED,
            "path_count": PATH_COUNT,
            "benchmark": "same_path_equal_weight_buy_and_hold",
        },
        "gates": {
            "all_288_cells_present": True,
            "paths_per_cell": PATH_COUNT,
            "strategy_observations": design["expected_strategy_observations"],
            "benchmark_observations": design["expected_benchmark_observations"],
            "all_signal_modes_present": True,
            "all_scenarios_present": True,
            "all_costs_present": True,
            "all_stop_variants_present": True,
            "same_path_benchmark_required": True,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_hard_rejection": False,
            "five_x_policy": "warning_only",
            "deterministic_replay_required": True,
        },
        "falsification_checks": [
            "same_seed_byte_identical_replay",
            "future_prices_do_not_change_past_scores",
            "signal_t_fills_at_t_plus_one_open_only",
            "ohlc_and_volume_invariants",
            "cost_table_exact_2x_3x_5x",
            "no_leverage_and_max_exposure_at_or_below_100pct",
            "same_path_equal_weight_benchmark",
            "all_cells_have_exact_path_count",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(prereg, raw)
    _write_sidecar(prereg)
    return prereg


def _verify_runner_binding(root: Path) -> None:
    prereg_hash = verify_sidecar(root / RESEARCH_PREREGISTRATION)
    payload = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    if payload["research_id"] != RESEARCH_ID or payload["research_type"] != RESEARCH_TYPE:
        raise RuntimeError("R-GBM-001 preregistration identity mismatch")
    if payload["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R-GBM-001 runner hash does not match frozen preregistration")
    if payload.get("input_hashes", {}).get("generator") != sha256(root / GENERATOR):
        raise RuntimeError("R-GBM-001 generator hash does not match frozen preregistration")
    if not prereg_hash:
        raise RuntimeError("R-GBM-001 preregistration sidecar is empty")


def _jobs(path_count: int) -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": scenario_id,
            "beta": beta,
            "path_id": path_id,
            "master_seed": gbm.DEFAULT_MASTER_SEED,
        }
        for scenario_id in gbm.SCENARIOS
        for beta in gbm.CORRELATIONS
        for path_id in range(path_count)
    ]


def _validate_path_payload(job: dict[str, Any], payload: dict[str, Any], path_count: int) -> None:
    expected_strategy = len(gbm.SIGNAL_MODES) * len(gbm.STOP_VARIANTS) * len(gbm.COST_MULTIPLIERS)
    if len(payload["observations"]) != expected_strategy:
        raise RuntimeError(f"strategy path count mismatch: {job}")
    if len(payload["benchmarks"]) != len(gbm.COST_MULTIPLIERS):
        raise RuntimeError(f"benchmark path count mismatch: {job}")
    if payload["path_key"] != {
        "scenario_id": job["scenario_id"],
        "beta": job["beta"],
        "path_id": job["path_id"],
    }:
        raise RuntimeError(f"path identity mismatch: {job}")
    if not 0 <= job["path_id"] < path_count:
        raise RuntimeError(f"path id outside frozen range: {job}")


def _run_one(job: dict[str, Any], path_count: int) -> dict[str, Any]:
    spec = gbm.SyntheticPathSpec(
        scenario_id=job["scenario_id"],
        beta=job["beta"],
        path_id=job["path_id"],
        master_seed=job["master_seed"],
    )
    payload = gbm.run_synthetic_path(
        spec,
        asset_count=gbm.ASSET_COUNT,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=gbm.EVALUATION_DAYS,
        max_positions=gbm.MAX_POSITIONS,
        max_hold_days=gbm.MAX_HOLD_DAYS,
        top_rank=gbm.TOP_RANK,
        industry_cap=gbm.INDUSTRY_CAP,
    )
    _validate_path_payload(job, payload, path_count)
    return payload


def run_paths(path_count: int, workers: int) -> dict[str, Any]:
    jobs = _jobs(path_count)
    started = time.perf_counter()
    observations: list[dict[str, Any]] = []
    benchmarks: list[dict[str, Any]] = []
    realized: list[dict[str, Any]] = []
    completed = 0
    if workers <= 1:
        for job in jobs:
            payload = _run_one(job, path_count)
            observations.extend(payload["observations"])
            benchmarks.extend(payload["benchmarks"])
            realized.append(payload["realized"])
            completed += 1
            if completed % 20 == 0 or completed == len(jobs):
                print(f"completed {completed}/{len(jobs)} paths", flush=True)
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as executor:
            futures = {
                executor.submit(_run_one, job, path_count): job
                for job in jobs
            }
            for future in as_completed(futures):
                payload = future.result()
                observations.extend(payload["observations"])
                benchmarks.extend(payload["benchmarks"])
                realized.append(payload["realized"])
                completed += 1
                if completed % 40 == 0 or completed == len(jobs):
                    print(f"completed {completed}/{len(jobs)} paths", flush=True)
    elapsed = time.perf_counter() - started
    return {
        "observations": observations,
        "benchmarks": benchmarks,
        "realized": realized,
        "seconds": elapsed,
        "workers": workers,
    }


def _benchmark_controls(benchmarks: list[dict[str, Any]], path_count: int) -> dict[str, Any]:
    expected = (
        len(gbm.SCENARIOS)
        * len(gbm.CORRELATIONS)
        * len(gbm.COST_MULTIPLIERS)
        * path_count
    )
    keys = {
        (
            str(row["scenario_id"]),
            float(row["beta"]),
            int(row["path_id"]),
            int(row["cost_multiplier"]),
        )
        for row in benchmarks
    }
    if len(benchmarks) != expected or len(keys) != expected:
        raise RuntimeError(
            f"benchmark coverage mismatch: expected {expected}, got {len(benchmarks)}/{len(keys)}"
        )
    exposures = [float(row["max_exposure"]) for row in benchmarks]
    return {
        "observations": len(benchmarks),
        "unique_keys": len(keys),
        "min_max_exposure": round(min(exposures), 6),
        "median_max_exposure": round(float(sorted(exposures)[len(exposures) // 2]), 6),
        "max_max_exposure": round(max(exposures), 6),
    }


def _deterministic_replay(job: dict[str, Any], path_count: int) -> bool:
    first = _run_one(job, path_count)
    replay = _run_one(job, path_count)
    return first == replay


def build_result_payload(
    run: dict[str, Any],
    *,
    path_count: int,
    replay_ok: bool,
) -> dict[str, Any]:
    aggregate = gbm.aggregate_observations(
        run["observations"],
        expected_paths=path_count,
    )
    benchmark = _benchmark_controls(run["benchmarks"], path_count)
    realized = gbm.realize_path_market_metrics(run["realized"])
    design = scientific_design()
    strategy_max_exposure = max(
        float(row["strategy_max_exposure"]) for row in run["observations"]
    )
    gate_results = {
        "all_cells_completed": aggregate["observed_cell_count"] == design["expected_cells"],
        "paths_per_cell_exact": aggregate["min_paths_per_cell"] == path_count,
        "strategy_observation_count": (
            aggregate["observed_observations"]
            == design["expected_strategy_observations"]
        ),
        "benchmark_observation_count": (
            benchmark["observations"] == design["expected_benchmark_observations"]
        ),
        "benchmark_exposure_above_95pct": benchmark["max_max_exposure"] >= 0.95,
        "no_leverage": design["execution"]["leverage"] == "none",
        "max_exposure_at_or_below_100pct": strategy_max_exposure <= 1.0 + 1e-6,
        "five_x_warning_only": design["execution"]["five_x_policy"] == "warning_only",
        "deterministic_replay": replay_ok,
        "complete": False,
    }
    gate_results["complete"] = all(
        value for key, value in gate_results.items() if key != "complete"
    )
    if not gate_results["complete"]:
        raise RuntimeError(f"synthetic stress gates failed: {gate_results}")

    ranked = sorted(
        aggregate["cells"],
        key=lambda row: (
            float(row["excess_return"]["p50"]),
            float(row["strategy"]["sharpe"]["p50"]),
        ),
        reverse=True,
    )
    diagnostic_rank = [
        {
            "rank": index + 1,
            "scenario_id": row["scenario_id"],
            "beta": row["beta"],
            "signal_mode": row["signal_mode"],
            "stop_variant": row["stop_variant"],
            "cost_multiplier": row["cost_multiplier"],
            "median_excess_return": row["excess_return"]["p50"],
            "excess_positive_rate": row["excess_return"]["positive_rate"],
            "sign_test_p": row["excess_return"]["sign_test_p"],
            "median_sharpe": row["strategy"]["sharpe"]["p50"],
            "median_max_drawdown": row["strategy"]["max_drawdown"]["p50"],
        }
        for index, row in enumerate(ranked[:12])
    ]
    worst = [
        {
            "scenario_id": row["scenario_id"],
            "beta": row["beta"],
            "signal_mode": row["signal_mode"],
            "stop_variant": row["stop_variant"],
            "cost_multiplier": row["cost_multiplier"],
            "median_excess_return": row["excess_return"]["p50"],
            "excess_positive_rate": row["excess_return"]["positive_rate"],
            "sign_test_p": row["excess_return"]["sign_test_p"],
        }
        for row in reversed(ranked[-12:])
    ]
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "design": design,
        "run": {
            "path_count": path_count,
            "workers": run["workers"],
            "elapsed_seconds": round(float(run["seconds"]), 3),
            "deterministic_replay": replay_ok,
        },
        "benchmark_controls": benchmark,
        "realized_markets": realized,
        "aggregate": aggregate,
        "top_diagnostic_cells": diagnostic_rank,
        "worst_diagnostic_cells": worst,
        "gate_results": gate_results,
        "conclusion": "diagnostic_only",
    }


def _pct(value: float) -> str:
    return f"{float(value):.2%}"


def _cell_rows(cells: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 情景 | β | 信号 | 止损 | 成本 | 路径 | 策略收益 p05/p50/p95 | 等权 p50 | 超额 p50 | 胜率 | sign p | MDD p50 | 换手 p50 |",
        "| --- | ---: | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in cells:
        strategy = row["strategy"]
        benchmark = row["benchmark"]
        excess = row["excess_return"]
        lines.append(
            "| {scenario} | {beta:.1f} | {signal} | {stop} | {cost}× | {paths} | "
            "{sp05}/{sp50}/{sp95} | {bp50} | {xp50} | {win} | {p} | {mdd} | {turnover} |".format(
                scenario=row["scenario_id"],
                beta=row["beta"],
                signal=row["signal_mode"],
                stop=row["stop_variant"],
                cost=row["cost_multiplier"],
                paths=row["paths"],
                sp05=_pct(strategy["total_return"]["p05"]),
                sp50=_pct(strategy["total_return"]["p50"]),
                sp95=_pct(strategy["total_return"]["p95"]),
                bp50=_pct(benchmark["total_return"]["p50"]),
                xp50=_pct(excess["p50"]),
                win=_pct(excess["positive_rate"]),
                p=excess["sign_test_p"],
                mdd=_pct(strategy["max_drawdown"]["p50"]),
                turnover=f"{strategy['turnover']['p50']:.2f}",
            )
        )
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    design = payload["design"]
    aggregate = payload["aggregate"]
    lines = [
        "# R-GBM-001 综合 GBM 市场压力测试",
        "",
        "> 结论：`diagnostic_only`。这不是 HS300 真实历史回测，不能用于策略准入、订单、纸面目标或生产启用。",
        "",
        "## 研究范围",
        "",
        "- 模型：固定漂移/波动的几何布朗运动。",
        f"- 路径：{design['path_count']} 条/情景；{design['evaluation_days']} 个评价交易日，另有 {design['warmup_days']} 日 warmup。",
        f"- 股票：{design['asset_count']} 只，{design['industry_count']} 个合成行业。",
        "- 相关性：独立个股 `β=0` 与共同市场 `β=0.7`。",
        "- 信号：随机空对照、20/60 日动量、5 日反转、20 日低波、动量+低波。",
        "- 执行：项目矩阵策略 + 项目 `BacktestEngine`，T+1 开盘成交，10 仓、60 日、行业上限 3、无杠杆、最大敞口 100%。",
        "- 止损：-12% 与不止损。",
        "- 成本：2× 主诊断、3× 压力、5× warning-only。",
        "- 对照：同路径等权买入持有，同成本、同评价窗口。",
        "",
        "## 完成门禁",
        "",
        f"- 预期策略路径观测：{design['expected_strategy_observations']:,}；实际：{aggregate['observed_observations']:,}。",
        f"- 预期 cell：{design['expected_cells']}；实际：{aggregate['observed_cell_count']}。",
        f"- 每个 cell 路径数：{aggregate['min_paths_per_cell']}。",
        f"- 同路径等权观测：{payload['benchmark_controls']['observations']:,}。",
        f"- 等权最大敞口中位数：{_pct(payload['benchmark_controls']['median_max_exposure'])}。",
        f"- 确定性重放：{'通过' if payload['run']['deterministic_replay'] else '失败'}。",
        f"- 成本三档中位数完全单调的 cell 比例：{aggregate['fully_monotonic_cost_cell_rate']:.2%}。",
        "",
        "## 生成行情实现统计",
        "",
        "| 情景 | β | 路径 | 等权年化收益 p05/p50/p95 | 等权年化波动 p05/p50/p95 |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for row in payload["realized_markets"]:
        ret = row["equal_weight_annual_return"]
        vol = row["equal_weight_annual_volatility"]
        lines.append(
            f"| {row['scenario_id']} | {row['beta']:.1f} | {row['paths']} | "
            f"{_pct(ret['p05'])}/{_pct(ret['p50'])}/{_pct(ret['p95'])} | "
            f"{_pct(vol['p05'])}/{_pct(vol['p50'])}/{_pct(vol['p95'])} |"
        )
    lines += [
        "",
        "## 信号维度汇总",
        "",
        "> 这里把不同情景、相关性、止损和成本混合在一起，只作描述统计，不作因果或准入结论。",
        "",
        "| 信号 | 观测 | 超额 mean | 超额 p05/p50/p95 | 正超额比例 | sign p |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for row in aggregate["summary_by_signal"]:
        excess = row["excess_return"]
        lines.append(
            f"| {row['signal_mode']} | {row['observations']} | {_pct(excess['mean'])} | "
            f"{_pct(excess['p05'])}/{_pct(excess['p50'])}/{_pct(excess['p95'])} | "
            f"{_pct(excess['positive_rate'])} | {excess['sign_test_p']} |"
        )
    lines += [
        "",
        "## 情景 / 相关性 / 成本边际汇总",
        "",
        "| 维度 | 观测 | 超额 mean | 超额 p05/p50/p95 | 正超额比例 |",
        "| --- | ---: | ---: | --- | ---: |",
    ]
    for label, rows, field in (
        ("情景", aggregate["summary_by_scenario"], "scenario_id"),
        ("β", aggregate["summary_by_beta"], "beta"),
        ("成本", aggregate["summary_by_cost"], "cost_multiplier"),
    ):
        for row in rows:
            excess = row["excess_return"]
            value = row[field]
            rendered = f"{value:.1f}" if field == "beta" else str(value)
            lines.append(
                f"| {label}:{rendered} | {row['observations']} | {_pct(excess['mean'])} | "
                f"{_pct(excess['p05'])}/{_pct(excess['p50'])}/{_pct(excess['p95'])} | "
                f"{_pct(excess['positive_rate'])} |"
            )
    lines += [
        "",
        "## 诊断排序（不是推荐）",
        "",
        "| # | 情景 | β | 信号 | 止损 | 成本 | 超额 p50 | 胜率 | sign p | Sharpe p50 | MDD p50 |",
        "| ---: | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["top_diagnostic_cells"]:
        lines.append(
            f"| {row['rank']} | {row['scenario_id']} | {row['beta']:.1f} | {row['signal_mode']} | "
            f"{row['stop_variant']} | {row['cost_multiplier']}× | {_pct(row['median_excess_return'])} | "
            f"{_pct(row['excess_positive_rate'])} | {row['sign_test_p']} | {row['median_sharpe']:.2f} | "
            f"{_pct(row['median_max_drawdown'])} |"
        )
    lines += [
        "",
        "## 全部 288 个诊断 cell",
        "",
        *_cell_rows(aggregate["cells"]),
        "",
        "## 已知边界",
        "",
        "- GBM 不含肥尾、跳空聚集、波动率聚集、流动性冲击、停牌、涨跌停、财报、情绪、行业或宏观字段。",
        "- 随机空对照、动量、反转和低波只验证执行与价格类信号在受控世界中的行为，不代表真实 13 因子 alpha。",
        "- 同路径等权使用项目引擎和同一成本档位；大资金保证一手的粒度偏差可忽略。",
        "- 5× 成本仅作预警诊断，不是硬否决，也不是加杠杆依据。",
        "- 所有结果固定为 `diagnostic_only`；active version、订单、纸面目标和生产启用全部关闭。",
    ]
    return "\n".join(lines) + "\n"


def publish_result(payload: dict[str, Any]) -> dict[str, Any]:
    prereg_path = PREREG
    prereg_hash = verify_sidecar(prereg_path)
    preregistered = json.loads(prereg_path.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "runner": RUNNER,
        "runner_sha256": preregistered["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "conclusion": "diagnostic_only",
        "gate_results": payload["gate_results"],
        "metrics": {
            "design": payload["design"],
            "run": payload["run"],
            "benchmark_controls": payload["benchmark_controls"],
            "realized_markets": payload["realized_markets"],
            "aggregate": payload["aggregate"],
            "top_diagnostic_cells": payload["top_diagnostic_cells"],
            "worst_diagnostic_cells": payload["worst_diagnostic_cells"],
        },
        "source_result_hashes": [],
        "formal_evidence": None,
        "known_limits": [
            "GBM synthetic paths do not establish real historical or live performance.",
            "Financial, sentiment, industry, macro, and factor-library alpha are absent by construction.",
            "The six price-score modes are synthetic stress probes, not the real 13-factor portfolio.",
            "The equal-weight benchmark is a same-path passive control with the same cost profile.",
            "Five-times cost is warning-only and does not hard-reject a no-leverage strategy.",
        ],
        "negative_knowledge": [
            "A synthetic winner cannot be promoted into a real strategy.",
            "A random-null deviation reflects execution, turnover, selection mechanics, or path noise, not proven alpha.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r_gbm_001_comprehensive_market_stress_published",
    )
    return published.__dict__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--freeze-preregistration", action="store_true")
    parser.add_argument("--smoke-paths", type=int, default=0)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.freeze_preregistration:
        if args.smoke_paths:
            parser.error("--freeze-preregistration cannot be combined with --smoke-paths")
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.smoke_paths:
        if not 1 <= args.smoke_paths <= PATH_COUNT:
            parser.error(f"--smoke-paths must be between 1 and {PATH_COUNT}")
        run = run_paths(args.smoke_paths, args.workers)
        aggregate = gbm.aggregate_observations(
            run["observations"],
            expected_paths=args.smoke_paths,
        )
        print(json.dumps({
            "smoke_paths": args.smoke_paths,
            "workers": args.workers,
            "cells": aggregate["observed_cell_count"],
            "observations": aggregate["observed_observations"],
            "summary_by_signal": aggregate["summary_by_signal"],
            "summary_by_cost": aggregate["summary_by_cost"],
            "elapsed_seconds": round(float(run["seconds"]), 3),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R-GBM-001 results already exist; refusing overwrite")
    run = run_paths(PATH_COUNT, args.workers)
    replay_job = _jobs(PATH_COUNT)[0]
    replay_ok = _deterministic_replay(replay_job, PATH_COUNT)
    payload = build_result_payload(
        run,
        path_count=PATH_COUNT,
        replay_ok=replay_ok,
    )
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "cells": payload["aggregate"]["observed_cell_count"],
        "strategy_observations": payload["aggregate"]["observed_observations"],
        "benchmark_observations": payload["benchmark_controls"]["observations"],
        "fully_monotonic_cost_cell_rate": payload["aggregate"]["fully_monotonic_cost_cell_rate"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
