#!/usr/bin/env python3
"""R427: lagged-regime dynamic weights vs static equal weights."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r427_dynamic_regime_weights_preregistration.json"
OUTPUT = E / "r427_dynamic_regime_weights_2026-11-22.json"
RESULT = E / "r427_dynamic_regime_weights_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r427_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def regime_map() -> dict[str, str]:
    frame = pl.read_parquet(DATA / "regime_history" / "part.parquet").sort("date")
    frame = frame.with_columns(pl.col("state").shift(1).alias("state_lag"))
    return {
        str(row["date"]): str(row["state_lag"])
        for row in frame.iter_rows(named=True)
        if row["state_lag"] is not None
    }


def trading_dates() -> list[str]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return [path.parent.name.removeprefix("date=") for path in paths]


def state_weights(panel: pl.DataFrame, members: list[str], states: list[str], *, start: str, end: str) -> dict[str, dict[str, float]]:
    from app.factor_research.incremental import rank_score_frame
    from app.factor_research.signal_scan import calculate_rank_ic
    train = panel.filter((pl.col("date") >= date.fromisoformat(start)) & (pl.col("date") <= date.fromisoformat(end)))
    smap = regime_map()
    train = train.with_columns(pl.col("date").cast(pl.String).replace_strict(smap, default=None).alias("_state"))
    result: dict[str, dict[str, float]] = {}
    for state in states:
        subset = train.filter(pl.col("_state") == state)
        scores: dict[str, float] = {}
        for member in members:
            if subset.is_empty():
                scores[member] = 0.0
                continue
            scored = rank_score_frame(subset, member, "high", output_column="_rank")
            ic = calculate_rank_ic(scored, "_rank", "forward_return_5d", minimum=30)
            scores[member] = float(ic["ic"].mean()) if ic.height else 0.0
        raw = {member: max(0.0, score) for member, score in scores.items()}
        total = sum(raw.values())
        if total <= 0:
            weights = {member: 1.0 / len(members) for member in members}
        else:
            weights = {member: value / total for member, value in raw.items()}
        minimum = 0.02
        maximum = 0.30
        for _ in range(20):
            weights = {member: min(max(value, minimum), maximum) for member, value in weights.items()}
            total = sum(weights.values())
            weights = {member: value / total for member, value in weights.items()}
            if max(weights.values()) <= maximum + 1e-12 and min(weights.values()) >= minimum - 1e-12:
                break
        result[state] = {member: round(value, 10) for member, value in weights.items()}
    return result


def dynamic_scoring_for_window(states: list[str], weights: dict[str, dict[str, float]], start: str, end: str) -> dict[str, dict[str, Any]]:
    smap = regime_map()
    output = {}
    known_dates = sorted(smap)
    last_state = None
    known_index = 0
    for day in trading_dates():
        while known_index < len(known_dates) and known_dates[known_index] <= day:
            state = smap[known_dates[known_index]]
            if state in states:
                last_state = state
            known_index += 1
        if last_state in states:
            output[day] = {"scoring": weights[last_state], "directions": {member: "high" for member in weights[last_state]}}
    return output

def run(p5: Any, service: Any, members: list[str], *, start: str, end: str, dynamic: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    directions = {member: "high" for member in members}
    if dynamic is None:
        scoring, selected = p5._equal_inputs(members, directions)
        params = {"scoring": scoring, "directions": selected}
    else:
        params = {"dynamic_scoring": dynamic}
    config = p5.portfolio._backtest_config(
        {members[0]: 1.0} if dynamic is not None else p5._equal_inputs(members, directions)[0],
        directions,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=20,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=30,
    )
    if dynamic is not None:
        config.params = params
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
            raise RuntimeError(f"R427 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R427 preregistration checksum mismatch")
    p5 = load_p5()
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    members = prereg["members"]
    weights_by_state = state_weights(panel, members, prereg["states"], start=prereg["windows"]["train_start"], end=prereg["windows"]["train_end"])
    service = p5.portfolio._service()
    windows = prereg["windows"]
    static = {"train_2x": run(p5, service, members, start=windows["train_start"], end=windows["train_end"], dynamic=None), "validation_2x": run(p5, service, members, start=windows["validation_start"], end=windows["validation_end"], dynamic=None), "test_2x": run(p5, service, members, start=windows["test_start"], end=windows["test_end"], dynamic=None), "recent_2x": run(p5, service, members, start=windows["recent_start"], end=windows["test_end"], dynamic=None)}
    dynamic = {}
    for window, start in (("train", windows["train_start"]), ("validation", windows["validation_start"]), ("test", windows["test_start"]), ("recent", windows["recent_start"])):
        mapping = dynamic_scoring_for_window(prereg["states"], weights_by_state, start, windows["test_end"])
        dynamic[f"{window}_2x"] = run(p5, service, members, start=start, end=windows["test_end"] if window == "recent" else windows[f"{window}_end"], dynamic=mapping)
    variation = max(abs(weights_by_state[state][member] - 1.0 / len(members)) for state in prereg["states"] for member in members)
    checks = {
        "weight_variation_exists": variation >= 0.005,
        "test_total_p05_not_worse": bootstrap(dynamic["test_2x"]["equity_curve"], block=20, paths=5000, seed=427)["total_p05"] >= bootstrap(static["test_2x"]["equity_curve"], block=20, paths=5000, seed=427)["total_p05"],
        "recent_total_p05_not_worse": bootstrap(dynamic["recent_2x"]["equity_curve"], block=20, paths=5000, seed=428)["total_p05"] >= bootstrap(static["recent_2x"]["equity_curve"], block=20, paths=5000, seed=428)["total_p05"],
    }
    payload = {"round": "R427", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "weights": weights_by_state, "static": static, "dynamic": dynamic, "max_weight_variation": variation, "gate": {"pass": all(checks.values()), "checks": checks}, "decision": {**prereg["output_policy"], "status": "dynamic_regime_complete_no_promotion" if all(checks.values()) else "dynamic_regime_rejected"}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R427 状态动态权重审计", "", "| 版本 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for label, item in (("static", static), ("dynamic", dynamic)):
        lines.append(f"| {label} | {item['train_2x']['stats']['annual_return']:.2%} | {item['validation_2x']['stats']['annual_return']:.2%} | {item['test_2x']['stats']['annual_return']:.2%} | {item['test_2x']['stats']['sharpe']:.3f} | {item['test_2x']['stats']['max_drawdown']:.2%} | {item['recent_2x']['stats']['annual_return']:.2%} |")
    lines += ["", f"- 最大权重偏离等权：{variation:.2%}", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 状态权重", ""]
    for state, state_weight_map in weights_by_state.items():
        lines.append(f"- {state}: " + ", ".join(f"{member}={weight:.1%}" for member, weight in state_weight_map.items()))
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
