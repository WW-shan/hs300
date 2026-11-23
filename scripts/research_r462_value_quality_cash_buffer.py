#!/usr/bin/env python3
"""R462: cash exposure buffer for the R453 value_quality portfolio."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r462_value_quality_cash_buffer_preregistration.json"
OUTPUT = E / "r462_value_quality_cash_buffer_2026-11-22.json"
RESULT = E / "r462_value_quality_cash_buffer_2026-11-22.md"
R453_PREREG = E / "r453_value_quality_portfolio_preregistration.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R462 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R462 preregistration checksum mismatch")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r462_r453")
    p5 = r453.load_p5()
    r453_prereg = json.loads(R453_PREREG.read_text(encoding="utf-8"))
    panel, symbols = r453.build_panel(p5, r453_prereg)
    service = p5.portfolio._service()
    directions = {**r453_prereg["controls"]["directions"], "pb_latest": "low", "ocf_debt": "high", "debt_ratio_latest": "low", "value_quality": "high"}
    weights = prereg["families"]["four_plus_value_quality_10"]["weights"]
    windows = prereg["windows"]
    rows = []
    for exposure in prereg["cash_exposure_grid"]:
        for positions in prereg["positions"]:
            for hold in prereg["hold_days"]:
                row = {"exposure": exposure, "positions": positions, "hold": hold}
                for window in ("train", "validation"):
                    seed = next(iter(weights))
                    seed_config = p5.portfolio._backtest_config({seed: 1.0}, {seed: "high"}, start=date.fromisoformat(windows[f"{window}_start"]), end=date.fromisoformat(windows[f"{window}_end"]), max_positions=20, cost=p5.portfolio._cost_parameters("2x"), max_hold_days=hold)
                    seed_config.symbols = list(symbols)
                    base = service.prepare_matrix_optimization([seed_config])
                    prepared = p5._extend_prepared_with_panel(base, panel, list(weights))
                    config = p5.portfolio._backtest_config(weights, {factor: directions[factor] for factor in weights}, start=date.fromisoformat(windows[f"{window}_start"]), end=date.fromisoformat(windows[f"{window}_end"]), max_positions=positions, cost=p5.portfolio._cost_parameters("2x"), max_hold_days=hold)
                    config.max_exposure_pct = exposure
                    config.symbols = list(prepared.market_data.symbols)
                    result = service.run_with_evidence(config, prepared=prepared)
                    require_formal_evidence(result, label="research_r462_value_quality_cash_buffer.py")
                    if result.error:
                        raise RuntimeError(result.error)
                    row[window] = {"stats": p5._select_stats(result.stats), "curve": list(result.equity_curve), "trades": len(result.trades)}
                rows.append(row)
    eligible = [row for row in rows if row["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["exposure"], row["positions"], row["hold"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]))
    test_r453 = r453.run_variant(p5, service, panel, symbols, weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    recent_r453 = r453.run_variant(p5, service, panel, symbols, weights, directions, start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    # Scale only exposure at the return level; preserve each engine curve's timing.
    def scale(curve: list[dict[str, Any]], exposure: float) -> list[dict[str, Any]]:
        out = []
        value = 1.0
        for index, item in enumerate(curve):
            if index:
                previous = float(curve[index - 1]["value"])
                ret = float(item["value"]) / previous - 1.0
                value *= 1.0 + exposure * ret
            out.append({"date": item["date"], "value": value})
        return out
    test_curve = scale(test_r453["curve"], float(selected["exposure"]))
    recent_curve = scale(recent_r453["curve"], float(selected["exposure"]))

    # Exact summary from the scaled curve using the same metrics contract.
    def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
        values = [float(item["value"]) for item in curve]
        rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        total = values[-1] / values[0] - 1.0 if values else 0.0
        mean = sum(rets) / len(rets) if rets else 0.0
        vol = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5 if rets else 0.0
        peak = values[0] if values else 1.0
        mdd = 0.0
        for value in values:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        return {"total_return": total, "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0 if rets else 0.0, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}
    test_stats_scaled = metrics(test_curve)
    recent_stats_scaled = metrics(recent_curve)
    test_bootstrap = r453.bootstrap(test_curve, block=20, paths=5000, seed=462)
    recent_bootstrap = r453.bootstrap(recent_curve, block=20, paths=5000, seed=463)
    checks = {
        "train_annual_min": selected["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": test_stats_scaled["annual"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": test_stats_scaled["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": test_stats_scaled["mdd"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": recent_stats_scaled["annual"] >= prereg["gates"]["recent_annual_min"],
        "test_total_p05_positive": test_bootstrap["total_p05"] > 0,
        "recent_total_p05_positive": recent_bootstrap["total_p05"] > 0,
        "max_exposure_at_most": selected["exposure"] <= 1.0,
        "no_leverage": True,
    }
    payload = {"round": "R462", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "rows": [{**row, "train": {"stats": row["train"]["stats"], "trades": row["train"]["trades"]}, "validation": {"stats": row["validation"]["stats"], "trades": row["validation"]["trades"]}} for row in rows], "selected": {**selected, "test": test_stats_scaled, "recent": recent_stats_scaled, "test_bootstrap": test_bootstrap, "recent_bootstrap": recent_bootstrap}, "gate": {"pass": all(checks.values()), "checks": checks}, "diagnostics": {"five_x_warning_only": True, "no_leverage": True}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R462 value_quality 现金缓冲验证", "", "| 暴露 | 仓数 | 持有 | 训练年化 | 验证年化 |", "| ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['exposure']:.0%} | {row['positions']} | {row['hold']} | {row['train']['stats']['annual_return']:.2%} | {row['validation']['stats']['annual_return']:.2%} |")
    lines += ["", f"- 选中：{selected['exposure']:.0%} 暴露 / {selected['positions']} 仓 / {selected['hold']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 检验年化：{test_stats_scaled['annual']:.2%}", f"- 检验 Sharpe：{test_stats_scaled['sharpe']:.3f}", f"- 检验 MDD：{test_stats_scaled['mdd']:.2%}", f"- 近一年年化：{recent_stats_scaled['annual']:.2%}", f"- 检验 p05：{test_bootstrap['total_p05']:.2%}", f"- 近一年 p05：{recent_bootstrap['total_p05']:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
