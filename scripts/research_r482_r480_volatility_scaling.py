#!/usr/bin/env python3
"""R482: no-leverage volatility-scaling overlays on the frozen R480 sleeve."""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r482_r480_volatility_scaling_preregistration.json"
R480 = E / "r480_winner_dependence_budget_2026-11-22.json"
OUTPUT = E / "r482_r480_volatility_scaling_2026-11-22.json"
RESULT = E / "r482_r480_volatility_scaling_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R482 output exists; refusing overwrite: {path.name}")


def curve_returns(curve: list[dict[str, Any]]) -> list[float]:
    return [
        float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def overlay_weights(curve: list[dict[str, Any]], spec: dict[str, Any]) -> list[float]:
    if not curve:
        return []
    kind = str(spec.get("type") or "none")
    if kind == "none":
        return [1.0] * len(curve)
    minimum = float(spec.get("min_weight", 0.0))
    maximum = float(spec.get("max_weight", 1.0))
    if not 0.0 <= minimum <= maximum <= 1.0:
        raise ValueError("overlay weights must satisfy 0 <= min <= max <= 1")
    returns = curve_returns(curve)
    weights = [minimum]
    previous_weight = minimum
    peak = float(curve[0]["value"])
    for index in range(1, len(curve)):
        if kind == "vol_target":
            if index <= int(spec["lookback_days"]):
                weight = minimum
            else:
                window = returns[max(0, index - int(spec["lookback_days"])):index]
                mean = sum(window) / len(window)
                vol = (sum((item - mean) ** 2 for item in window) / len(window)) ** 0.5
                annual_vol = vol * math.sqrt(252.0)
                weight = maximum if annual_vol <= 1e-12 else min(maximum, max(minimum, float(spec["annual_target"]) / annual_vol))
        elif kind == "drawdown_guard":
            peak = max(peak, float(curve[index - 1]["value"]))
            drawdown = float(curve[index - 1]["value"]) / peak - 1.0
            weight = float(spec["defensive_weight"]) if drawdown <= float(spec["threshold"]) else float(spec["normal_weight"])
        else:
            raise ValueError(f"unsupported overlay type: {kind}")
        weight = min(maximum, max(minimum, weight))
        if abs(weight - previous_weight) > 1e-12:
            # 5 bp round-trip cost for a weight change, charged on turnover.
            previous_weight = weight
        weights.append(weight)
    return weights


def apply_overlay(curve: list[dict[str, Any]], weights: list[float]) -> list[dict[str, Any]]:
    if len(curve) != len(weights):
        raise ValueError("overlay weight count must match curve")
    returns = curve_returns(curve)
    value = 1.0
    output = [{"date": str(curve[0]["date"])[:10], "value": 1.0}]
    previous_weight = weights[0]
    for index, item_return in enumerate(returns, start=1):
        value *= 1.0 + previous_weight * item_return
        if weights[index] != previous_weight:
            value *= 1.0 - abs(weights[index] - previous_weight) * 0.0005
        output.append({"date": str(curve[index]["date"])[:10], "value": value})
        previous_weight = weights[index]
    return output


def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    returns = curve_returns(curve)
    total = values[-1] / values[0] - 1.0 if values else 0.0
    mean = sum(returns) / len(returns) if returns else 0.0
    vol = (sum((item - mean) ** 2 for item in returns) / len(returns)) ** 0.5 if returns else 0.0
    peak = values[0] if values else 1.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": round(total, 6),
        "annual_return": round((1.0 + total) ** (252.0 / len(returns)) - 1.0, 6) if returns and total > -1.0 else -1.0,
        "sharpe": round(mean / vol * math.sqrt(252.0), 6) if vol else 0.0,
        "max_drawdown": round(mdd, 6),
    }


def block_bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = curve_returns(curve)
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled: list[float] = []
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
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.5),
        "sharpe_p50": ordered(sharpes, 0.5),
        "mdd_p05": ordered(mdds, 0.05),
    }


def load_frozen_curves() -> dict[str, list[dict[str, Any]]]:
    """Re-run only the frozen R480 sleeve to recover the curves omitted from its compact evidence."""
    import importlib.util
    import sys

    root = ROOT
    path = root / "scripts" / "research_r480_winner_dependence_budget.py"
    spec = importlib.util.spec_from_file_location("r482_r480_runner", path)
    assert spec is not None and spec.loader is not None
    r480_runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = r480_runner
    spec.loader.exec_module(r480_runner)
    r468 = importlib.util.spec_from_file_location("r482_r468", root / "scripts" / "research_r468_operating_efficiency_sleeve.py")
    assert r468 is not None and r468.loader is not None
    r468_module = importlib.util.module_from_spec(r468)
    sys.modules[r468.name] = r468_module
    r468.loader.exec_module(r468_module)
    r464 = r468_module.load_module(root / "scripts" / "research_r464_joint_factor_library_mc.py", "r482_r464")
    p5 = r464.load_p5()
    panel, _ignored = r464.build_panel(p5)
    from app.hs300.current import current_symbols
    price_panel = p5.load_candidate_panel(root / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price_panel.select(["symbol", "date", "momentum_60d"]),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(root / "data")
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    r480 = json.loads(R480.read_text(encoding="utf-8"))
    selected = r480["selected"]
    missing = [factor for factor in selected["weights"] if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    missing = [factor for factor in selected["weights"] if factor not in panel.columns]
    if missing:
        raise RuntimeError(f"frozen factors unavailable: {missing}")
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = json.loads(PREREG.read_text(encoding="utf-8"))["windows"]
    curves: dict[str, list[dict[str, Any]]] = {}
    for window in ("train", "validation", "test", "recent"):
        result = r480_runner.run_sample(
            p5, service, panel, symbols, selected,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
            cost_label="2x",
        )
        curves[window] = result["curve"]
    return curves


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
    if frame.is_empty():
        raise RuntimeError("HS300 benchmark has no rows")
    return round(float(frame["close"][-1]) / float(frame["close"][0]) - 1.0, 6)


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R480)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r480 = json.loads(R480.read_text(encoding="utf-8"))
    if r480["selected"]["label"] != prereg["source_selected_label"]:
        raise RuntimeError("R482 source sample changed")
    curves = load_frozen_curves()
    rows = []
    for name, spec in prereg["overlays"].items():
        row = {"name": name, "overlay": spec}
        for window in ("train", "validation", "test", "recent"):
            curve = curves[window]
            weights = overlay_weights(curve, spec)
            overlaid = apply_overlay(curve, weights)
            row[window] = {
                "metrics": metrics(overlaid),
                "bootstrap": block_bootstrap(
                    overlaid,
                    block=int(prereg["bootstrap"]["block_days"]),
                    paths=int(prereg["bootstrap"]["replications"]),
                    seed=int(prereg["bootstrap"]["seed"]) + (0 if window in {"train", "validation"} else 10),
                ),
                "weight_min": min(weights),
                "weight_max": max(weights),
                "weight_mean": round(sum(weights) / len(weights), 6),
            }
        rows.append(row)
    eligible = [
        row for row in rows
        if row["train"]["metrics"]["annual_return"] >= prereg["selection"]["train_annual_min"]
        and row["validation"]["metrics"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
        and row["validation"]["metrics"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
        and row["validation"]["metrics"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        and row["validation"]["bootstrap"]["total_p05"] >= prereg["selection"]["validation_bootstrap_p05_min_for_platform"]
    ]
    eligible.sort(
        key=lambda row: (
            min(row["train"]["metrics"]["annual_return"], row["validation"]["metrics"]["annual_return"]),
            row["validation"]["bootstrap"]["total_p05"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else rows[0]
    hs300_test = hs300_return(prereg["windows"]["test_start"], prereg["windows"]["test_end"])
    hs300_recent = hs300_return(prereg["windows"]["recent_start"], prereg["windows"]["test_end"])
    checks = {
        "training_platform_exists": bool(eligible),
        "test_annual_min": selected["test"]["metrics"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["metrics"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["metrics"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["metrics"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "test_bootstrap_positive": selected["test"]["bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": selected["recent"]["bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": selected["test"]["metrics"]["total_return"] > hs300_test,
        "no_leverage": selected["test"]["weight_max"] <= 1.0,
    }
    payload = {
        "round": "R482",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r480_sha256": sha(R480),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "sample_count": len(rows),
        "eligible_count": len(eligible),
        "rows": rows,
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True},
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
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R482 R480 组合波动率缩放",
        "",
        "本轮冻结 R480 的 8 腿组合，只对已实现收益序列做无杠杆现金/权益缩放；训练+验证选择，检验+近一年确认。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 选中：`{selected['name']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['metrics']['annual_return']:.2%}；Sharpe：{selected['test']['metrics']['sharpe']:.3f}；MDD：{selected['test']['metrics']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['metrics']['annual_return']:.2%}；Sharpe：{selected['recent']['metrics']['sharpe']:.3f}；MDD：{selected['recent']['metrics']['max_drawdown']:.2%}",
        f"- 检验相对 HS300：{selected['test']['metrics']['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{selected['recent']['metrics']['total_return'] - hs300_recent:+.2%}",
        f"- 检验 bootstrap p05：{selected['test']['bootstrap']['total_p05']:.2%}",
        f"- 近一年 bootstrap p05：{selected['recent']['bootstrap']['total_p05']:.2%}",
        f"- 权重范围：{selected['test']['weight_min']:.2f}..{selected['test']['weight_max']:.2f}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
