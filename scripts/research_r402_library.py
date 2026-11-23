#!/usr/bin/env python3
"""R402: multi-dimension registered-factor library expansion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r402_library_preregistration.json"
OUTPUT = E / "r402_library_2026-11-22.json"
RESULT = E / "r402_library_2026-11-22.md"
SELECTED = [
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_idio_vol_40d_low",
    "ext_alt_hs300_ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low",
    "momentum_60d",
    "pb_latest",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R402 preregistration checksum mismatch")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R402":
        raise RuntimeError("R402 round mismatch")
    return payload


def portfolio():
    import importlib.util
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r402_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metrics(module: Any, service: Any, weights: dict[str, float], *, start: str, end: str, positions: int, hold: int, cost: str) -> dict[str, Any]:
    from datetime import date
    config = module._backtest_config(
        weights, {factor: "high" for factor in weights},
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=positions, cost=module._cost_parameters(cost), max_hold_days=hold,
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r402_library.py")
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {"annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"), "mdd": stats.get("max_drawdown"), "total_return": stats.get("total_return"), "trades": len(result.trades)}


def index_return(start: str, end: str, symbol: str) -> float:
    from datetime import date

    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        raise RuntimeError("R402 benchmark unavailable")
    return float(frame["close"][-1] / frame["close"][0] - 1.0)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R402 output exists: {path.name}")
    prereg = verify()
    if len(SELECTED) != int(prereg["design"]["capacity_limit"]):
        raise RuntimeError("R402 selected set must respect the eight-factor execution cap")
    module = portfolio()
    service = module._service()
    weights = {factor: 1.0 / len(SELECTED) for factor in SELECTED}
    windows = prereg["windows"]
    rows = []
    for positions in prereg["design"]["grid"]["max_positions"]:
        for hold in prereg["design"]["grid"]["max_hold_days"]:
            train = metrics(module, service, weights, start=windows["train_start"], end=windows["train_end"], positions=positions, hold=hold, cost="2x")
            validation = metrics(module, service, weights, start=windows["validation_start"], end=windows["validation_end"], positions=positions, hold=hold, cost="2x")
            test = metrics(module, service, weights, start=windows["test_start"], end=windows["test_end"], positions=positions, hold=hold, cost="2x")
            test3 = metrics(module, service, weights, start=windows["test_start"], end=windows["test_end"], positions=positions, hold=hold, cost="3x")
            hs300 = index_return(windows["test_start"], windows["test_end"], prereg["benchmark"]["symbol"])
            row = {
                "max_positions": positions, "max_hold_days": hold, "members": SELECTED,
                "weights": weights, "train_2x": train, "validation_2x": validation,
                "test_2x": test, "test_3x": test3, "hs300_return": hs300,
                "hs300_excess": test["total_return"] - hs300,
            }
            rows.append(row)
            print(json.dumps({"positions": positions, "hold": hold, "train": train["annual"], "validation": validation["annual"], "test": test["annual"], "hs300_excess": row["hs300_excess"], "pass": row["hs300_excess"] > 0}), flush=True)
    rows.sort(key=lambda row: (-min(row["train_2x"]["annual"], row["validation_2x"]["annual"], row["test_2x"]["annual"]), -row["test_2x"]["sharpe"]))
    payload = {
        "round": "R402", "classification": prereg["classification"],
        "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG),
        "members": SELECTED, "grid": rows,
        "decision": {"status": "library_expansion_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R402 多维因子库扩容", "", f"8 腿经济多维组合（执行层上限），沪深300基准 `{prereg['benchmark']['symbol']}`。", "", "| 仓数 | 持有 | 训练年化 | 验证年化 | 盲测年化 | 盲测Sharpe | HS300超额 |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['max_positions']} | {row['max_hold_days']} | {row['train_2x']['annual']:.2%} | {row['validation_2x']['annual']:.2%} | {row['test_2x']['annual']:.2%} | {row['test_2x']['sharpe']:.3f} | {row['hs300_excess']:+.2%} |")
    lines += ["", "## 边界", "", "- 这是已注册因子的组合，不是新公式。", "- 当前 HS300 名单回填历史仍有幸存者偏差。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
