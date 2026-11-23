#!/usr/bin/env python3
"""R398 finalize: validation stability, one-shot test, benchmark-relative gates."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r398_holdout_preregistration.json"
TRAIN = E / "r398_train_2026-11-22.jsonl"
VALID = E / "r398_validation_2026-11-22.jsonl"
OUTPUT = E / "r398_strict_mc_2026-11-22.json"
RESULT = E / "r398_strict_mc_2026-11-22.md"
SHORT = {
    "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q",
    "ext_alt_hs300_dividend_yield": "dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio",
    "ext_alt_hs300_holder_top10_share_chg_yoy": "holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d",
    "ext_alt_hs300_margin_chg_1m_low": "margin_chg_1m",
    "ext_alt_hs300_ocf_margin_4q": "ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol_20d",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha256(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R398 preregistration checksum mismatch")
    return json.loads(PREREG.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r398_finalize_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(module: Any, service: Any, row: dict[str, Any], *, start: str, end: str, cost: str) -> dict[str, Any]:
    from datetime import date

    config = module._backtest_config(
        row["weights"],
        {factor: "high" for factor in row["members"]},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]),
        cost=module._cost_parameters(cost),
        max_hold_days=int(row["max_hold_days"]),
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r398_finalize.py")
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {
        "annual": stats.get("annual_return"),
        "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"),
        "total_return": stats.get("total_return"),
        "trades": len(result.trades),
        "benchmark_return": module._curve_return(result.benchmark_curve),
    }


def main() -> int:
    prereg = verify_prereg()
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R398 final output exists: {path.name}")
    train = read_jsonl(TRAIN)
    validation = read_jsonl(VALID)
    if len(train) != 1962 or len(validation) != 225:
        raise RuntimeError("R398 input row count changed")
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in validation:
        grouped.setdefault(tuple(row["members"]), []).append(row)
    candidates = []
    for members, rows in grouped.items():
        if len(rows) != 9 or sum(bool(row["pass"]) for row in rows) < 7:
            continue
        best = max(rows, key=lambda row: (row["validation_2x"]["annual"], row["validation_2x"]["sharpe"]))
        train_row = next(
            row for row in train
            if tuple(row["members"]) == members
            and row["max_positions"] == best["max_positions"]
            and row["max_hold_days"] == best["max_hold_days"]
        )
        candidates.append({
            "members": best["members"], "weights": best["weights"],
            "max_positions": best["max_positions"], "max_hold_days": best["max_hold_days"],
            "train_2x": train_row["train_2x"], "validation_2x": best["validation_2x"],
            "validation_platform_pass_count": sum(bool(row["pass"]) for row in rows),
        })
    candidates.sort(key=lambda row: (
        -min(row["train_2x"]["annual"], row["validation_2x"]["annual"]),
        -min(row["train_2x"]["sharpe"], row["validation_2x"]["sharpe"]),
    ))
    module = portfolio_module()
    service = module._service()
    for index, row in enumerate(candidates):
        row["id"] = index
        row["test_2x"] = evaluate(module, service, row, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost="2x")
        row["test_3x"] = evaluate(module, service, row, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost="3x")
        row["benchmark_relative"] = row["test_2x"]["total_return"] - row["test_2x"]["benchmark_return"]
        row["test_gate"] = bool(
            row["test_2x"]["annual"] > 0
            and row["test_2x"]["sharpe"] >= 0.5
            and row["test_2x"]["mdd"] >= -0.15
            and row["test_3x"]["annual"] > 0
        )
        if index % 5 == 0:
            print(json.dumps({"id": index, "members": row["members"], "positions": row["max_positions"], "hold": row["max_hold_days"], "test": row["test_2x"], "pass": row["test_gate"]}), flush=True)
    candidates.sort(key=lambda row: (
        -min(row["train_2x"]["annual"], row["validation_2x"]["annual"], row["test_2x"]["annual"]),
        -row["test_2x"]["sharpe"], row["id"],
    ))
    payload = {
        "round": "R398",
        "classification": "strict walk-forward subset/execution confirmation; no promotion",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "train_rows": len(train), "validation_points": len(validation),
        "validation_stable_candidates": candidates,
        "candidate_count": len(candidates),
        "test_pass_count": sum(bool(row["test_gate"]) for row in candidates),
        "decision": {"status": "strict_holdout_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R398 严格三段 walk-forward 确认", "",
        "训练 2022-05-05..2024-05-05；验证 2024-05-06..2025-05-05；盲测 2025-05-06..2026-11-05。", "",
        "| 子集 | 仓数 | 持有 | 训练年化 | 验证年化 | 盲测年化 | 盲测 Sharpe | 盲测 MDD | 基准收益 | 超额 | 通过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in candidates:
        lines.append(
            f"| {', '.join(SHORT[f] for f in row['members'])} | {row['max_positions']} | {row['max_hold_days']} | "
            f"{row['train_2x']['annual']:.2%} | {row['validation_2x']['annual']:.2%} | {row['test_2x']['annual']:.2%} | "
            f"{row['test_2x']['sharpe']:.3f} | {row['test_2x']['mdd']:.2%} | {row['test_2x']['benchmark_return']:.2%} | "
            f"{row['benchmark_relative']:.2%} | {'是' if row['test_gate'] else '否'} |"
        )
    lines += ["", "## 结论边界", "", "- 测试窗口在前几轮已被观察，不能称为全新盲测。", "- 当前 HS300 名单回填历史有幸存者偏差。", "- 不生成订单、纸面目标或生产配置。", ""]
    markdown = "\n".join(lines)
    RESULT.write_text(markdown, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(f"validation_stable={len(candidates)} test_pass={payload['test_pass_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
