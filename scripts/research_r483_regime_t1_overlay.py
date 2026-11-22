#!/usr/bin/env python3
"""R483: strict T-1 market-regime exposure overlays on the frozen R480 sleeve."""
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
PREREG = E / "r483_regime_t1_overlay_preregistration.json"
OUTPUT = E / "r483_regime_t1_overlay_2026-11-22.json"
RESULT = E / "r483_regime_t1_overlay_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R483 output exists; refusing overwrite: {path.name}")


def load_regime() -> dict[str, dict[str, Any]]:
    frame = pl.read_parquet(ROOT / "data" / "regime_history" / "part.parquet").sort("date")
    output = {}
    for row in frame.select(["date", "state", "phase", "score"]).iter_rows(named=True):
        output[str(row["date"])[:10]] = {
            "state": str(row["state"]) if row["state"] is not None else None,
            "phase": str(row["phase"]) if row["phase"] is not None else None,
            "score": float(row["score"]) if row["score"] is not None else None,
        }
    return output


def lookup_previous_day(day: str, regime: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    dates = sorted(day_key for day_key in regime if day_key < day)
    return regime[dates[-1]] if dates else None


def overlay_weights(curve: list[dict[str, Any]], regime: dict[str, dict[str, Any]], spec: dict[str, Any]) -> list[float]:
    if not curve:
        return []
    kind = str(spec.get("type") or "none")
    if kind == "none":
        return [1.0] * len(curve)
    normal = 1.0
    weights = []
    for item in curve:
        previous = lookup_previous_day(str(item["date"])[:10], regime)
        if previous is None:
            weight = 0.0
        elif kind == "state_exclude":
            weight = float(spec["weight"]) if previous["state"] in set(spec["states"]) else normal
        elif kind == "state_include":
            weight = normal if previous["state"] in set(spec["states"]) else float(spec["defensive_weight"])
        elif kind == "phase_exclude":
            weight = float(spec["weight"]) if previous["phase"] in set(spec["phases"]) else normal
        elif kind == "score_guard":
            weight = float(spec["defensive_weight"]) if previous["score"] is not None and previous["score"] < float(spec["threshold"]) else normal
        else:
            raise ValueError(f"unsupported overlay type: {kind}")
        if not 0.0 <= weight <= 1.0:
            raise ValueError("overlay weight must be within [0,1]")
        weights.append(weight)
    return weights


def apply_overlay(curve: list[dict[str, Any]], weights: list[float]) -> list[dict[str, Any]]:
    if len(curve) != len(weights):
        raise ValueError("overlay weight count must match curve")
    output = [{"date": str(curve[0]["date"])[:10], "value": 1.0}]
    value = 1.0
    previous_weight = weights[0]
    for index in range(1, len(curve)):
        ret = float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        value *= 1.0 + previous_weight * ret
        if weights[index] != previous_weight:
            value *= 1.0 - abs(weights[index] - previous_weight) * 0.0005
        output.append({"date": str(curve[index]["date"])[:10], "value": value})
        previous_weight = weights[index]
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
        "annual_return": round((1.0 + total) ** (252.0 / len(returns)) - 1.0, 6) if returns else 0.0,
        "sharpe": round(mean / vol * math.sqrt(252.0), 6) if vol else 0.0,
        "max_drawdown": round(mdd, 6),
    }


def block_bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0 for index in range(1, len(curve))]
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
    return {"total_p05": ordered(totals, 0.05), "total_p50": ordered(totals, 0.5), "sharpe_p50": ordered(sharpes, 0.5), "mdd_p05": ordered(mdds, 0.05)}


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
    import importlib.util
    import sys
    p = ROOT / "scripts" / "research_r480_winner_dependence_budget.py"
    spec = importlib.util.spec_from_file_location("r483_r480", p)
    assert spec is not None and spec.loader is not None
    r480 = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = r480
    spec.loader.exec_module(r480)
    r468_spec = importlib.util.spec_from_file_location("r483_r468", ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py")
    assert r468_spec is not None and r468_spec.loader is not None
    r468 = importlib.util.module_from_spec(r468_spec)
    sys.modules[r468_spec.name] = r468
    r468_spec.loader.exec_module(r468)
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r483_r464")
    p5 = r464.load_p5()
    panel, _ignored = r464.build_panel(p5)
    from app.hs300.current import current_symbols
    price_panel = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(price_panel.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left").sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(ROOT / "data")
    selected = json.loads((E / "r480_winner_dependence_budget_2026-11-22.json").read_text(encoding="utf-8"))["selected"]
    missing = [factor for factor in selected["weights"] if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    windows = prereg["windows"]
    curves = {}
    for window in ("train", "validation", "test", "recent"):
        result = r480.run_sample(
            p5, service, panel, symbols, selected,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
            cost_label="2x",
        )
        curves[window] = result["curve"]
    return curves


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    regime = load_regime()
    curves = recover_curves()
    hs300_test = hs300_return(prereg["windows"]["test_start"], prereg["windows"]["test_end"])
    hs300_recent = hs300_return(prereg["windows"]["recent_start"], prereg["windows"]["test_end"])
    rows = []
    for name, spec in prereg["overlays"].items():
        row = {"name": name, "overlay": spec}
        for window in ("train", "validation", "test", "recent"):
            weights = overlay_weights(curves[window], regime, spec)
            overlaid = apply_overlay(curves[window], weights)
            row[window] = {
                "metrics": metrics(overlaid),
                "bootstrap": block_bootstrap(overlaid, block=20, paths=5000, seed=int(prereg["bootstrap"]["seed"]) + (0 if window in {"train", "validation"} else 10)),
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
    eligible.sort(key=lambda row: (min(row["train"]["metrics"]["annual_return"], row["validation"]["metrics"]["annual_return"]), row["validation"]["bootstrap"]["total_p05"]), reverse=True)
    selected = eligible[0] if eligible else rows[0]
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
        "round": "R483",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "rows": rows,
        "eligible_count": len(eligible),
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True, "regime_shift_days": 1},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R483 T-1 市场状态叠加",
        "",
        "本轮冻结 R480 的 8 腿组合，只使用前一交易日 regime 状态调整现金/权益暴露。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 选中：`{selected['name']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['metrics']['annual_return']:.2%}；Sharpe：{selected['test']['metrics']['sharpe']:.3f}；MDD：{selected['test']['metrics']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['metrics']['annual_return']:.2%}；Sharpe：{selected['recent']['metrics']['sharpe']:.3f}；MDD：{selected['recent']['metrics']['max_drawdown']:.2%}",
        f"- 检验相对 HS300：{selected['test']['metrics']['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{selected['recent']['metrics']['total_return'] - hs300_recent:+.2%}",
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
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
