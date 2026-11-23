#!/usr/bin/env python3
"""R410: rerun frozen candidate 4 with historical HS300 membership."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
SOURCE = E / "r398_strict_mc_2026-11-22.json"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
PREREG = E / "r410_historical_membership_preregistration.json"
OUTPUT = E / "r410_historical_membership_2026-11-22.json"
RESULT = E / "r410_historical_membership_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def membership_frame() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP)
        .select([
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
    from app.backtest import strategy as strategy_module
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r410_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    rets = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(rets)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {"total_return": total, "annual": (1 + total) ** (252 / len(rets)) - 1 if rets else 0.0, "sharpe": st.mean(rets) / vol * (252 ** 0.5) if vol else 0.0, "mdd": mdd}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R410 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R410 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidate = next(row for row in source["validation_stable_candidates"] if row["id"] == 4)
    members = candidate["members"]
    weights = {factor: 1.0 / len(members) for factor in members}
    module = portfolio_module()
    service = module._service()
    membership = membership_frame()
    reports = {}
    with patched_membership(membership):
        for boundary in ("test", "recent"):
            config = module._backtest_config(
                weights,
                {factor: "high" for factor in members},
                start=date.fromisoformat(prereg["windows"][f"{boundary}_start"]),
                end=date.fromisoformat(prereg["windows"][f"{boundary}_end"]),
                max_positions=10,
                cost=module._cost_parameters("2x"),
                max_hold_days=40,
            )
            result = service.run_with_evidence(config)
            require_formal_evidence(result, label="research_r410_historical_membership.py")
            if result.error:
                raise RuntimeError(result.error)
            reports[boundary] = {
                "metrics": metrics(result.equity_curve),
                "trade_count": len(result.trades),
                "membership_sha256": sha256(MEMBERSHIP),
            }
            print(boundary, result.stats.get("total_return"), result.stats.get("max_drawdown"), len(result.trades), flush=True)
    payload = {"round": "R410", "classification": prereg["classification"], "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG), "source_sha256": sha256(SOURCE), "membership_sha256": sha256(MEMBERSHIP), "reports": reports, "decision": {"status": "historical_membership_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R410 历史 HS300 成分验证", "", "| 窗口 | 总收益 | Sharpe | MDD | 交易数 |", "| --- | ---: | ---: | ---: | ---: |"]
    for boundary, row in reports.items():
        lines.append(f"| {boundary} | {row['metrics']['total_return']:.2%} | {row['metrics']['sharpe']:.3f} | {row['metrics']['mdd']:.2%} | {row['trade_count']} |")
    lines += ["", "## 边界", "", "- 历史成分来自 r78 daily snapshots；仍存在公告时点/回填口径限制。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
