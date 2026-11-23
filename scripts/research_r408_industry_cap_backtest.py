#!/usr/bin/env python3
"""R408: actual industry-capped stock selection versus baseline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r408_industry_cap_backtest_preregistration.json"
OUTPUT = E / "r408_industry_cap_backtest_2026-11-22.json"
RESULT = E / "r408_industry_cap_backtest_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r408_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def industry_map() -> dict[str, str]:
    frame = pl.read_parquet(DATA / "ext_data" / "ext_hy_ths" / "part.parquet")
    return {
        str(row["symbol"]): str(row["所属同花顺行业"]).split("-")[0]
        for row in frame.select(["symbol", "所属同花顺行业"]).to_dicts()
    }


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
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0 if rets else 0.0,
        "sharpe": st.mean(rets) / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R408 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    module = portfolio_module()
    service = module._service()
    industry = industry_map()
    members = prereg["candidate"]["core_members"]
    weights = {factor: 1.0 / len(members) for factor in members}
    rows = []
    for cap in prereg["rules"]:
        for boundary in ("test", "recent"):
            start = date.fromisoformat(prereg["windows"][f"{boundary}_start"])
            end = date.fromisoformat(prereg["windows"][f"{boundary}_end"])
            config = module._backtest_config(
                weights,
                {factor: "high" for factor in members},
                start=start,
                end=end,
                max_positions=10,
                cost=module._cost_parameters("2x"),
                max_hold_days=40,
                industry_by_symbol=industry if cap > 0 else None,
                max_industry_positions=cap,
            )
            result = service.run_with_evidence(config)
            require_formal_evidence(result, label="research_r408_industry_cap_backtest.py")
            if result.error:
                raise RuntimeError(result.error)
            rows.append({
                "cap": cap,
                "window": boundary,
                "stats": result.stats,
                "metrics": metrics(result.equity_curve),
                "trade_count": len(result.trades),
            })
            print(cap, boundary, result.stats.get("total_return"), result.stats.get("max_drawdown"), len(result.trades), flush=True)
    payload = {
        "round": "R408", "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG), "rows": rows,
        "decision": {"status": "industry_cap_backtest_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R408 行业上限真实回测", "", "| 行业上限 | 窗口 | 总收益 | Sharpe | MDD | 交易数 |", "| ---: | --- | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['cap']} | {row['window']} | {row['metrics']['total_return']:.2%} | {row['metrics']['sharpe']:.3f} | {row['metrics']['mdd']:.2%} | {row['trade_count']} |")
    lines += ["", "## 边界", "", "- 行业映射是当前快照，不是历史 vintage。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
