#!/usr/bin/env python3
"""R477: frozen-sample diagnostics for the R476 expanded factor library."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r477_frozen_library_diagnostics_preregistration.json"
R476 = E / "r476_factor_set_expansion_mc_2026-11-22.json"
OUTPUT = E / "r477_frozen_library_diagnostics_2026-11-22.json"
RESULT = E / "r477_frozen_library_diagnostics_2026-11-22.md"
COMPOSITE = "_r477_frozen_library_composite"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R477 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def curve_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [
        (str(curve[index]["date"])[:10], float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0)
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def yearly_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    buckets: dict[str, float] = defaultdict(lambda: 1.0)
    for day, value in curve_returns(curve):
        buckets[day[:4]] *= 1.0 + value
    return {year: round(product - 1.0, 6) for year, product in sorted(buckets.items())}


def monthly_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    buckets: dict[str, float] = defaultdict(lambda: 1.0)
    for day, value in curve_returns(curve):
        buckets[day[:7]] *= 1.0 + value
    return {month: round(product - 1.0, 6) for month, product in sorted(buckets.items())}


def rolling_year_returns(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = [(str(item["date"])[:10], float(item["value"])) for item in curve if item.get("value")]
    output = []
    for start_index, (start_day, start_value) in enumerate(values):
        target = date.fromisoformat(start_day)
        end_index = start_index
        while end_index + 1 < len(values) and date.fromisoformat(values[end_index + 1][0]) <= date(target.year + 1, target.month, target.day) - __import__("datetime").timedelta(days=1):
            end_index += 1
        if end_index == start_index:
            continue
        end_day, end_value = values[end_index]
        output.append({"start": start_day, "end": end_day, "return": round(end_value / start_value - 1.0, 6)})
    return output


def benchmark_return(curve: list[dict[str, Any]]) -> float | None:
    if not curve:
        return None
    first = float(curve[0].get("value") or 0.0)
    last = float(curve[-1].get("value") or 0.0)
    return round(last / first - 1.0, 6) if first else None


def concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = sorted((float(item.get("pnl_amount") or 0.0) for item in trades), reverse=True)
    total = sum(pnls)
    return {
        "trade_count": len(pnls),
        "positive_trades": sum(value > 0 for value in pnls),
        "negative_trades": sum(value < 0 for value in pnls),
        "total_pnl": round(total, 4),
        "top_1_pnl": round(pnls[0], 4) if pnls else 0.0,
        "top_5_pnl": round(sum(pnls[:5]), 4),
        "top_10_pnl": round(sum(pnls[:10]), 4),
        "remaining_after_top_1": round(total - (pnls[0] if pnls else 0.0), 4),
        "remaining_after_top_5": round(total - sum(pnls[:5]), 4),
        "remaining_after_top_10": round(total - sum(pnls[:10]), 4),
    }


def symbol_concentration(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0.0, "pnl": 0.0})
    for item in trades:
        symbol = str(item.get("symbol") or "")
        grouped[symbol]["trades"] += 1
        grouped[symbol]["pnl"] += float(item.get("pnl_amount") or 0.0)
    total = sum(value["pnl"] for value in grouped.values())
    rows = [
        {
            "symbol": symbol,
            "trades": int(value["trades"]),
            "pnl": round(value["pnl"], 4),
            "share_of_total_pnl": round(value["pnl"] / total, 6) if total else None,
        }
        for symbol, value in grouped.items()
    ]
    return sorted(rows, key=lambda item: item["pnl"], reverse=True)


def block_bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [value for _, value in curve_returns(curve)]
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + item for item in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
        path = [1.0]
        for item in selected:
            path.append(path[-1] * (1.0 + item))
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)
    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "block_days": block,
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.5),
        "sharpe_p50": ordered(sharpes, 0.5),
        "mdd_p05": ordered(mdds, 0.05),
    }


def run_frozen(
    r468: Any,
    r464: Any,
    p5: Any,
    service: Any,
    panel: Any,
    symbols: list[str],
    frozen: dict[str, Any],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite
    from app.factor_research.locking import atomic_write_text

    del atomic_write_text
    field = COMPOSITE
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel,
            frozen["weights"],
            frozen["directions"],
            field,
            minimum_weight_coverage=0.75,
        )
    seed_config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(frozen["max_positions"]),
        cost=p5.portfolio._cost_parameters(cost_label) if cost_label != "5x" else {
            "fees_pct": 0.001,
            "commission_pct": 0.001,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 25.0,
        },
        max_hold_days=int(frozen["max_hold_days"]),
    )
    seed_config.params = {
        **(seed_config.params or {}),
        "entry_score": float(frozen["entry_score"]),
        "exit_score": float(frozen["exit_score"]),
        "top_rank": int(frozen["top_rank"]),
    }
    seed_config.max_exposure_pct = float(frozen["max_exposure_pct"])
    seed_config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([seed_config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, [field])
    result = service.run_with_evidence(seed_config, prepared=prepared)
    require_formal_evidence(result, label="research_r477_frozen_library_diagnostics.py")
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "benchmark_curve": list(result.benchmark_curve),
        "trades": list(result.trades),
        "per_symbol_stats": list(result.per_symbol_stats),
        "trade_count": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r476 = json.loads(R476.read_text(encoding="utf-8"))
    frozen = r476["selected"]
    if frozen["label"] != prereg["source_selected_label"]:
        raise RuntimeError("R477 source sample changed")
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r477_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r477_r464")
    p5 = r464.load_p5()
    panel, _ignored = r464.build_panel(p5)
    # Reconstruct the exact R476 panel and its frozen 9-leg composite.
    from app.hs300.current import current_symbols
    price_panel = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price_panel.select(["symbol", "date", "momentum_60d"]),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(ROOT / "data")
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    missing = [factor for factor in frozen["weights"] if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    missing = [factor for factor in frozen["weights"] if factor not in panel.columns]
    if missing:
        raise RuntimeError(f"frozen factors unavailable: {missing}")
    # Use only the frozen R476 weights, not a regenerated candidate grid.
    from app.factor_research.incremental import build_rank_composite
    panel, _ = build_rank_composite(
        panel,
        frozen["weights"],
        frozen["directions"],
        COMPOSITE,
        minimum_weight_coverage=0.75,
    )
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    runs = {}
    for window, start in (("test", windows["test_start"]), ("recent", windows["recent_start"])):
        runs[window] = run_frozen(r468, r464, p5, service, panel, symbols, frozen, start=start, end=windows["end"], cost_label="2x")
    runs["test_3x"] = run_frozen(r468, r464, p5, service, panel, symbols, frozen, start=windows["test_start"], end=windows["end"], cost_label="3x")
    runs["test_5x"] = run_frozen(r468, r464, p5, service, panel, symbols, frozen, start=windows["test_start"], end=windows["end"], cost_label="5x")
    diagnostics = {}
    for window in ("test", "recent"):
        result = runs[window]
        diagnostics[window] = {
            "yearly_returns": yearly_returns(result["curve"]),
            "monthly_returns": monthly_returns(result["curve"]),
            "rolling_one_year": rolling_year_returns(result["curve"]),
            "benchmark_return": benchmark_return(result["benchmark_curve"]),
            "trade_concentration": concentration(result["trades"]),
            "symbol_concentration": symbol_concentration(result["trades"]),
            "bootstrap": [
                block_bootstrap(result["curve"], block=block, paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + index)
                for index, block in enumerate(prereg["bootstrap"]["block_days"])
            ],
        }
    payload = {
        "round": "R477",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r476_sha256": sha(R476),
        "source_selected_label": frozen["label"],
        "frozen_weights": frozen["weights"],
        "frozen_directions": frozen["directions"],
        "frozen_execution": {
            key: frozen[key]
            for key in ("max_positions", "max_hold_days", "entry_score", "exit_score", "top_rank", "max_exposure_pct")
        },
        "new_parameter_search": False,
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "runs": {
            name: {
                "stats": result["stats"],
                "trade_count": result["trade_count"],
                "benchmark_return": benchmark_return(result["benchmark_curve"]),
            }
            for name, result in runs.items()
        },
        "diagnostics": diagnostics,
        "gate": {
            "status": "diagnostic_only",
            "watch_ready": False,
            "reason": "Frozen R476 sample remains rejected; this round explains fragility and does not promote it.",
        },
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    test = runs["test"]["stats"]
    recent = runs["recent"]["stats"]
    tc = diagnostics["test"]["trade_concentration"]
    lines = [
        "# R477 冻结因子库诊断",
        "",
        "本轮不重新选样：直接冻结 R476 的 9 腿组合和参数，只解释其检验/近一年表现为何不能晋级。",
        "",
        f"- 冻结样本：`{frozen['label']}`",
        f"- 组合腿数：{len(frozen['weights'])}",
        f"- 检验年化：{test['annual_return']:.2%}；Sharpe：{test['sharpe']:.3f}；MDD：{test['max_drawdown']:.2%}",
        f"- 近一年年化：{recent['annual_return']:.2%}；Sharpe：{recent['sharpe']:.3f}；MDD：{recent['max_drawdown']:.2%}",
        f"- 检验相对基准：{diagnostics['test']['benchmark_return'] or 0.0:.2%}（策略 {test['total_return']:.2%}）",
        f"- 近一年相对基准：{diagnostics['recent']['benchmark_return'] or 0.0:.2%}（策略 {recent['total_return']:.2%}）",
        f"- 检验交易数：{tc['trade_count']}；正/负：{tc['positive_trades']}/{tc['negative_trades']}",
        f"- 总盈亏：{tc['total_pnl']:.2f}；去掉最大 1/5/10 笔后：{tc['remaining_after_top_1']:.2f}/{tc['remaining_after_top_5']:.2f}/{tc['remaining_after_top_10']:.2f}",
        "",
        "## 检验期逐年收益",
        "",
        *[f"- {year}: {value:.2%}" for year, value in diagnostics["test"]["yearly_returns"].items()],
        "",
        "## 近一年逐年收益",
        "",
        *[f"- {year}: {value:.2%}" for year, value in diagnostics["recent"]["yearly_returns"].items()],
        "",
        "## 门槛与结论",
        "",
        "- 本轮只做冻结样本诊断，不产生新的 watch 候选。",
        "- R476 仍为 rejected；`active_version` 保持 null。",
        "",
        "## 边界",
        "",
    ] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
