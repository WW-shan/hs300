#!/usr/bin/env python3
"""R514: trailing-return dynamic risk budget on frozen R506 cap-1."""

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

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r514_r506_dynamic_risk_budget_preregistration.json"
R506 = E / "r506_r499_cap1_confirmation_2026-11-22.json"
OUTPUT = E / "r514_r506_dynamic_risk_budget_2026-11-22.json"
RESULT = E / "r514_r506_dynamic_risk_budget_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R514 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
                window = returns[max(0, index - int(spec["lookback_days"])) : index]
                mean = sum(window) / len(window)
                vol = (sum((item - mean) ** 2 for item in window) / len(window)) ** 0.5
                annual_vol = vol * math.sqrt(252.0)
                weight = (
                    maximum
                    if annual_vol <= 1e-12
                    else min(maximum, max(minimum, float(spec["annual_target"]) / annual_vol))
                )
        elif kind == "drawdown_guard":
            peak = max(peak, float(curve[index - 1]["value"]))
            drawdown = float(curve[index - 1]["value"]) / peak - 1.0
            weight = (
                float(spec["defensive_weight"])
                if drawdown <= float(spec["threshold"])
                else float(spec["normal_weight"])
            )
        else:
            raise ValueError(f"unsupported overlay type: {kind}")
        weight = min(maximum, max(minimum, weight))
        if abs(weight - previous_weight) > 1e-12:
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
        "annual_return": round((1.0 + total) ** (252.0 / len(returns)) - 1.0, 6)
        if returns and total > -1.0
        else -1.0,
        "sharpe": round(mean / vol * math.sqrt(252.0), 6) if vol else 0.0,
        "max_drawdown": round(mdd, 6),
    }


def block_bootstrap(
    curve: list[dict[str, Any]], *, seed: int, block: int = 20, paths: int = 5000
) -> dict[str, float]:
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
        product = math.prod(1.0 + value for value in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((value - mean) ** 2 for value in selected) / len(selected)) ** 0.5
        path = [1.0]
        for value in selected:
            path.append(path[-1] * (1.0 + value))
        peak = path[0]
        mdd = 0.0
        for value in path:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "mdd_p05": ordered(mdds, 0.05),
    }


def hs300_return(start: str, end: str) -> float:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
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


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r514_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r514_vq_pb_latest").fill_null(0.0)
            + pl.col("_r514_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r514_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r514_vq_pb_latest").is_not_null()
            & pl.col("_r514_vq_ocf_debt").is_not_null()
            & pl.col("_r514_vq_debt_ratio_latest").is_not_null()
        ).alias("_r514_vq_valid"),
    ).filter(pl.col("_r514_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_curve(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    prereg: dict[str, Any],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> list[dict[str, Any]]:
    from app.factor_research.incremental import build_rank_composite

    weights = {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }
    field = "_r514_r506"
    work, _ = build_rank_composite(
        panel, weights, {member: "high" for member in weights}, field, minimum_weight_coverage=0.75
    )
    fixed = {
        "max_positions": 10,
        "max_hold_days": 25,
        "entry_score": 70.0,
        "exit_score": 40.0,
        "top_rank": 20,
        "max_new_positions_per_day": 1,
        "max_exposure_pct": 1.0,
    }
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(fixed["max_positions"]),
        cost=p5.portfolio._cost_parameters(cost_label),
        max_hold_days=int(fixed["max_hold_days"]),
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(fixed["entry_score"]),
        "exit_score": float(fixed["exit_score"]),
        "top_rank": int(fixed["top_rank"]),
    }
    config.max_exposure_pct = float(fixed["max_exposure_pct"])
    config.max_new_positions_per_day = int(fixed["max_new_positions_per_day"])
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, work, [field])
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return list(result.equity_curve)


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R506)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(R506.read_text(encoding="utf-8"))
    if source["selected_variant"] != "cap_1":
        raise RuntimeError("R506 cap-1 changed")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r514_r453")
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    windows = prereg["windows"]
    base_curves = {}
    for window in ("train", "validation", "test", "recent"):
        base_curves[window] = run_curve(
            p5,
            service,
            panel,
            symbols,
            prereg,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
            cost_label="2x",
        )
    rows = []
    for name, spec in prereg["overlays"].items():
        row = {"name": name, "overlay": spec}
        for window in ("train", "validation"):
            weights = overlay_weights(base_curves[window], spec)
            overlaid = apply_overlay(base_curves[window], weights)
            row[window] = {
                "metrics": metrics(overlaid),
                "curve": overlaid,
                "bootstrap": block_bootstrap(overlaid, seed=514 + len(rows)),
                "weight_mean": round(sum(weights) / len(weights), 6),
                "weight_max": max(weights),
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
            row["validation"]["weight_mean"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else rows[0]
    for key, start_date, cost in (
        ("test", windows["test_start"], "2x"),
        ("recent", windows["recent_start"], "2x"),
        ("test_3x", windows["test_start"], "3x"),
    ):
        curve = run_curve(
            p5,
            service,
            panel,
            symbols,
            prereg,
            start=start_date,
            end=windows["test_end"],
            cost_label=cost,
        )
        weights = overlay_weights(curve, selected["overlay"])
        selected[key] = {
            "metrics": metrics(apply_overlay(curve, weights)),
            "curve": apply_overlay(curve, weights),
            "bootstrap": block_bootstrap(apply_overlay(curve, weights), seed=515),
            "weight_mean": round(sum(weights) / len(weights), 6),
            "weight_max": max(weights),
        }
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected["validation"]["bootstrap"]["total_p05"] > 0.0,
        "test_annual_min": selected.get("test", {}).get("metrics", {}).get("annual_return", -1.0)
        >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected.get("test", {}).get("metrics", {}).get("sharpe", -1.0)
        >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected.get("test", {}).get("metrics", {}).get("max_drawdown", -1.0)
        >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected.get("recent", {})
        .get("metrics", {})
        .get("annual_return", -1.0)
        >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected.get("test_3x", {})
        .get("metrics", {})
        .get("annual_return", -1.0)
        > 0.0,
        "test_bootstrap_positive": selected.get("test", {})
        .get("bootstrap", {})
        .get("total_p05", -1.0)
        > 0.0,
        "recent_bootstrap_positive": selected.get("recent", {})
        .get("bootstrap", {})
        .get("total_p05", -1.0)
        > 0.0,
        "test_excess_hs300_positive": selected.get("test", {})
        .get("metrics", {})
        .get("total_return", -1.0)
        > hs300_test,
        "no_leverage": selected["validation"]["weight_max"] <= 1.0,
    }
    payload = {
        "round": "R514",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r506_sha256": sha(R506),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "candidates": [
            {
                "name": row["name"],
                "overlay": row["overlay"],
                "train": {key: value for key, value in row["train"].items() if key != "curve"},
                "validation": {
                    key: value for key, value in row["validation"].items() if key != "curve"
                },
                "selected": row is selected,
            }
            for row in rows
        ],
        "eligible_count": len(eligible),
        "selected": selected,
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {
            "status": "watch_candidate" if all(checks.values()) else "rejected",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    ).encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = (
        [
            "# R514 R506 动态风险预算",
            "",
            f"- 选中：`{selected['name']}`",
            f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
            "",
            "## 门槛",
            "",
        ]
        + [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
        + ["", "## 边界", ""]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
