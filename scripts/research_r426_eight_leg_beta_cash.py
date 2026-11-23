#!/usr/bin/env python3
"""R426: eight-leg strategy plus index and cash sleeves, tail audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r426_eight_leg_beta_cash_preregistration.json"
OUTPUT = E / "r426_eight_leg_beta_cash_2026-11-22.json"
RESULT = E / "r426_eight_leg_beta_cash_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r426_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_strategy(p5: Any, service: Any, members: list[str], *, start: str, end: str) -> dict[str, Any]:
    directions = {member: "high" for member in members}
    scoring, selected = p5._equal_inputs(members, directions)
    config = p5.portfolio._backtest_config(
        scoring, selected,
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=int(prereg["source_strategy"]["max_positions"]),
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=int(prereg["source_strategy"]["max_hold_days"]),
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r426_eight_leg_beta_cash.py")
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": dict(result.stats), "equity_curve": list(result.equity_curve)}


def index_curve(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        raise RuntimeError("index curve unavailable")
    return [{"date": str(row["date"]), "value": float(row["close"])} for row in frame.iter_rows(named=True)]


def daily_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(curve[i]["date"])[:10]: float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0
        for i in range(1, len(curve))
    }


def blend(strategy: list[dict[str, Any]], index: list[dict[str, Any]], beta_weight: float, cash_weight: float) -> list[dict[str, Any]]:
    sr = daily_returns(strategy)
    ir = daily_returns(index)
    dates = sorted(set(sr) & set(ir))
    strategy_weight = 1.0 - beta_weight - cash_weight
    if strategy_weight < 0:
        raise ValueError("sleeve weights exceed 100%")
    curve = []
    value = 1.0
    previous_month = None
    for day in dates:
        month = day[:7]
        turnover_cost = 0.0005 * abs(beta_weight) if previous_month is not None and month != previous_month else 0.0
        value *= 1.0 + strategy_weight * sr[day] + beta_weight * ir[day] - turnover_cost
        previous_month = month
        curve.append({"date": day, "value": value})
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


prereg = json.loads(PREREG.read_text(encoding="utf-8"))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R426 output exists: {path.name}")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R426 preregistration checksum mismatch")
    p5 = load_p5()
    service = p5.portfolio._service()
    members = prereg["source_strategy"]["members"]
    windows = prereg["windows"]
    strategy_curves = {
        "train": run_strategy(p5, service, members, start=windows["train_start"], end=windows["train_end"])["equity_curve"],
        "validation": run_strategy(p5, service, members, start=windows["validation_start"], end=windows["validation_end"])["equity_curve"],
        "test": run_strategy(p5, service, members, start=windows["test_start"], end=windows["test_end"])["equity_curve"],
        "recent": run_strategy(p5, service, members, start=windows["recent_start"], end=windows["test_end"])["equity_curve"],
    }
    index_curves = {
        "train": index_curve(prereg["benchmark"]["symbol"], windows["train_start"], windows["train_end"]),
        "validation": index_curve(prereg["benchmark"]["symbol"], windows["validation_start"], windows["validation_end"]),
        "test": index_curve(prereg["benchmark"]["symbol"], windows["test_start"], windows["test_end"]),
        "recent": index_curve(prereg["benchmark"]["symbol"], windows["recent_start"], windows["test_end"]),
    }
    rows = []
    for beta_weight in prereg["grid"]["beta_weight"]:
        for cash_weight in prereg["grid"]["cash_weight"]:
            row = {"beta_weight": beta_weight, "cash_weight": cash_weight, "strategy_weight": 1.0 - beta_weight - cash_weight}
            for window in ("train", "validation", "test", "recent"):
                curve = blend(strategy_curves[window], index_curves[window], beta_weight, cash_weight)
                row[f"{window}_curve"] = curve
                row[f"{window}_metrics"] = path_metrics([float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))])
                row[f"{window}_bootstrap"] = bootstrap(curve, block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + len(rows))
            rows.append(row)
    eligible = [row for row in rows if row["test_bootstrap"]["total_p05"] > 0 and row["recent_bootstrap"]["total_p05"] > 0 and row["test_bootstrap"]["sharpe_p50"] >= prereg["gates"]["test_sharpe_p50_min"] and row["recent_bootstrap"]["sharpe_p50"] >= prereg["gates"]["recent_sharpe_p50_min"]]
    eligible.sort(key=lambda row: (min(row["test_bootstrap"]["total_p05"], row["recent_bootstrap"]["total_p05"]), row["test_bootstrap"]["sharpe_p50"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: (row["test_bootstrap"]["total_p05"], row["recent_bootstrap"]["total_p05"]))
    payload = {
        "round": "R426",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "grid": [{k: v for k, v in row.items() if not k.endswith("_curve")} for row in rows],
        "selected": {k: v for k, v in selected.items() if not k.endswith("_curve")},
        "eligible_count": len(eligible),
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R426 八腿策略 + 指数/现金底仓尾部审计",
        "",
        "| 指数 | 现金 | 策略 | 检验年化 | 检验Sharpe | 检验MDD | 检验p05 | 近一年p05 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(f"| {row['beta_weight']:.0%} | {row['cash_weight']:.0%} | {row['strategy_weight']:.0%} | {row['test_metrics']['annual']:.2%} | {row['test_metrics']['sharpe']:.3f} | {row['test_metrics']['mdd']:.2%} | {row['test_bootstrap']['total_p05']:.2%} | {row['recent_bootstrap']['total_p05']:.2%} |")
    lines += ["", f"- 合格组合：{len(eligible)}", f"- 选中：指数 {selected['beta_weight']:.0%} / 现金 {selected['cash_weight']:.0%} / 策略 {selected['strategy_weight']:.0%}", f"- 判定：**{'通过' if eligible else '拒绝'}**", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
