#!/usr/bin/env python3
"""R404: paired moving-block Monte Carlo for frozen core+HS300 blends."""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r404_bootstrap_preregistration.json"
OUTPUT = E / "r404_bootstrap_2026-11-22.json"
RESULT = E / "r404_bootstrap_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha256(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R404 preregistration checksum mismatch")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R404":
        raise RuntimeError("R404 round mismatch")
    return payload


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(fraction * len(ordered))))]


def paired_block_bootstrap(
    returns: list[float],
    *,
    block_days: int,
    horizon: int,
    replications: int,
    seed: int,
) -> dict[str, Any]:
    if not returns or block_days <= 0 or horizon <= 0 or replications <= 0:
        raise ValueError("bootstrap dimensions must be positive")
    rng = random.Random(seed)
    n = len(returns)
    blocks = math.ceil(horizon / block_days)
    totals: list[float] = []
    sharpes: list[float] = []
    mdds: list[float] = []
    for _ in range(replications):
        sampled: list[float] = []
        for _ in range(blocks):
            start = rng.randrange(n)
            sampled.extend(returns[(start + offset) % n] for offset in range(block_days))
        path = sampled[:horizon]
        growth = 1.0
        curve = [1.0]
        for value in path:
            growth *= 1.0 + value
            curve.append(growth)
        mean = sum(path) / len(path)
        variance = sum((value - mean) ** 2 for value in path) / len(path)
        std = math.sqrt(variance)
        peak = curve[0]
        mdd = 0.0
        for value in curve:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        totals.append(growth - 1.0)
        sharpes.append(mean / std * math.sqrt(252.0) if std > 1e-14 else 0.0)
        mdds.append(mdd)
    return {
        "replications": replications,
        "block_days": block_days,
        "horizon_days": horizon,
        "seed": seed,
        "total_p05": quantile(totals, 0.05),
        "total_p50": quantile(totals, 0.50),
        "total_p95": quantile(totals, 0.95),
        "sharpe_p05": quantile(sharpes, 0.05),
        "sharpe_p50": quantile(sharpes, 0.50),
        "sharpe_p95": quantile(sharpes, 0.95),
        "mdd_p05": quantile(mdds, 0.05),
        "mdd_p50": quantile(mdds, 0.50),
        "mdd_p95": quantile(mdds, 0.95),
        "probability_positive": sum(value > 0.0 for value in totals) / len(totals),
    }


def paired_excess_bootstrap(
    strategy_returns: list[float],
    benchmark_returns: list[float],
    *,
    block_days: int,
    replications: int,
    seed: int,
) -> dict[str, Any]:
    if len(strategy_returns) != len(benchmark_returns) or not strategy_returns:
        raise ValueError("paired return series must have equal non-zero length")
    rng = random.Random(seed)
    n = len(strategy_returns)
    blocks = math.ceil(n / block_days)
    excess_totals: list[float] = []
    for _ in range(replications):
        indices: list[int] = []
        for _ in range(blocks):
            start = rng.randrange(n)
            indices.extend((start + offset) % n for offset in range(block_days))
        indices = indices[:n]
        strategy_growth = 1.0
        benchmark_growth = 1.0
        for index in indices:
            strategy_growth *= 1.0 + strategy_returns[index]
            benchmark_growth *= 1.0 + benchmark_returns[index]
        excess_totals.append(strategy_growth - benchmark_growth)
    return {
        "replications": replications,
        "block_days": block_days,
        "horizon_days": n,
        "seed": seed,
        "excess_p05": quantile(excess_totals, 0.05),
        "excess_p50": quantile(excess_totals, 0.50),
        "excess_p95": quantile(excess_totals, 0.95),
        "probability_positive": sum(value > 0.0 for value in excess_totals) / len(excess_totals),
    }


def _returns(curve: list[dict[str, Any]]) -> list[float]:
    values = [float(item["value"]) for item in curve]
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def _paired_excess(strategy: list[dict[str, Any]], benchmark: list[dict[str, Any]]) -> list[float]:
    if len(strategy) != len(benchmark):
        raise ValueError("R404 strategy and benchmark curves must have equal length")
    strategy_returns = _returns(strategy)
    benchmark_returns = _returns(benchmark)
    return [left - right for left, right in zip(strategy_returns, benchmark_returns, strict=True)]


def main() -> int:
    prereg = verify_prereg()
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R404 output exists: {path.name}")
    curve_source = E / "r403v2_core_beta_2026-11-22.json"
    source = json.loads(curve_source.read_text(encoding="utf-8"))
    design = prereg["bootstrap"]
    results = []
    for core_weight in prereg["weight_grid"]["core"]:
        curves = source["curves"][str(core_weight)]
        row = {"core_weight": core_weight, "beta_weight": round(1.0 - core_weight, 10), "windows": {}}
        for boundary in ("train", "validation", "test", "recent"):
            strategy_curve = curves[boundary]
            strategy_returns = _returns(strategy_curve)
            # The benchmark is the same 000300.SH series used in R403. Re-fetch
            # it for the exact boundary and align it to the strategy dates.
            from datetime import date

            from app.tickflow.repository import DataStore, KlineRepository
            repo = KlineRepository(DataStore(ROOT / "data"))
            start = prereg["windows"][f"{boundary}_start"]
            end = prereg["windows"][f"{boundary}_end"]
            benchmark = repo.get_index_daily(prereg["beta"]["symbol"], date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
            align = {str(item["date"])[:10]: float(item["close"]) for item in benchmark.to_dicts()}
            dates = [str(item["date"])[:10] for item in strategy_curve]
            missing = [day for day in dates if day not in align]
            if missing:
                raise RuntimeError(f"R404 benchmark missing {missing[0]}")
            benchmark_curve = [{"date": day, "value": align[day]} for day in dates]
            strategy_bootstrap = paired_block_bootstrap(strategy_returns, block_days=int(design["block_days"]), horizon=len(strategy_returns), replications=int(design["replications"]), seed=int(design["seed"]))
            benchmark_returns = _returns(benchmark_curve)
            excess_bootstrap = paired_excess_bootstrap(strategy_returns, benchmark_returns, block_days=int(design["block_days"]), replications=int(design["replications"]), seed=int(design["seed"]) + 1)
            row["windows"][boundary] = {"strategy": strategy_bootstrap, "excess": excess_bootstrap, "point_total_return": float(strategy_curve[-1]["value"]) / float(strategy_curve[0]["value"]) - 1.0, "benchmark_total_return": float(benchmark_curve[-1]["value"]) / float(benchmark_curve[0]["value"]) - 1.0}
        row["pass_2x"] = bool(row["windows"]["test"]["strategy"]["total_p05"] > 0 and row["windows"]["test"]["excess"]["excess_p05"] > 0 and row["windows"]["recent"]["strategy"]["total_p05"] > 0 and row["windows"]["recent"]["excess"]["excess_p05"] > 0 and row["windows"]["test"]["strategy"]["sharpe_p50"] >= 0.8 and row["windows"]["recent"]["strategy"]["sharpe_p50"] >= 0.5)
        results.append(row)
        print(json.dumps({"core_weight": core_weight, "test": row["windows"]["test"], "recent": row["windows"]["recent"], "pass": row["pass_2x"]}), flush=True)
    payload = {"round": "R404", "classification": prereg["classification"], "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG), "source_sha256": sha256(curve_source), "rows": results, "decision": {"status": "bootstrap_mc_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R404 配对 moving-block Monte Carlo", "", "2× 股票成本；80/20 为主候选；每档 5000 次配对块重采样，block=20 日。", "", "| 核心 | 盲测收益 p05 | 盲测超额 p05 | 盲测 Sharpe p50 | 最近一年收益 p05 | 最近一年超额 p05 | 最近 Sharpe p50 | 通过 |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |"]
    for row in results:
        test = row["windows"]["test"]
        recent = row["windows"]["recent"]
        lines.append(f"| {row['core_weight']:.0%} | {test['strategy']['total_p05']:.2%} | {test['excess']['excess_p05']:.2%} | {test['strategy']['sharpe_p50']:.3f} | {recent['strategy']['total_p05']:.2%} | {recent['excess']['excess_p05']:.2%} | {recent['strategy']['sharpe_p50']:.3f} | {'是' if row['pass_2x'] else '否'} |")
    lines += ["", "## 边界", "", "- Bootstrap 只重采样历史路径，不创造新市场状态。", "- 指数收益不是可交易 ETF 净值，未覆盖跟踪误差。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
