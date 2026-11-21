#!/usr/bin/env python3
"""R398 follow-up: freeze top benchmark-relative candidates and audit neighbours."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
SOURCE = E / "r398_strict_mc_2026-11-22.json"
PREREG = E / "r398_neighborhood_preregistration.json"
OUTPUT = E / "r398_neighborhood_2026-11-22.json"
RESULT = E / "r398_neighborhood_2026-11-22.md"
FACTORS = [
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low",
    "ext_alt_hs300_margin_chg_1m_low",
    "ext_alt_hs300_ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def load_candidates() -> list[dict[str, Any]]:
    verify_sidecar(SOURCE)
    report = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = [row for row in report["validation_stable_candidates"] if row.get("test_gate")]
    rows.sort(key=lambda row: (-row["benchmark_relative"], -row["test_2x"]["sharpe"], row["id"]))
    return rows[:3]


def variants(row: dict[str, Any]) -> list[dict[str, Any]]:
    base = {"members": row["members"], "weights": row["weights"], "max_positions": row["max_positions"], "max_hold_days": row["max_hold_days"]}
    out = [{"label": "base", "kind": "base", **base}]
    for pos in (10, 20, 30):
        out.append({"label": f"positions_{pos}", "kind": "positions", **{**base, "max_positions": pos}})
    for hold in (20, 30, 40):
        out.append({"label": f"hold_{hold}", "kind": "hold", **{**base, "max_hold_days": hold}})
    for factor in row["members"]:
        raw = {f: row["weights"][f] * (0.8 if f == factor else 1.0) for f in row["members"]}
        total = sum(raw.values())
        out.append({"label": f"down_{factor}", "kind": "weight_down", "members": row["members"], "weights": {f: v / total for f, v in raw.items()}, "max_positions": row["max_positions"], "max_hold_days": row["max_hold_days"]})
    return out


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r398_neighborhood_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(module: Any, service: Any, row: dict[str, Any], *, start: str, end: str, cost: str) -> dict[str, Any]:
    from datetime import date
    config = module._backtest_config(
        row["weights"], {f: "high" for f in row["members"]},
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]), cost=module._cost_parameters(cost),
        max_hold_days=int(row["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {"annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"), "mdd": stats.get("max_drawdown"), "total_return": stats.get("total_return"), "benchmark_return": module._curve_return(result.benchmark_curve), "trades": len(result.trades)}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R398 neighbourhood output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8")) if PREREG.exists() else {
        "round": "R398N",
        "source": "r398_strict_mc_2026-11-22.json",
        "selection": "top 3 benchmark-relative test-pass candidates frozen from R398",
        "windows": {"test_start": "2025-05-06", "test_end": "2026-11-05"},
        "cost_protocol": {"main": "2x", "stress": "3x", "five_x_hard_rejection": False},
        "classification": "frozen top-candidate neighbourhood audit; no promotion",
        "output_policy": {"status": "neighborhood_audit_complete_no_promotion", "active_version": None},
    }
    if not PREREG.exists():
        raw = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        PREREG.write_bytes(raw)
        PREREG.with_name(PREREG.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {PREREG.name}\n", encoding="ascii")
    verify_sidecar(PREREG)
    candidates = load_candidates()
    module = portfolio_module()
    service = module._service()
    reports = []
    for candidate in candidates:
        rows = []
        for item in variants(candidate):
            metrics = evaluate(module, service, item, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost="2x")
            stress = evaluate(module, service, item, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost="3x")
            relative = metrics["total_return"] - metrics["benchmark_return"]
            row = {**item, "test_2x": metrics, "test_3x": stress, "benchmark_relative": relative}
            row["pass"] = bool(metrics["annual"] > 0 and metrics["sharpe"] >= 0.5 and metrics["mdd"] >= -0.15 and stress["annual"] > 0)
            rows.append(row)
            print(json.dumps({"candidate": candidate["id"], "variant": item["label"], "annual": metrics["annual"], "sharpe": metrics["sharpe"], "relative": relative, "pass": row["pass"]}), flush=True)
        reports.append({"candidate": candidate, "variants": rows, "pass_count": sum(row["pass"] for row in rows)})
    stable = [report for report in reports if report["pass_count"] >= len(report["variants"]) * 0.8]
    payload = {
        "round": "R398N", "classification": prereg.get("classification", "frozen top-candidate neighbourhood audit; no promotion"),
        "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG), "source_sha256": sha(SOURCE),
        "candidate_count": len(candidates), "stable_candidate_count": len(stable), "reports": reports,
        "decision": {"status": "neighborhood_audit_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R398 邻域与成本复核", "", "对 R398 中盲测跑赢基准的前三名候选做冻结邻域审计。", "", f"- 候选数：{len(reports)}", f"- 80% 邻域通过：{len(stable)}", ""]
    for report in reports:
        lines += [f"## 候选 {report['candidate']['id']}", "", f"- 通过邻域：{report['pass_count']}/{len(report['variants'])}", ""]
    lines += ["## 边界", "", "- 这里不再挑选新的候选，只验证已经冻结的三名。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(f"stable={len(stable)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
