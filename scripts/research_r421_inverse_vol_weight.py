#!/usr/bin/env python3
"""R421: training-estimated inverse-volatility factor weights vs equal weight."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r421_inverse_vol_weight_preregistration.json"
OUTPUT = E / "r421_inverse_vol_weight_2026-11-22.json"
RESULT = E / "r421_inverse_vol_weight_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r421_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def factor_weights(panel: pl.DataFrame, members: list[str], prereg: dict[str, Any]) -> dict[str, float]:
    from app.factor_research.incremental import rank_score_frame
    start = date.fromisoformat(prereg["windows"]["train_start"])
    end = date.fromisoformat(prereg["windows"]["train_end"])
    train = panel.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    vols: dict[str, float] = {}
    for factor in members:
        scored = rank_score_frame(train, factor, "high", output_column=f"_r421_{factor}")
        # The risk proxy is the average daily cross-sectional dispersion of
        # the factor score, not the volatility of the cross-sectional mean.
        daily = scored.group_by("date").agg(pl.col(f"_r421_{factor}").std().alias("daily_std")).sort("date")
        values = daily["daily_std"].drop_nulls().to_numpy()
        values = values[np.isfinite(values)]
        vols[factor] = float(np.mean(values)) if values.size else 1.0
    raw = {factor: 1.0 / max(vol, 1e-9) for factor, vol in vols.items()}
    total = sum(raw.values())
    weights = {factor: value / total for factor, value in raw.items()}
    minimum = float(prereg["weight_rule"]["minimum_weight"])
    maximum = float(prereg["weight_rule"]["maximum_weight"])
    # Clip and renormalize until the maximum constraint is respected.
    for _ in range(20):
        clipped = {factor: min(max(value, minimum), maximum) for factor, value in weights.items()}
        total = sum(clipped.values())
        weights = {factor: value / total for factor, value in clipped.items()}
        if max(weights.values()) <= maximum + 1e-12 and min(weights.values()) >= minimum - 1e-12:
            break
    return {factor: round(value, 10) for factor, value in weights.items()}


def run(p5: Any, service: Any, members: list[str], weights: dict[str, float], *, start: str, end: str, cost: str) -> dict[str, Any]:
    directions = {member: "high" for member in members}
    config = p5.portfolio._backtest_config(
        weights, directions,
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=20, cost=p5.portfolio._cost_parameters(cost), max_hold_days=30,
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r421_inverse_vol_weight.py")
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": dict(result.stats), "equity_curve": list(result.equity_curve), "trades": len(result.trades)}


def rets(curve: list[dict[str, Any]]) -> list[float]:
    values = [float(item["value"]) for item in curve]
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def path_metrics(values: list[float]) -> dict[str, float]:
    value = 1.0
    curve = [1.0]
    for ret in values:
        value *= 1.0 + ret
        curve.append(value)
    mean = sum(values) / len(values)
    vol = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5
    peak = curve[0]
    mdd = 0.0
    for item in curve:
        peak = max(peak, item)
        mdd = min(mdd, item / peak - 1.0)
    return {"total_return": value - 1.0, "annual": (value ** (252.0 / len(values)) - 1.0) if values else 0.0, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def bootstrap(values: list[float], *, block: int, paths: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        metrics = path_metrics(sampled[: len(values)])
        totals.append(metrics["total_return"])
        sharpes.append(metrics["sharpe"])
        mdds.append(metrics["mdd"])
    def q(items: list[float], fraction: float) -> float:
        ordered = sorted(items)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {"total_p05": q(totals, 0.05), "total_p50": q(totals, 0.5), "sharpe_p50": q(sharpes, 0.5), "mdd_p05": q(mdds, 0.05), "mdd_p50": q(mdds, 0.5)}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R421 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R421 preregistration checksum mismatch")
    p5 = load_p5()
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    members = prereg["members"]
    missing = [member for member in members if member not in panel.columns]
    if missing:
        raise RuntimeError(f"R421 missing members: {missing}")
    inverse = factor_weights(panel, members, prereg)
    equal = {member: 1.0 / len(members) for member in members}
    service = p5.portfolio._service()
    windows = prereg["windows"]
    results: dict[str, dict[str, Any]] = {}
    for label, weights in (("equal", equal), ("inverse_vol", inverse)):
        results[label] = {"weights": weights}
        for window, start in (("train", windows["train_start"]), ("validation", windows["validation_start"]), ("test", windows["test_start"]), ("recent", windows["recent_start"])):
            end = windows["test_end"]
            results[label][f"{window}_2x"] = run(p5, service, members, weights, start=start, end=end, cost="2x")
        results[label]["test_3x"] = run(p5, service, members, weights, start=windows["test_start"], end=windows["test_end"], cost="3x")
        for window in ("train", "validation", "test", "recent"):
            values = rets(results[label][f"{window}_2x"]["equity_curve"])
            results[label][f"{window}_bootstrap"] = bootstrap(values, block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]))
    inv = results["inverse_vol"]
    eq = results["equal"]
    max_weight_delta = max(abs(inv["weights"][member] - eq["weights"][member]) for member in members)
    checks = {
        "weight_variation_exists": max_weight_delta >= 0.005,
        "test_total_p05_not_worse": inv["test_bootstrap"]["total_p05"] >= eq["test_bootstrap"]["total_p05"],
        "recent_total_p05_not_worse": inv["recent_bootstrap"]["total_p05"] >= eq["recent_bootstrap"]["total_p05"],
        "test_sharpe_p50_not_worse": inv["test_bootstrap"]["sharpe_p50"] >= eq["test_bootstrap"]["sharpe_p50"],
        "test_mdd_p05_not_worse": inv["test_bootstrap"]["mdd_p05"] >= eq["test_bootstrap"]["mdd_p05"],
    }
    payload = {
        "round": "R421",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "weights": {"equal": equal, "inverse_vol": inverse},
        "results": results,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {**prereg["output_policy"], "status": "invalid_no_weight_variation" if max_weight_delta < 0.005 else prereg["output_policy"]["status"]},
        "max_weight_delta": max_weight_delta,
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R421 逆波动权重尾部审计",
        "",
        "| 权重 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 | 检验MDD p05 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label in ("equal", "inverse_vol"):
        item = results[label]
        lines.append(f"| {label} | {item['train_2x']['stats']['annual_return']:.2%} | {item['validation_2x']['stats']['annual_return']:.2%} | {item['test_2x']['stats']['annual_return']:.2%} | {item['test_2x']['stats']['sharpe']:.3f} | {item['test_2x']['stats']['max_drawdown']:.2%} | {item['recent_2x']['stats']['annual_return']:.2%} | {item['test_bootstrap']['total_p05']:.2%} | {item['recent_bootstrap']['total_p05']:.2%} | {item['test_bootstrap']['mdd_p05']:.2%} |")
    lines += ["", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 逆波动权重", ""] + [f"- {factor}: {weight:.2%}" for factor, weight in inverse.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
