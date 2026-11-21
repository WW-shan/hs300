#!/usr/bin/env python3
"""R420: compact four-factor vs current eight-factor tail audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r420_final_candidate_tail_preregistration.json"
OUTPUT = E / "r420_final_candidate_tail_2026-11-22.json"
RESULT = E / "r420_final_candidate_tail_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r420_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(p5: Any, service: Any, config_spec: dict[str, Any], directions: dict[str, str], *, start: str, end: str, cost: str) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(config_spec["members"], directions)
    config = p5.portfolio._backtest_config(
        scoring,
        selected,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(config_spec["max_positions"]),
        cost=p5.portfolio._cost_parameters(cost),
        max_hold_days=int(config_spec["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": dict(result.stats),
        "equity_curve": list(result.equity_curve),
        "trades": len(result.trades),
    }


def returns(curve: list[dict[str, Any]]) -> list[float]:
    values = [float(item["value"]) for item in curve]
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def path_metrics(rets: list[float]) -> dict[str, float]:
    if not rets:
        return {"total_return": 0.0, "annual": 0.0, "sharpe": 0.0, "mdd": 0.0}
    value = 1.0
    curve = [1.0]
    for ret in rets:
        value *= 1.0 + ret
        curve.append(value)
    total = value - 1.0
    mean = sum(rets) / len(rets)
    vol = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5
    peak = curve[0]
    mdd = 0.0
    for item in curve:
        peak = max(peak, item)
        mdd = min(mdd, item / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0,
        "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
    }


def bootstrap(rets: list[float], *, block: int, paths: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(rets):
            start = rng.randrange(len(rets))
            sampled.extend(rets[(start + offset) % len(rets)] for offset in range(block))
        metrics = path_metrics(sampled[: len(rets)])
        totals.append(metrics["total_return"])
        sharpes.append(metrics["sharpe"])
        mdds.append(metrics["mdd"])
    def q(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {
        "total_p05": q(totals, 0.05),
        "total_p50": q(totals, 0.5),
        "sharpe_p50": q(sharpes, 0.5),
        "mdd_p05": q(mdds, 0.05),
        "mdd_p50": q(mdds, 0.5),
        "prob_positive": sum(x > 0 for x in totals) / paths,
    }


def monthly(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import OrderedDict
    months: OrderedDict[str, list[float]] = OrderedDict()
    for item in curve:
        months.setdefault(str(item["date"])[:7], []).append(float(item["value"]))
    output = []
    previous = None
    for month, values in months.items():
        if previous is None:
            output.append({"month": month, "return": 0.0})
        else:
            output.append({"month": month, "return": values[-1] / previous - 1.0})
        previous = values[-1]
    return output


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R420 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R420 preregistration checksum mismatch")
    p5 = load_p5()
    service = p5.portfolio._service()
    directions = {member: "high" for spec in prereg["candidates"].values() for member in spec["members"]}
    windows = prereg["windows"]
    results: dict[str, dict[str, Any]] = {}
    for name, spec in prereg["candidates"].items():
        results[name] = {
            "config": spec,
            "train_2x": run(p5, service, spec, directions, start=windows["train_start"], end=windows["train_end"], cost="2x"),
            "validation_2x": run(p5, service, spec, directions, start=windows["validation_start"], end=windows["validation_end"], cost="2x"),
            "test_2x": run(p5, service, spec, directions, start=windows["test_start"], end=windows["test_end"], cost="2x"),
            "test_3x": run(p5, service, spec, directions, start=windows["test_start"], end=windows["test_end"], cost="3x"),
            "recent_2x": run(p5, service, spec, directions, start=windows["recent_start"], end=windows["test_end"], cost="2x"),
        }
        for label in ("train_2x", "validation_2x", "test_2x", "test_3x", "recent_2x"):
            rets = returns(results[name][label]["equity_curve"])
            results[name][label]["returns"] = rets
            results[name][label]["monthly"] = monthly(results[name][label]["equity_curve"])
        results[name]["bootstrap"] = {
            label: bootstrap(
                results[name][label]["returns"],
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seed"]),
            )
            for label in ("train_2x", "validation_2x", "test_2x", "recent_2x")
        }
    compact = results["compact_4f"]
    current = results["current_8f"]
    checks = {
        "compact_test_total_p05_positive": compact["bootstrap"]["test_2x"]["total_p05"] > 0,
        "compact_recent_total_p05_positive": compact["bootstrap"]["recent_2x"]["total_p05"] > 0,
        "compact_test_mdd_p05_not_worse": compact["bootstrap"]["test_2x"]["mdd_p05"] >= current["bootstrap"]["test_2x"]["mdd_p05"],
        "compact_recent_sharpe_p50_min": compact["bootstrap"]["recent_2x"]["sharpe_p50"] >= prereg["gates"]["compact_recent_sharpe_p50_min"],
    }
    payload = {
        "round": "R420",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "candidates": results,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R420 四因子 vs 八因子尾部审计",
        "",
        "| 候选 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 | 近一年年化 | 检验p05 | 近一年p05 | 检验MDD p05 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in results.items():
        stats = item["test_2x"]["stats"]
        recent = item["recent_2x"]["stats"]
        lines.append(f"| {name} | {item['train_2x']['stats']['annual_return']:.2%} | {item['validation_2x']['stats']['annual_return']:.2%} | {stats['annual_return']:.2%} | {stats['sharpe']:.3f} | {stats['max_drawdown']:.2%} | {item['test_3x']['stats']['annual_return']:.2%} | {recent['annual_return']:.2%} | {item['bootstrap']['test_2x']['total_p05']:.2%} | {item['bootstrap']['recent_2x']['total_p05']:.2%} | {item['bootstrap']['test_2x']['mdd_p05']:.2%} |")
    lines += ["", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 近一年月度（四因子）", "", "| 月份 | 收益 |", "| --- | ---: |"]
    lines += [f"| {item['month']} | {item['return']:.2%} |" for item in compact["recent_2x"]["monthly"]]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
