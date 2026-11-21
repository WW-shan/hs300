#!/usr/bin/env python3
"""R403V2: corrected core+HS300 blend with summary/curve parity checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r403_core_beta_preregistration.json"
OUTPUT = E / "r403v2_core_beta_2026-11-22.json"
RESULT = E / "r403v2_core_beta_2026-11-22.md"
BOUNDARIES = ("train", "validation", "test", "recent")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portfolio_module() -> Any:
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r403v2_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index_curve(symbol: str, start: str, end: str) -> pl.DataFrame:
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        raise RuntimeError("index curve unavailable")
    return frame.with_columns((pl.col("close") / pl.col("close").first() * 1_000_000.0).alias("value")).select(["date", "value"])


def returns(curve: list[dict[str, Any]]) -> list[float]:
    values = [float(item["value"]) for item in curve]
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    rets = returns(curve)
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(rets)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0 if rets else 0.0,
        "sharpe": st.mean(rets) / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
    }


def compose(core: pl.DataFrame, beta: pl.DataFrame, core_weight: float) -> list[dict[str, Any]]:
    joined = (
        core.select([pl.col("date").cast(pl.Date).alias("date"), pl.col("value").alias("core")])
        .join(beta.select([pl.col("date").cast(pl.Date).alias("date"), pl.col("value").alias("beta")]), on="date", how="inner")
        .sort("date")
    )
    core_rows = [{"date": str(item["date"]), "value": float(item["core"])} for item in joined.to_dicts()]
    beta_rows = [{"date": str(item["date"]), "value": float(item["beta"])} for item in joined.to_dicts()]
    core_returns = returns(core_rows)
    beta_returns = returns(beta_rows)
    output = [{"date": core_rows[0]["date"], "value": 1.0}]
    value = 1.0
    for index in range(1, len(core_rows)):
        value *= 1.0 + core_weight * core_returns[index - 1] + (1.0 - core_weight) * beta_returns[index - 1]
        value *= 1.0 - 0.0015 / 252.0
        if index + 1 == len(core_rows) or core_rows[index + 1]["date"][:7] != core_rows[index]["date"][:7]:
            value *= 0.9998
        output.append({"date": core_rows[index]["date"], "value": value})
    return output


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R403V2 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    module = portfolio_module()
    service = module._service()
    core = prereg["core_candidate"]
    scoring = {factor: 1.0 / len(core["members"]) for factor in core["members"]}
    curves: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for boundary in BOUNDARIES:
        start = prereg["windows"][f"{boundary}_start"]
        end = prereg["windows"][f"{boundary}_end"]
        config = module._backtest_config(
            scoring, {factor: "high" for factor in scoring},
            start=date.fromisoformat(start), end=date.fromisoformat(end),
            max_positions=int(core["max_positions"]), cost=module._cost_parameters("2x"),
            max_hold_days=int(core["max_hold_days"]),
        )
        result = service.run(config)
        if result.error:
            raise RuntimeError(result.error)
        core_curve = pl.DataFrame(result.equity_curve).with_columns(pl.col("date").cast(pl.Date))
        beta_curve = index_curve(prereg["beta"]["symbol"], start, end)
        for weight in prereg["weight_grid"]["core"]:
            curves.setdefault(str(weight), {})[boundary] = compose(core_curve, beta_curve, float(weight))
    rows = []
    for weight in prereg["weight_grid"]["core"]:
        row = {"core_weight": weight, "beta_weight": round(1.0 - weight, 10)}
        for boundary in BOUNDARIES:
            row[f"{boundary}_2x"] = metrics(curves[str(weight)][boundary])
        row["pass"] = bool(row["test_2x"]["total_return"] > 0 and row["recent_2x"]["total_return"] > 0)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    # Hard parity guard: different weights must produce different curves.
    if curves["0.7"]["test"] == curves["0.8"]["test"] or curves["0.8"]["test"] == curves["0.9"]["test"]:
        raise RuntimeError("R403V2 weights produced duplicate curves")
    payload = {
        "round": "R403V2", "classification": "corrected core+HS300 blend with curve parity",
        "preregistration_sha256": sha256(PREREG), "rows": rows, "curves": curves,
        "decision": {"status": "corrected_blend_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R403V2 修正版核心+HS300", "", "| 核心 | 盲测总收益 | 最近一年总收益 | 盲测Sharpe | 最近Sharpe |", "| ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['core_weight']:.0%} | {row['test_2x']['total_return']:.2%} | {row['recent_2x']['total_return']:.2%} | {row['test_2x']['sharpe']:.3f} | {row['recent_2x']['sharpe']:.3f} |")
    lines += ["", "## 修正说明", "", "- 汇总和导出使用同一组曲线，并强制检查不同权重曲线不相同。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
