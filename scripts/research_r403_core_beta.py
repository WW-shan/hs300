#!/usr/bin/env python3
"""R403: static core plus HS300 beta completion."""
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
OUTPUT = E / "r403_core_beta_2026-11-22.json"
RESULT = E / "r403_core_beta_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, Any]:
    side = PREREG.with_name(PREREG.name + ".sha256")
    if not side.exists() or sha(PREREG) != side.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R403 preregistration checksum mismatch")
    return json.loads(PREREG.read_text(encoding="utf-8"))


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r403_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index_curve(symbol: str, start: str, end: str) -> pl.DataFrame:
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        raise RuntimeError("R403 index curve unavailable")
    return frame.with_columns((pl.col("close") / pl.col("close").first() * 1_000_000.0).alias("value")).select(["date", "value"])


def curve_metrics(curve: pl.DataFrame, start: str, end: str) -> dict[str, Any]:
    part = curve.filter((pl.col("date") >= pl.lit(start).str.strptime(pl.Date, "%Y-%m-%d")) & (pl.col("date") <= pl.lit(end).str.strptime(pl.Date, "%Y-%m-%d"))).sort("date")
    values = part["value"].to_list()
    if len(values) < 2:
        return {"annual": None, "sharpe": None, "mdd": None, "total_return": None, "trades": 0}
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {"annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0, "sharpe": st.mean(returns) / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd, "total_return": total, "trades": len(part)}


def blend(core: pl.DataFrame, beta: pl.DataFrame, *, core_weight: float, management_fee: float, rebalance_cost: float) -> pl.DataFrame:
    joined = core.rename({"value": "core"}).join(beta.rename({"value": "beta"}), on="date", how="inner").sort("date")
    dates = joined["date"].to_list()
    cv = joined["core"].to_list()
    bv = joined["beta"].to_list()
    cr = [0.0, *[cv[i] / cv[i - 1] - 1.0 for i in range(1, len(cv))]]
    br = [0.0, *[bv[i] / bv[i - 1] - 1.0 for i in range(1, len(bv))]]
    value = 1.0
    rows = [{"date": dates[0], "value": value}]
    for i in range(1, len(dates)):
        value *= 1.0 + core_weight * cr[i] + (1.0 - core_weight) * br[i]
        # Index tracking / management fee accrues daily; monthly rebalancing
        # between the sleeves pays a small turnover cost.
        value *= 1.0 - management_fee / 252.0
        if i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (dates[i].year, dates[i].month):
            value *= 1.0 - rebalance_cost
        rows.append({"date": dates[i], "value": value})
    return pl.DataFrame(rows)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R403 output exists: {path.name}")
    prereg = verify()
    module = portfolio_module()
    service = module._service()
    core = prereg["core_candidate"]
    weights = {factor: 1.0 / len(core["members"]) for factor in core["members"]}
    curves = {}
    for boundary in ("train", "validation", "test", "recent"):
        start = prereg["windows"][f"{boundary}_start"]
        end = prereg["windows"][f"{boundary}_end"]
        config = module._backtest_config(
            weights, {factor: "high" for factor in weights},
            start=date.fromisoformat(start), end=date.fromisoformat(end),
            max_positions=int(core["max_positions"]), cost=module._cost_parameters("2x"),
            max_hold_days=int(core["max_hold_days"]),
        )
        result = service.run(config)
        if result.error:
            raise RuntimeError(result.error)
        curves[boundary] = (pl.DataFrame(result.equity_curve).with_columns(pl.col("date").cast(pl.Date)), index_curve(prereg["beta"]["symbol"], start, end))
    recent_benchmark_curve = index_curve(prereg["beta"]["symbol"], prereg["windows"]["recent_start"], prereg["windows"]["recent_end"])
    recent_benchmark_return = curve_metrics(recent_benchmark_curve, prereg["windows"]["recent_start"], prereg["windows"]["recent_end"])["total_return"]
    rows = []
    for core_weight in prereg["weight_grid"]["core"]:
        beta_weight = round(1.0 - core_weight, 10)
        mixed = {boundary: blend(core_curve, beta_curve, core_weight=core_weight, management_fee=float(prereg["beta"]["annual_management_assumption"]), rebalance_cost=0.0002) for boundary, (core_curve, beta_curve) in curves.items()}
        row = {"core_weight": core_weight, "beta_weight": beta_weight}
        for boundary, curve in mixed.items():
            row[f"{boundary}_2x"] = curve_metrics(curve, prereg["windows"][f"{boundary}_start"], prereg["windows"][f"{boundary}_end"])
        hs300 = index_curve(prereg["beta"]["symbol"], prereg["windows"]["test_start"], prereg["windows"]["test_end"])
        hs300_return = curve_metrics(hs300, prereg["windows"]["test_start"], prereg["windows"]["test_end"])["total_return"]
        row["hs300_test_return"] = hs300_return
        row["test_excess"] = row["test_2x"]["total_return"] - float(hs300_return or 0.0)
        row["recent_hs300_return"] = recent_benchmark_return
        row["recent_excess"] = row["recent_2x"]["total_return"] - float(recent_benchmark_return or 0.0)
        row["pass"] = bool(row["train_2x"]["annual"] >= 0.08 and row["validation_2x"]["annual"] >= 0.12 and row["test_2x"]["annual"] >= 0.08 and row["test_2x"]["sharpe"] >= 0.8 and row["test_2x"]["mdd"] >= -0.15 and row["test_excess"] >= 0.02)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    payload = {"round": "R403", "classification": prereg["classification"], "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG), "core_candidate": core, "rows": rows, "decision": {"status": "core_beta_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R403 核心 + HS300 Beta 补全", "", "候选 4 冻结，沪深300底仓按静态权重混合；无杠杆。", "", "| 核心 | HS300 | 训练年化 | 验证年化 | 盲测年化 | 最近一年累计 | 盲测Sharpe | 盲测MDD | HS300超额 | 通过 |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |"]
    for row in rows:
        lines.append(f"| {row['core_weight']:.0%} | {row['beta_weight']:.0%} | {row['train_2x']['annual']:.2%} | {row['validation_2x']['annual']:.2%} | {row['test_2x']['annual']:.2%} | {row['recent_2x']['total_return']:.2%} | {row['recent_excess']:+.2%} | {row['test_2x']['sharpe']:.3f} | {row['test_2x']['mdd']:.2%} | {row['test_excess']:+.2%} | {'是' if row['pass'] else '否'} |")
    lines += ["", "## 边界", "", "- 指数收益不是可交易 ETF 净值，未覆盖跟踪误差。", "- 当前 HS300 名单回填历史仍有幸存者偏差。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
