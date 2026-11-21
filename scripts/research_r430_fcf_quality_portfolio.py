#!/usr/bin/env python3
"""R430: FCF/revenue and OCF/revenue financial quality portfolio swaps."""
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
PREREG = E / "r430_fcf_quality_portfolio_preregistration.json"
OUTPUT = E / "r430_fcf_quality_portfolio_2026-11-22.json"
RESULT = E / "r430_fcf_quality_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r430_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def variants(prereg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = list(prereg["baseline_members"])
    out = {"baseline": {"members": base, "directions": {m: "high" for m in base}}}
    for item in prereg["variants"]:
        drop = set(item["drop"])
        members = [m for m in base if m not in drop] + list(item["add"])
        if len(members) != 8:
            raise RuntimeError(f"R430 variant must keep eight legs: {item['name']}")
        directions = {m: "high" for m in members}
        directions.update(prereg["directions"])
        out[item["name"]] = {"members": members, "directions": directions}
    return out


def run(p5: Any, service: Any, members: list[str], directions: dict[str, str], *, start: str, end: str, cost: str) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(members, directions)
    config = p5.portfolio._backtest_config(scoring, selected, start=date.fromisoformat(start), end=date.fromisoformat(end), max_positions=20, cost=p5.portfolio._cost_parameters(cost), max_hold_days=30)
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": dict(result.stats), "equity_curve": list(result.equity_curve)}


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
    return {"total_return": value - 1.0, "annual": value ** (252.0 / len(values)) - 1.0 if values else 0.0, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
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
            raise RuntimeError(f"R430 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R430 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, ["fcf_proxy_to_revenue_ttm", "ocf_to_revenue_ttm"])
    service = p5.portfolio._service()
    windows = prereg["windows"]
    specs = variants(prereg)
    results = {}
    for name, spec in specs.items():
        missing = [m for m in spec["members"] if m not in panel.columns]
        if missing:
            raise RuntimeError(f"R430 missing {name}: {missing}")
        results[name] = {"members": spec["members"], "train_2x": run(p5, service, spec["members"], spec["directions"], start=windows["train_start"], end=windows["train_end"], cost="2x"), "validation_2x": run(p5, service, spec["members"], spec["directions"], start=windows["validation_start"], end=windows["validation_end"], cost="2x")}
    base = results["baseline"]
    eligible = [(name, row) for name, row in results.items() if name != "baseline" and row["train_2x"]["stats"]["annual_return"] > base["train_2x"]["stats"]["annual_return"] and row["validation_2x"]["stats"]["annual_return"] > base["validation_2x"]["stats"]["annual_return"]]
    eligible.sort(key=lambda item: (min(item[1]["train_2x"]["stats"]["annual_return"], item[1]["validation_2x"]["stats"]["annual_return"]), item[0]), reverse=True)
    selected_name = eligible[0][0] if eligible else "baseline"
    selected = results[selected_name]
    selected["test_2x"] = run(p5, service, selected["members"], specs[selected_name]["directions"], start=windows["test_start"], end=windows["test_end"], cost="2x")
    selected["test_3x"] = run(p5, service, selected["members"], specs[selected_name]["directions"], start=windows["test_start"], end=windows["test_end"], cost="3x")
    selected["recent_2x"] = run(p5, service, selected["members"], specs[selected_name]["directions"], start=windows["recent_start"], end=windows["test_end"], cost="2x")
    selected["test_bootstrap"] = bootstrap(selected["test_2x"]["equity_curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]))
    selected["recent_bootstrap"] = bootstrap(selected["recent_2x"]["equity_curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 1)
    gates = prereg["gates"]
    checks = {"selected_not_baseline": selected_name != "baseline", "train_annual_min": selected["train_2x"]["stats"]["annual_return"] >= gates["train_annual_min"], "validation_annual_min": selected["validation_2x"]["stats"]["annual_return"] >= gates["validation_annual_min"], "test_annual_min": selected["test_2x"]["stats"]["annual_return"] >= gates["test_annual_min"], "test_sharpe_min": selected["test_2x"]["stats"]["sharpe"] >= gates["test_sharpe_min"], "test_mdd_min": selected["test_2x"]["stats"]["max_drawdown"] >= gates["test_mdd_min"], "beat_baseline_train": selected["train_2x"]["stats"]["annual_return"] > base["train_2x"]["stats"]["annual_return"], "beat_baseline_validation": selected["validation_2x"]["stats"]["annual_return"] > base["validation_2x"]["stats"]["annual_return"], "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0}
    payload = {"round": "R430", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "selected_variant": selected_name, "variants": results, "gate": {"pass": all(checks.values()), "checks": checks}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R430 FCF/OCF 收入质量组合确认", "", "| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in results.items():
        if "test_2x" not in row:
            lines.append(f"| {name} | {row['train_2x']['stats']['annual_return']:.2%} | {row['validation_2x']['stats']['annual_return']:.2%} | - | - | - | - | - | - | - |")
        else:
            lines.append(f"| {name} | {row['train_2x']['stats']['annual_return']:.2%} | {row['validation_2x']['stats']['annual_return']:.2%} | {row['test_2x']['stats']['annual_return']:.2%} | {row['test_2x']['stats']['sharpe']:.3f} | {row['test_2x']['stats']['max_drawdown']:.2%} | {row['recent_2x']['stats']['annual_return']:.2%} | {row['test_3x']['stats']['annual_return']:.2%} | {row['test_bootstrap']['total_p05']:.2%} | {row['recent_bootstrap']['total_p05']:.2%} |")
    lines += ["", f"- 选中：`{selected_name}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
