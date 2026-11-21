#!/usr/bin/env python3
"""R444: non-price risk-budget overlays on the four-factor dual-admitted portfolio."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r444_dual_admitted_risk_budget_preregistration.json"
OUTPUT = E / "r444_dual_admitted_risk_budget_2026-11-22.json"
RESULT = E / "r444_dual_admitted_risk_budget_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r444_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def strategy_curve(p5: Any, service: Any, prereg: dict[str, Any], *, start: str, end: str) -> list[dict[str, Any]]:
    scoring, selected = p5._equal_inputs(prereg["members"], prereg["directions"])
    config = p5.portfolio._backtest_config(
        scoring, selected, start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=int(prereg["base_execution"]["max_positions"]),
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=int(prereg["base_execution"]["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    return list(result.equity_curve)


def index_curve(start: str, end: str) -> list[dict[str, Any]]:
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily("000300.SH", date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        raise RuntimeError("index curve unavailable")
    return [{"date": str(row["date"]), "value": float(row["close"])} for row in frame.iter_rows(named=True)]


def daily_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    return {str(curve[i]["date"])[:10]: float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))}


def apply_overlay(strategy: list[dict[str, Any]], index: list[dict[str, Any]], overlay: dict[str, Any]) -> list[dict[str, Any]]:
    sr = daily_returns(strategy)
    ir = daily_returns(index)
    dates = sorted(set(sr) & set(ir))
    curve = []
    value = 1.0
    strategy_returns = []
    kind = overlay["type"]
    for day in dates:
        if kind == "none":
            weight = 1.0
            index_weight = 0.0
        elif kind == "fixed_cash":
            weight = 1.0 - float(overlay["cash_weight"])
            index_weight = 0.0
        elif kind == "index_cash":
            index_weight = float(overlay["index_weight"])
            weight = 1.0 - index_weight - float(overlay["cash_weight"])
        elif kind == "vol_target":
            lookback = int(overlay["lookback_days"])
            recent = strategy_returns[-lookback:]
            realized = math.sqrt(sum((item - sum(recent) / len(recent)) ** 2 for item in recent) / len(recent)) * math.sqrt(252.0) if len(recent) > 1 else 0.0
            weight = min(float(overlay["max_exposure"]), float(overlay["target_annual_vol"]) / realized) if realized > 0 else 1.0
            weight = max(0.0, weight)
            index_weight = 0.0
        else:
            raise ValueError(f"unsupported overlay: {kind}")
        ret = weight * sr[day] + index_weight * ir[day]
        value *= 1.0 + ret
        curve.append({"date": day, "value": value})
        strategy_returns.append(sr[day])
    return curve


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
            raise RuntimeError(f"R444 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R444 preregistration checksum mismatch")
    p5 = load_p5()
    service = p5.portfolio._service()
    windows = prereg["windows"]
    base_curves = {
        "train": strategy_curve(p5, service, prereg, start=windows["train_start"], end=windows["train_end"]),
        "validation": strategy_curve(p5, service, prereg, start=windows["validation_start"], end=windows["validation_end"]),
        "test": strategy_curve(p5, service, prereg, start=windows["test_start"], end=windows["test_end"]),
        "recent": strategy_curve(p5, service, prereg, start=windows["recent_start"], end=windows["test_end"]),
    }
    index_curves = {
        "train": index_curve(windows["train_start"], windows["train_end"]),
        "validation": index_curve(windows["validation_start"], windows["validation_end"]),
        "test": index_curve(windows["test_start"], windows["test_end"]),
        "recent": index_curve(windows["recent_start"], windows["test_end"]),
    }
    rows = []
    for overlay in prereg["overlays"]:
        row = {"name": overlay["name"], "overlay": overlay}
        for window in ("train", "validation"):
            curve = apply_overlay(base_curves[window], index_curves[window], overlay)
            row[f"{window}_metrics"] = path_metrics([float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))])
        rows.append(row)
    base = next(row for row in rows if row["name"] == "baseline")
    eligible = [row for row in rows if row["name"] != "baseline" and row["train_metrics"]["annual"] > base["train_metrics"]["annual"] and row["validation_metrics"]["annual"] > base["validation_metrics"]["annual"]]
    eligible.sort(key=lambda row: (min(row["train_metrics"]["annual"], row["validation_metrics"]["annual"]), row["name"]), reverse=True)
    selected_name = eligible[0]["name"] if eligible else "baseline"
    selected = next(row for row in rows if row["name"] == selected_name)
    selected["test_curve"] = apply_overlay(base_curves["test"], index_curves["test"], selected["overlay"])
    selected["recent_curve"] = apply_overlay(base_curves["recent"], index_curves["recent"], selected["overlay"])
    selected["test_metrics"] = path_metrics([float(selected["test_curve"][i]["value"]) / float(selected["test_curve"][i - 1]["value"]) - 1.0 for i in range(1, len(selected["test_curve"]))])
    selected["recent_metrics"] = path_metrics([float(selected["recent_curve"][i]["value"]) / float(selected["recent_curve"][i - 1]["value"]) - 1.0 for i in range(1, len(selected["recent_curve"]))])
    selected["test_bootstrap"] = bootstrap(selected["test_curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]))
    selected["recent_bootstrap"] = bootstrap(selected["recent_curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 1)
    gates = prereg["gates"]
    checks = {"selected_not_baseline": selected_name != "baseline", "train_annual_min": selected["train_metrics"]["annual"] >= gates["train_annual_min"], "validation_annual_min": selected["validation_metrics"]["annual"] >= gates["validation_annual_min"], "test_annual_min": selected["test_metrics"]["annual"] >= gates["test_annual_min"], "test_sharpe_min": selected["test_metrics"]["sharpe"] >= gates["test_sharpe_min"], "test_mdd_min": selected["test_metrics"]["mdd"] >= gates["test_mdd_min"], "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0, "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0}
    payload = {"round": "R444", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "selected_overlay": selected_name, "overlays": rows, "selected": selected, "gate": {"pass": all(checks.values()), "checks": checks}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R444 四因子非价格风险预算", "", "| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        if "test_metrics" not in row:
            lines.append(f"| {row['name']} | {row['train_metrics']['annual']:.2%} | {row['validation_metrics']['annual']:.2%} | - | - | - | - | - | - |")
        else:
            lines.append(f"| {row['name']} | {row['train_metrics']['annual']:.2%} | {row['validation_metrics']['annual']:.2%} | {row['test_metrics']['annual']:.2%} | {row['test_metrics']['sharpe']:.3f} | {row['test_metrics']['mdd']:.2%} | {row['recent_metrics']['annual']:.2%} | {row['test_bootstrap']['total_p05']:.2%} | {row['recent_bootstrap']['total_p05']:.2%} |")
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
