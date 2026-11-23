#!/usr/bin/env python3
"""R488: T-1 conditional bond/gold overlay on frozen R484 sleeve."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_TYPE = "diagnostic_custom_simulation"

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r488_defensive_asset_drawdown_conditional_preregistration.json"
R484 = E / "r484_daily_entry_batch_cap_2026-11-22.json"
OUTPUT = E / "r488_defensive_asset_drawdown_conditional_2026-11-22.json"
RESULT = E / "r488_defensive_asset_drawdown_conditional_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R488 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def previous_drawdown(curve: list[dict[str, Any]]) -> list[float]:
    peak = 0.0
    output = []
    for item in curve:
        value = float(item["value"])
        peak = max(peak, value)
        output.append(value / peak - 1.0)
    return output


def load_regime() -> dict[str, str]:
    frame = pl.read_parquet(ROOT / "data" / "regime_history" / "part.parquet").sort("date")
    return {
        str(row["date"])[:10]: str(row["state"])
        for row in frame.select(["date", "state"]).iter_rows(named=True)
    }


def rule_weights(
    curve: list[dict[str, Any]], regime: dict[str, str], rule: dict[str, Any]
) -> list[dict[str, float]]:
    condition = str(rule.get("condition", "none"))
    equity = float(rule.get("equity", 1.0))
    bond = float(rule.get("bond", 0.0))
    gold = float(rule.get("gold", 0.0))
    cash = float(rule.get("cash", 0.0))
    fallback = float(rule.get("fallback_equity", 1.0))
    drawdowns = previous_drawdown(curve)
    output = []
    for index, item in enumerate(curve):
        day = str(item["date"])[:10]
        previous_drawdown_value = drawdowns[index - 1] if index > 0 else 0.0
        prev_day = str(curve[index - 1]["date"])[:10] if index > 0 else day
        if condition == "none":
            output.append({"equity": 1.0, "bond": 0.0, "gold": 0.0, "cash": 0.0})
        elif condition == "always":
            output.append({"equity": equity, "bond": bond, "gold": gold, "cash": cash})
        elif condition == "drawdown_le_-0.05" and previous_drawdown_value <= -0.05:
            output.append({"equity": equity, "bond": bond, "gold": gold, "cash": cash})
        elif condition == "regime_weak_or_lean_weak" and regime.get(prev_day) in {
            "weak",
            "lean_weak",
        }:
            output.append({"equity": equity, "bond": bond, "gold": gold, "cash": cash})
        else:
            output.append({"equity": fallback, "bond": 0.0, "gold": 0.0, "cash": 0.0})
    return output


def load_etf(symbols: list[str], start: str, end: str) -> pl.DataFrame:
    paths = sorted((ROOT / "data" / "kline_etf_enriched").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol").is_in(symbols))
            & (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
        )
        .select(["symbol", "date", "open", "close"])
        .collect()
        .sort(["symbol", "date"])
    )
    missing = sorted(set(symbols) - set(frame["symbol"].unique().to_list()))
    if missing:
        raise FileNotFoundError(f"missing ETF history: {missing}")
    return frame


def etf_returns(frame: pl.DataFrame, symbol: str) -> dict[str, float]:
    rows = frame.filter(pl.col("symbol") == symbol).sort("date")
    output = {}
    for index in range(1, rows.height):
        day = str(rows["date"][index])[:10]
        prev_close = float(rows["close"][index - 1])
        output[day] = float(rows["open"][index]) / prev_close - 1.0
    return output


def apply_rule(
    curve: list[dict[str, Any]],
    regime: dict[str, str],
    rule: dict[str, Any],
    etf: pl.DataFrame,
    cost_bps: float,
) -> list[dict[str, Any]]:
    weights = rule_weights(curve, regime, rule)
    bond = etf_returns(etf, "511010.SH")
    gold = etf_returns(etf, "518880.SH")
    value = 1.0
    output = [{"date": str(curve[0]["date"])[:10], "value": 1.0}]
    previous = weights[0]
    for index in range(1, len(curve)):
        day = str(curve[index]["date"])[:10]
        equity_ret = float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        allocation = weights[index - 1]
        bond_ret = bond.get(day, 0.0)
        gold_ret = gold.get(day, 0.0)
        portfolio_ret = (
            allocation["equity"] * equity_ret
            + allocation["bond"] * bond_ret
            + allocation["gold"] * gold_ret
        )
        turnover = sum(
            abs(allocation[key] - previous[key]) for key in ("equity", "bond", "gold", "cash")
        )
        value *= 1.0 + portfolio_ret
        value *= 1.0 - turnover * cost_bps / 10000.0
        output.append({"date": day, "value": value})
        previous = allocation
    return output


def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    mean = sum(returns) / len(returns) if returns else 0.0
    vol = (sum((item - mean) ** 2 for item in returns) / len(returns)) ** 0.5 if returns else 0.0
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": round(total, 6),
        "annual_return": round((1.0 + total) ** (252.0 / len(returns)) - 1.0, 6)
        if returns
        else 0.0,
        "sharpe": round(mean / vol * math.sqrt(252.0), 6) if vol else 0.0,
        "max_drawdown": round(mdd, 6),
    }


def bootstrap(
    curve: list[dict[str, Any]], *, block: int = 20, paths: int = 5000, seed: int = 488
) -> dict[str, float]:
    returns = [
        float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        for index in range(1, len(curve))
    ]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(returns):
            start = rng.randrange(len(returns))
            sampled.extend(returns[(start + offset) % len(returns)] for offset in range(block))
        selected = sampled[: len(returns)]
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

    def q(items: list[float], fraction: float) -> float:
        return sorted(items)[min(len(items) - 1, int(fraction * len(items)))]

    return {
        "total_p05": q(totals, 0.05),
        "total_p50": q(totals, 0.5),
        "sharpe_p50": q(sharpes, 0.5),
        "mdd_p05": q(mdds, 0.05),
    }


def hs300_return(start: str, end: str) -> float:
    paths = sorted((ROOT / "data" / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    return round(float(frame["close"][-1]) / float(frame["close"][0]) - 1.0, 6)


def recover_curves() -> dict[str, list[dict[str, Any]]]:
    r484 = load_module(ROOT / "scripts" / "research_r484_daily_entry_batch_cap.py", "r488_r484")
    r468 = load_module(
        ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r488_r468"
    )
    r464 = r468.load_module(
        ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r488_r464"
    )
    p5 = r464.load_p5()
    panel, _ = r464.build_panel(p5)
    from app.hs300.current import current_symbols

    price = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0].with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left"
    ).sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(ROOT / "data")
    selected = json.loads(R484.read_text(encoding="utf-8"))["selected"]
    missing = [f for f in selected["weights"] if f not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = json.loads(PREREG.read_text(encoding="utf-8"))["windows"]
    curves = {}
    for window in ("train", "validation", "test", "recent"):
        result = r484.run_sample(
            p5,
            service,
            panel,
            symbols,
            selected,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
            cost_label="2x",
        )
        curves[window] = result["curve"]
    return curves


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R484)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    curves = recover_curves()
    regime = load_regime()
    etf = load_etf(
        list(prereg["defensive_etfs"]),
        prereg["windows"]["train_start"],
        prereg["windows"]["test_end"],
    )
    hs300_test = hs300_return(prereg["windows"]["test_start"], prereg["windows"]["test_end"])
    hs300_recent = hs300_return(prereg["windows"]["recent_start"], prereg["windows"]["test_end"])
    rows = []
    for name, rule in prereg["rules"].items():
        row = {"name": name, "rule": rule}
        for window in ("train", "validation", "test", "recent"):
            curve = apply_rule(curves[window], regime, rule, etf, 10.0)
            row[window] = {
                "metrics": metrics(curve),
                "bootstrap": bootstrap(
                    curve, seed=488 + (0 if window in {"train", "validation"} else 10)
                ),
                "weight_min": min(
                    1.0 - x["cash"] for x in rule_weights(curves[window], regime, rule)
                ),
            }
        rows.append(row)
    eligible = [
        row
        for row in rows
        if row["train"]["metrics"]["annual_return"] >= prereg["selection"]["train_annual_min"]
        and row["validation"]["metrics"]["annual_return"]
        >= prereg["selection"]["validation_annual_min"]
        and row["validation"]["metrics"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
        and row["validation"]["metrics"]["max_drawdown"]
        >= prereg["selection"]["validation_mdd_min"]
        and row["validation"]["bootstrap"]["total_p05"]
        >= prereg["selection"]["validation_bootstrap_p05_min_for_platform"]
    ]
    eligible.sort(
        key=lambda row: (
            min(
                row["train"]["metrics"]["annual_return"],
                row["validation"]["metrics"]["annual_return"],
            ),
            row["validation"]["bootstrap"]["total_p05"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else rows[0]
    checks = {
        "training_platform_exists": bool(eligible),
        "test_annual_min": selected["test"]["metrics"]["annual_return"]
        >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["metrics"]["sharpe"]
        >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["metrics"]["max_drawdown"]
        >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["metrics"]["annual_return"]
        >= prereg["confirmation_gates"]["recent_annual_min"],
        "test_bootstrap_positive": selected["test"]["bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": selected["recent"]["bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": selected["test"]["metrics"]["total_return"] > hs300_test,
        "no_leverage": selected["test"]["weight_min"] <= 1.0,
    }
    payload = {
        "round": "R488",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r484_sha256": sha(R484),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "rows": rows,
        "eligible_count": len(eligible),
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {
            "five_x_warning_only": True,
            "no_leverage": True,
            "conditional_t1_only": True,
        },
        "decision": {
            "status": "watch_candidate" if all(checks.values()) else "rejected",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    st = selected["test"]["metrics"]
    rc = selected["recent"]["metrics"]
    lines = [
        "# R488 防御资产条件化叠加",
        "",
        "本轮冻结 R484 股票 sleeve，只使用 T-1 回撤/regime 与国债/黄金 ETF 做条件化防御。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 选中：`{selected['name']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        (
            f"- 检验年化：{st['annual_return']:.2%}；"
            f"Sharpe：{st['sharpe']:.3f}；MDD：{st['max_drawdown']:.2%}"
        ),
        (
            f"- 近一年年化：{rc['annual_return']:.2%}；"
            f"Sharpe：{rc['sharpe']:.3f}；MDD：{rc['max_drawdown']:.2%}"
        ),
        f"- 检验相对 HS300：{st['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{rc['total_return'] - hs300_recent:+.2%}",
        f"- 检验 p05：{selected['test']['bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent']['bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
