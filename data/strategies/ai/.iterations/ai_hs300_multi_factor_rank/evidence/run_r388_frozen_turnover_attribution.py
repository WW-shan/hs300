"""R388: attribute the frozen R386/R387 candidate's stock turnover and cost drag.

This runner is deliberately read-only with respect to strategy selection. It
verifies the R387 evidence and cache identities, sums A/B/C sleeve notional,
and publishes an append-only attribution report. It never tunes parameters or
creates orders.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r388_turnover_attribution_preregistration.json"
R387_REPORT = EVIDENCE / "r387_forward_shadow_confirmation.json"
R387_RESULT = EVIDENCE / "r387_forward_shadow_result.md"
R387_RUNNER = EVIDENCE / "run_r387_forward_shadow_confirmation.py"
# Keep a short alias for tests and downstream tooling; the underlying path is
# still the immutable R387 report.
REPORT = R387_REPORT
CACHE = DATA / "research" / "r387_forward_shadow_cache"
OUTPUT = EVIDENCE / "r388_turnover_attribution.json"
RESULT = EVIDENCE / "r388_turnover_attribution_result.md"
COSTS = ("2x", "3x", "5x")
EXPECTED_PARAMETERS = {
    "c_satellite_cap": 0.13,
    "equity_target_vol": 0.17,
    "overlay_lookback_days": 110,
    "overlay_target_vol": 0.17,
    "overlay_equity_weight_max": 0.37,
}
STOCK_COSTS = {
    "2x": {"commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
    "3x": {"commission_pct": 0.0006, "stamp_tax_pct": 0.001, "slippage_bps": 15.0},
    "5x": {"commission_pct": 0.001, "stamp_tax_pct": 0.00125, "slippage_bps": 25.0},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.relative_to(ROOT)}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch: {path.relative_to(ROOT)}")
    return actual


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R388 output exists; refusing overwrite: {path.name}")


def _cache_run(name: str) -> dict[str, Any]:
    sidecar = CACHE / f"{name}.json"
    if not sidecar.exists():
        raise RuntimeError(f"missing R387 cache manifest: {name}")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if payload.get("meta", {}).get("round") != "R387":
        raise RuntimeError(f"R387 cache round mismatch: {name}")
    if payload.get("meta", {}).get("end") != "2026-11-20":
        raise RuntimeError(f"R387 cache end mismatch: {name}")
    if "window_trades" not in payload.get("run", {}):
        raise RuntimeError(f"R387 cache lacks window_trades: {name}")
    return payload["run"]


def verify_r387_inputs() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    verify_sidecar(PREREG)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    result_text = R387_RESULT.read_text(encoding="utf-8")
    if report.get("parameters") != EXPECTED_PARAMETERS:
        raise RuntimeError("R387 parameters changed")
    if report.get("window", {}).get("end") != "2026-11-20":
        raise RuntimeError("R387 window tail changed")
    if report.get("no_leverage") is not True:
        raise RuntimeError("R387 no-leverage check failed")
    if report.get("decision", {}).get("status") != "shadow_observation_complete_no_decision":
        raise RuntimeError("R387 status is not a shadow observation")
    if sha256(REPORT) != prereg["input_sha256"]["r387_report"]:
        raise RuntimeError("R387 report hash changed")
    if sha256(R387_RESULT) != prereg["input_sha256"]["r387_result"]:
        raise RuntimeError("R387 result hash changed")
    if sha256(R387_RUNNER) != prereg["input_sha256"]["r387_runner"]:
        raise RuntimeError("R387 runner hash changed")
    return {"preregistration": prereg, "report": report, "result_text": result_text}


def _trade_row(name: str) -> dict[str, Any]:
    return _cache_run(name)


def sleeve_rows(cost: str) -> dict[str, Any]:
    a = _trade_row(f"A_{cost}_A")
    b = _trade_row(f"B_{cost}_fade05_h30_sl20")
    c = [_trade_row(f"C_{cost}_C{index}") for index in range(3)]
    return {"A": a, "B": b, "C": c}


def _sleeve_totals(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list):
        trades = [row["window_trades"] for row in rows]
        buy = sum(float(row["buy_notional"]) for row in trades)
        sell = sum(float(row["sell_notional"]) for row in trades)
        entries = sum(int(row["entry_count"]) for row in trades)
        exits = sum(int(row["exit_count"]) for row in trades)
        full = sum(int(row.get("full_trade_count", 0)) for row in rows)
    else:
        trades = rows["window_trades"]
        buy = float(trades["buy_notional"])
        sell = float(trades["sell_notional"])
        entries = int(trades["entry_count"])
        exits = int(trades["exit_count"])
        full = int(rows.get("full_trade_count", 0))
    return {
        "buy_notional": buy,
        "sell_notional": sell,
        "gross_notional": buy + sell,
        "entry_count": entries,
        "exit_count": exits,
        "full_trade_count": full,
    }


def attribute_sleeves(rows: dict[str, Any], cost: str, initial_capital: float = 1_000_000.0) -> dict[str, Any]:
    if cost not in STOCK_COSTS:
        raise ValueError(f"unsupported cost scenario: {cost}")
    sleeves = {name: _sleeve_totals(rows[name]) for name in ("A", "B", "C")}
    total_buy = sum(row["buy_notional"] for row in sleeves.values())
    total_sell = sum(row["sell_notional"] for row in sleeves.values())
    total_gross = total_buy + total_sell
    for row in sleeves.values():
        row["share_of_total_turnover"] = row["gross_notional"] / total_gross if total_gross else 0.0
    assumptions = STOCK_COSTS[cost]
    buy_rate = assumptions["commission_pct"] + assumptions["slippage_bps"] / 10000.0
    sell_rate = assumptions["commission_pct"] + assumptions["stamp_tax_pct"] + assumptions["slippage_bps"] / 10000.0
    estimated_cost = total_buy * buy_rate + total_sell * sell_rate
    return {
        "cost": cost,
        "initial_capital": initial_capital,
        "total_buy_notional": total_buy,
        "total_sell_notional": total_sell,
        "total_gross_notional": total_gross,
        "one_way_turnover": total_gross / (2.0 * initial_capital),
        "estimated_stock_cost": estimated_cost,
        "estimated_stock_cost_pct_of_capital": estimated_cost / initial_capital,
        "buy_cost_rate": buy_rate,
        "sell_cost_rate": sell_rate,
        "sleeves": sleeves,
    }


def turnover_warning(attribution: dict[str, Any], warning_protocol: dict[str, Any], *, cost: str) -> dict[str, Any]:
    threshold = float(warning_protocol["one_way_turnover_warning_threshold"])
    warning = bool(attribution["one_way_turnover"] >= threshold)
    return {
        "warning": warning,
        "hard_rejection": False,
        "threshold": threshold,
        "cost": cost,
        "five_x_is_warning_only": bool(warning_protocol["five_x_is_warning_only"]),
    }


def build_report() -> dict[str, Any]:
    verified = verify_r387_inputs()
    report = verified["report"]
    costs: dict[str, Any] = {}
    for cost in COSTS:
        attribution = attribute_sleeves(sleeve_rows(cost), cost)
        attribution["warning"] = turnover_warning(
            attribution,
            verified["preregistration"]["warning_protocol"],
            cost=cost,
        )
        r387_cost = report["costs"][cost]
        attribution["portfolio"] = {
            "total_return": r387_cost["metrics"]["total_return"],
            "sharpe": r387_cost["metrics"]["sharpe"],
            "mdd": r387_cost["metrics"]["mdd"],
            "excess_total_return": r387_cost["excess_total_return"],
            "one_way_turnover": r387_cost["trades"]["one_way_turnover"],
            "max_total_weight": r387_cost["exposures"]["total_weight"],
        }
        costs[cost] = attribution
    no_leverage = all(row["portfolio"]["max_total_weight"] <= 1.0 + 1e-12 for row in costs.values())
    return {
        "round": "R388",
        "classification": "frozen-strategy turnover and cost attribution; no tuning, no execution",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "source_round": "R387",
        "source_status": report["decision"]["status"],
        "window": report["window"],
        "parameters": report["parameters"],
        "input_sha256": verified["preregistration"]["input_sha256"],
        "costs": costs,
        "no_leverage": no_leverage,
        "all_pass": bool(no_leverage),
        "decision": {
            "status": "attribution_complete_no_decision",
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": verified["preregistration"]["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R388 · 冻结候选换手与成本归因",
        "",
        "R388 不改变 R386/R387 的 A/B/C 策略或组合参数，只把冻结候选的股票换手拆到 sleeve。",
        "这是归因报告，不是新的策略选择；11 天观察窗口不能支持参数调整。",
        "",
        "## 换手归因",
        "",
        "| 成本 | 总换手 | 2×/3×/5× 总收益 | Sharpe | MDD | 5× warning |",
        "| --- | ---: | ---: | ---: | ---: | :---: |",
    ]
    for cost in COSTS:
        row = report["costs"][cost]
        lines.append(
            f"| {cost} | {row['one_way_turnover']:.2%} | {row['portfolio']['total_return']:.2%} | "
            f"{row['portfolio']['sharpe']:.3f} | {row['portfolio']['mdd']:.2%} | "
            f"{'是' if row['warning']['warning'] else '否'} |"
        )
    lines.extend([
        "",
        "## 2× sleeve 占比",
        "",
        "| Sleeve | 买入金额 | 卖出金额 | 单边换手 | 换手占比 | 买入/卖出笔数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    row = report["costs"]["2x"]
    for name in ("A", "B", "C"):
        sleeve = row["sleeves"][name]
        lines.append(
            f"| {name} | {sleeve['buy_notional']/1e6:.2f}M | {sleeve['sell_notional']/1e6:.2f}M | "
            f"{sleeve['gross_notional']/(2*row['initial_capital']):.2%} | "
            f"{sleeve['share_of_total_turnover']:.2%} | {sleeve['entry_count']}/{sleeve['exit_count']} |"
        )
    lines.extend([
        "",
        "## 成本近似",
        "",
        f"- 2× 股票成本估算：{row['estimated_stock_cost_pct_of_capital']:.2%} 的初始资金。",
        "- 成本按 R387 冻结的佣金、印花税和滑点拆分口径计算；仅用于归因，不替代引擎完整净值。",
        "- 5× 只记录 warning，不构成硬否决。",
        f"- 最大总权重：{max(item['portfolio']['max_total_weight'] for item in report['costs'].values()):.4f}；无杠杆检查：{'通过' if report['no_leverage'] else '失败'}。",
        "",
        "## 结论边界",
        "",
        "- 本轮不调参、不改变策略、不读取未来数据。",
        "- 11 天样本不足以判断低换手替代方案是否更好。",
        "- 没有订单、纸面目标或生产配置。",
        "",
    ])
    return "\n".join(lines)


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def write_text_with_checksum(path: Path, text: str) -> None:
    raw = text.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    write_json_with_checksum(OUTPUT, report)
    write_text_with_checksum(RESULT, markdown(report))
    print(json.dumps(report["costs"]["2x"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
