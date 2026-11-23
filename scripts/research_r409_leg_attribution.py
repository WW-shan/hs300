#!/usr/bin/env python3
"""R409: leave-one-out and single-leg attribution in the drawdown window."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
SOURCE = E / "r398_strict_mc_2026-11-22.json"
PREREG = E / "r409_leg_attribution_preregistration.json"
OUTPUT = E / "r409_leg_attribution_2026-11-22.json"
RESULT = E / "r409_leg_attribution_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r409_portfolio", path)
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
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0 if rets else 0.0,
        "sharpe": st.mean(rets) / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R409 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R409 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidate = next(row for row in source["validation_stable_candidates"] if row["id"] == 4)
    members = candidate["members"]
    variants = [("all", members)]
    variants.extend((f"drop_{factor.split('_')[-2]}", [item for item in members if item != factor]) for factor in members)
    variants.extend((f"single_{factor.split('_')[-2]}", [factor]) for factor in members)
    module = portfolio_module()
    service = module._service()
    rows = []
    for label, selected in variants:
        weights = {factor: 1.0 / len(selected) for factor in selected}
        config = module._backtest_config(
            weights,
            {factor: "high" for factor in selected},
            start=date.fromisoformat(prereg["windows"]["start"]),
            end=date.fromisoformat(prereg["windows"]["end"]),
            max_positions=10,
            cost=module._cost_parameters("2x"),
            max_hold_days=40,
        )
        result = service.run_with_evidence(config)
        require_formal_evidence(result, label="research_r409_leg_attribution.py")
        if result.error:
            raise RuntimeError(result.error)
        rows.append({"label": label, "members": selected, "metrics": metrics(result.equity_curve), "trades": len(result.trades)})
        print(label, result.stats.get("total_return"), result.stats.get("max_drawdown"), len(result.trades), flush=True)
    payload = {
        "round": "R409", "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG), "source_sha256": sha256(SOURCE),
        "rows": rows,
        "decision": {"status": "leg_attribution_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R409 2026 回撤腿部归因", "", "| 变体 | 总收益 | Sharpe | MDD | 交易数 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['label']} | {row['metrics']['total_return']:.2%} | {row['metrics']['sharpe']:.3f} | {row['metrics']['mdd']:.2%} | {row['trades']} |")
    lines += ["", "## 边界", "", "- Leave-one-out 只改变组合归一化，不是因果归因。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
