#!/usr/bin/env python3
"""R406: lagged market-state drawdown control for frozen 80/20."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r406_state_drawdown_preregistration.json"
SOURCE = E / "r403v2_core_beta_2026-11-22.json"
OUTPUT = E / "r406_state_drawdown_2026-11-22.json"
RESULT = E / "r406_state_drawdown_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_frame() -> pl.DataFrame:
    frame = pl.read_parquet(DATA / "regime_history" / "part.parquet").sort("date")
    frame = frame.with_columns([
        pl.col("date").shift(1).alias("signal_date"),
        pl.col("state").shift(1).alias("state_lag"),
        pl.col("above_ma20_pct").shift(1).alias("breadth_lag"),
        pl.col("trend_score").shift(1).alias("trend_lag"),
    ])
    return frame.select(["signal_date", "state_lag", "breadth_lag", "trend_lag"]).with_columns(pl.col("signal_date").cast(pl.Date))


def exposure(row: dict[str, Any], rule: str) -> float:
    state = row.get("state_lag")
    breadth = row.get("breadth_lag")
    trend = row.get("trend_lag")
    if rule == "baseline":
        return 1.0
    if rule == "weak_off":
        return 0.5 if state == "weak" else 1.0
    if rule == "weak_or_leanweak_off":
        return 0.5 if state in {"weak", "lean_weak"} else 1.0
    if rule == "breadth_defensive":
        return 0.5 if breadth is not None and float(breadth) < 0.35 else 1.0
    if rule == "trend_defensive":
        return 0.5 if trend is not None and float(trend) < 40.0 else 1.0
    if rule == "combined_defensive":
        weak = state in {"weak", "lean_weak"}
        low_breadth = breadth is not None and float(breadth) < 0.35
        low_trend = trend is not None and float(trend) < 40.0
        return 0.5 if weak or low_breadth or low_trend else 1.0
    raise ValueError(f"unknown R406 rule: {rule}")


def returns(curve: list[dict[str, Any]]) -> list[float]:
    values = [float(item["value"]) for item in curve]
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def apply_rule(curve: list[dict[str, Any]], states: dict[str, dict[str, Any]], rule: str, switch_cost: float) -> list[float]:
    raw = returns(curve)
    dates = [str(item["date"]) for item in curve]
    result = []
    previous = 1.0
    for index, ret in enumerate(raw, start=1):
        signal = states.get(dates[index - 1], {})
        current = exposure(signal, rule)
        cost = 0.0 if current == previous else switch_cost * abs(current - previous)
        result.append(current * ret - cost)
        previous = current
    return result


def path_metrics(rets: list[float]) -> dict[str, float]:
    value = 1.0
    curve = [1.0]
    for ret in rets:
        value *= 1.0 + ret
        curve.append(value)
    total = value - 1.0
    vol = (sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / len(rets)) ** 0.5 if rets else 0.0
    peak = curve[0]
    mdd = 0.0
    for item in curve:
        peak = max(peak, item)
        mdd = min(mdd, item / peak - 1.0)
    return {"total_return": total, "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0 if rets else 0.0, "sharpe": (sum(rets) / len(rets) / vol) * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def bootstrap(rets: list[float], *, block: int, paths: int, seed: int) -> dict[str, float]:
    import random
    rng = random.Random(seed)
    totals = []
    sharpes = []
    mdds = []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(rets):
            start = rng.randrange(len(rets))
            sampled.extend(rets[(start + offset) % len(rets)] for offset in range(block))
        path = sampled[: len(rets)]
        m = path_metrics(path)
        totals.append(m["total_return"])
        sharpes.append(m["sharpe"])
        mdds.append(m["mdd"])
    def q(values: list[float], fraction: float) -> float:
        values = sorted(values)
        return values[min(len(values) - 1, int(fraction * len(values)))]
    return {"total_p05": q(totals, 0.05), "total_p50": q(totals, 0.5), "sharpe_p50": q(sharpes, 0.5), "mdd_p05": q(mdds, 0.05), "mdd_p50": q(mdds, 0.5), "prob_positive": sum(x > 0 for x in totals) / paths}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R406 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R406 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    state_rows = {str(row["signal_date"]): row for row in state_frame().to_dicts()}
    rows = []
    curves = source["curves"]["0.8"]
    for rule in prereg["rules"]:
        row = {"rule": rule}
        for boundary in ("train", "validation", "test", "recent"):
            rets = apply_rule(curves[boundary], state_rows, rule, float(prereg["mode"]["switch_cost"]))
            row[f"{boundary}_point"] = path_metrics(rets)
            row[f"{boundary}_bootstrap"] = bootstrap(rets, block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]))
        row["pass"] = bool(row["test_bootstrap"]["total_p05"] > 0 and row["recent_bootstrap"]["total_p05"] > 0 and row["test_bootstrap"]["sharpe_p50"] >= 0.8 and row["recent_bootstrap"]["sharpe_p50"] >= 0.5)
        rows.append(row)
        print(json.dumps({"rule": rule, "test": row["test_bootstrap"], "recent": row["recent_bootstrap"], "pass": row["pass"]}), flush=True)
    payload = {"round": "R406", "classification": prereg["classification"], "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG), "source_sha256": sha(SOURCE), "rows": rows, "decision": {"status": "state_drawdown_mc_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R406 状态化回撤防守", "", "| 规则 | 盲测点收益 | 盲测p05 | 最近点收益 | 最近p05 | 盲测Sharpe p50 | 最近Sharpe p50 | 通过 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |"]
    for row in rows:
        lines.append(f"| {row['rule']} | {row['test_point']['total_return']:.2%} | {row['test_bootstrap']['total_p05']:.2%} | {row['recent_point']['total_return']:.2%} | {row['recent_bootstrap']['total_p05']:.2%} | {row['test_bootstrap']['sharpe_p50']:.3f} | {row['recent_bootstrap']['sharpe_p50']:.3f} | {'是' if row['pass'] else '否'} |")
    lines += ["", "## 边界", "", "- 规则使用 T-1 状态，但 regime 历史不是完整实时 vintage。", "- 现金收益按 0，切换成本 5bp。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
