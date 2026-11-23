#!/usr/bin/env python3
"""R407: diagnostic industry concentration cap on frozen core portfolios."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r407_industry_cap_preregistration.json"
OUTPUT = E / "r407_industry_cap_2026-11-22.json"
RESULT = E / "r407_industry_cap_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def industry_map() -> dict[str, str]:
    frame = pl.read_parquet(DATA / "ext_data" / "ext_hy_ths" / "part.parquet")
    return {str(row["symbol"]): str(row["所属同花顺行业"]).split("-")[0] for row in frame.select(["symbol", "所属同花顺行业"]).to_dicts()}


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r407_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(module: Any, service: Any, members: list[str], industry: dict[str, str], cap: int | None, *, start: str, end: str) -> dict[str, Any]:
    # The engine has no selection-time industry cap, so first run the frozen
    # core and inspect its trades. R407 is a diagnostic of the existing path,
    # not a production portfolio constructor.
    weights = {factor: 1.0 / len(members) for factor in members}
    config = module._backtest_config(weights, {factor: "high" for factor in weights}, start=date.fromisoformat(start), end=date.fromisoformat(end), max_positions=10, cost=module._cost_parameters("2x"), max_hold_days=40)
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r407_industry_cap.py")
    if result.error:
        raise RuntimeError(result.error)
    trades = result.trades
    counts = {}
    for trade in trades:
        label = industry.get(str(trade["symbol"]), "unknown")
        counts[label] = counts.get(label, 0) + 1
    return {"stats": result.stats, "max_industry_count": max(counts.values()) if counts else 0, "industry_counts": counts, "trade_count": len(trades), "cap": cap}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R407 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R407 preregistration checksum mismatch")
    module = portfolio_module()
    service = module._service()
    industry = industry_map()
    members = prereg["candidate"]["core_members"]
    reports = {}
    for boundary in ("test", "recent"):
        reports[boundary] = evaluate(module, service, members, industry, None, start=prereg["windows"][f"{boundary}_start"], end=prereg["windows"][f"{boundary}_end"])
    payload = {"round": "R407", "classification": prereg["classification"], "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG), "reports": reports, "decision": {"status": "industry_cap_diagnostic_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False, "reason": "The frozen execution path has no selection-time industry cap; this report only measures concentration. A future constructor must implement the cap explicitly and validate it on historical industry vintages."}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R407 行业集中度诊断", "", "| 窗口 | 总收益 | MDD | 最大同行业持仓/笔数 | 行业分布 |", "| --- | ---: | ---: | ---: | --- |"]
    for boundary, row in reports.items():
        lines.append(f"| {boundary} | {row['stats'].get('total_return',0):.2%} | {row['stats'].get('max_drawdown',0):.2%} | {row['max_industry_count']} | {row['industry_counts']} |")
    lines += ["", "## 结论", "", "- 冻结执行路径不能在选股时施加行业上限；本报告只量化集中度。", "- 下一步若做行业上限，必须实现选股约束并在历史行业 vintage 上复验。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
