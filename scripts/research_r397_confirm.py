#!/usr/bin/env python3
"""R397 confirmation: evaluate frozen training-stable subsets on the test window."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r397_factor_subset_preregistration.json"
TRAIN_RUNS = EVIDENCE / "r397_runs" / "train_runs.jsonl"
OUTPUT = EVIDENCE / "r397_factor_subset_confirm_2026-11-22.json"
RESULT = EVIDENCE / "r397_factor_subset_confirm_2026-11-22.md"
TRAIN = ("2022-05-05", "2025-05-05")
TEST = ("2025-05-06", "2026-11-05")
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


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R397 confirmation output exists; refusing overwrite: {path.name}")


def load_stable_candidates() -> list[dict[str, Any]]:
    verify_sidecar(PREREG)
    rows = [json.loads(line) for line in TRAIN_RUNS.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_members: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        by_members.setdefault(tuple(row["members"]), []).append(row)
    candidates = []
    for members, group in by_members.items():
        if len(group) != 9 or not all(row["train_gate"] for row in group):
            continue
        best = max(group, key=lambda row: (row["train_2x"]["annual"], row["train_2x"]["sharpe"], -row["index"]))
        candidates.append({
            "members": list(members),
            "short_members": [SHORT[factor] for factor in members],
            "max_positions": int(best["max_positions"]),
            "holding_days": int(best["holding_days"]),
            "train_platform_pass_count": 9,
            "train_2x": best["train_2x"],
            "source_index": best["index"],
        })
    candidates.sort(key=lambda row: (-row["train_2x"]["annual"], -row["train_2x"]["sharpe"], row["source_index"]))
    return candidates


def _portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r397_confirm_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(module: Any, service: Any, candidate: dict[str, Any], *, cost: str) -> dict[str, Any]:
    from datetime import date
    members = candidate["members"]
    config = module._backtest_config(
        {factor: 1.0 / len(members) for factor in members},
        {factor: "high" for factor in members},
        start=date.fromisoformat(TEST[0]),
        end=date.fromisoformat(TEST[1]),
        max_positions=int(candidate["max_positions"]),
        cost=module._cost_parameters(cost),
    )
    config.holding_days = int(candidate["holding_days"])
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {
        "annual": stats.get("annual_return"),
        "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"),
        "total_return": stats.get("total_return"),
        "trades": len(result.trades),
    }


def build_report() -> dict[str, Any]:
    candidates = load_stable_candidates()
    module = _portfolio_module()
    service = module._service()
    rows = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        row["test_2x"] = evaluate(module, service, row, cost="2x")
        row["test_3x"] = evaluate(module, service, row, cost="3x")
        row["confirmation_gate"] = bool(
            row["test_2x"]["annual"] is not None
            and row["test_2x"]["annual"] >= 0.07
            and row["test_2x"]["sharpe"] is not None
            and row["test_2x"]["sharpe"] >= 0.6
            and row["test_3x"]["annual"] is not None
            and row["test_3x"]["annual"] > 0.0
        )
        row["id"] = index
        rows.append(row)
        print(json.dumps({"id": index, "members": row["short_members"], "positions": row["max_positions"], "holding": row["holding_days"], "test_annual": row["test_2x"]["annual"], "test_sharpe": row["test_2x"]["sharpe"], "pass": row["confirmation_gate"]}), flush=True)
    passed = [row for row in rows if row["confirmation_gate"]]
    passed.sort(key=lambda row: (-min(row["train_2x"]["annual"], row["test_2x"]["annual"]), -min(row["train_2x"]["sharpe"], row["test_2x"]["sharpe"]), row["id"]))
    return {
        "round": "R397", "classification": "frozen training-stable subset confirmation; no promotion",
        "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG),
        "train_runs_sha256": sha256(TRAIN_RUNS), "windows": {"train": TRAIN, "test": TEST},
        "candidate_count": len(rows), "confirmation_pass_count": len(passed),
        "candidates": rows, "best_confirmed": passed[0] if passed else None,
        "decision": {"status": "subset_confirmation_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R397 因子子集与组合参数确认",
        "",
        "训练段只用于筛选 9/9 参数平台；下面所有子集在检验段一次性确认，未使用检验段重新选参。",
        "",
        f"- 候选子集：{report['candidate_count']}",
        f"- 检验通过：{report['confirmation_pass_count']}",
        "",
        "| 子集 | 仓数 | 持有期 | 训练年化 | 检验年化 | 检验 Sharpe | 检验 MDD | 3× 检验年化 | 通过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in report["candidates"]:
        lines.append(f"| {', '.join(row['short_members'])} | {row['max_positions']} | {row['holding_days']} | {row['train_2x']['annual']:.2%} | {row['test_2x']['annual']:.2%} | {row['test_2x']['sharpe']:.3f} | {row['test_2x']['mdd']:.2%} | {row['test_3x']['annual']:.2%} | {'是' if row['confirmation_gate'] else '否'} |")
    lines += ["", "## 结论边界", "", "- 子集来自预登记训练平台，不是新增因子。", "- 检验段仅确认，不再选择。", "- 不生成订单、纸面目标或生产配置。", ""]
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    payload = build_report()
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    md = markdown(payload)
    RESULT.write_text(md, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
