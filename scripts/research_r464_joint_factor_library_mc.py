#!/usr/bin/env python3
"""R464: joint factor-library sleeve and portfolio-parameter Monte Carlo."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r464_joint_factor_library_mc_preregistration.json"
OUTPUT = E / "r464_joint_factor_library_mc_2026-11-22.json"
RESULT = E / "r464_joint_factor_library_mc_2026-11-22.md"

SLEEVE_ORDER = ("four_vq", "seven_vq", "four_vqs", "three_vq")
CORE_IDS = (
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low",
    "ext_alt_hs300_ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low",
)
COMPOSITE_IDS = ("value_quality", "value_quality_safety")
FUNDAMENTAL_IDS = ("pb_latest", "ocf_debt", "debt_ratio_latest", "accrual")
SHORT = {
    "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q",
    "ext_alt_hs300_dividend_yield": "dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio",
    "ext_alt_hs300_holder_top10_share_chg_yoy": "holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d",
    "ext_alt_hs300_ocf_margin_4q": "ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol_20d",
    "value_quality": "value_quality",
    "value_quality_safety": "value_quality_safety",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R464 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R464":
        raise RuntimeError("R464 round mismatch")
    if payload["selection"]["basis"] != "train_and_validation_only":
        raise RuntimeError("R464 selection basis changed")
    if payload["execution"]["five_x_hard_rejection"] is not False:
        raise RuntimeError("R464 five-x must remain warning-only")
    return payload


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r464_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_r453() -> Any:
    path = ROOT / "scripts" / "research_r453_value_quality_portfolio.py"
    spec = importlib.util.spec_from_file_location("r464_r453", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lhs_column(rng: random.Random, n: int) -> list[float]:
    order = list(range(n))
    rng.shuffle(order)
    return [(order[index] + rng.random()) / n for index in range(n)]


def _choose(values: list[Any], fraction: float) -> Any:
    index = min(len(values) - 1, int(fraction * len(values)))
    return values[index]


def _normalise_weights(core: list[str], composite_weight: float) -> dict[str, float]:
    composite_weight = min(0.2, max(0.05, float(composite_weight)))
    core_weight = 1.0 - composite_weight
    return {
        **{factor: core_weight / len(core) for factor in core},
        next(iter(COMPOSITE_IDS)): 0.0,
    } | {}


def sleeve_weights(sleeve: str, composite_weight: float, prereg: dict[str, Any]) -> dict[str, float]:
    spec = prereg["sleeves"][sleeve]
    core = list(spec["core"])
    composite = str(spec["composite"])
    core_weight = 1.0 - float(composite_weight)
    return {
        **{factor: core_weight / len(core) for factor in core},
        composite: float(composite_weight),
    }


def generate_samples() -> list[dict[str, Any]]:
    prereg = load_prereg()
    design = prereg["samples"]
    space = prereg["parameter_space"]
    n = int(design["latin_hypercube"])
    rng = random.Random(int(design["seed"]))
    strata = {
        "sleeve": _lhs_column(rng, n),
        "composite_weight": _lhs_column(rng, n),
        "max_positions": _lhs_column(rng, n),
        "max_hold_days": _lhs_column(rng, n),
        "max_exposure_pct": _lhs_column(rng, n),
        "entry_score": _lhs_column(rng, n),
        "exit_score": _lhs_column(rng, n),
    }
    rows: list[dict[str, Any]] = []
    baseline = prereg["r463_baseline"]
    rows.append(
        {
            "label": "r463_baseline",
            "sample": -1,
            "sleeve": baseline["sleeve"],
            "weights": sleeve_weights(baseline["sleeve"], baseline["value_quality_weight"], prereg),
            "max_positions": int(baseline["max_positions"]),
            "max_hold_days": int(baseline["max_hold_days"]),
            "max_exposure_pct": float(baseline["max_exposure_pct"]),
            "entry_score": float(baseline["entry_score"]),
            "exit_score": float(baseline["exit_score"]),
        }
    )
    for sleeve in SLEEVE_ORDER:
        rows.append(
            {
                "label": f"center_{sleeve}",
                "sample": -1,
                "sleeve": sleeve,
                "weights": sleeve_weights(sleeve, 0.10, prereg),
                "max_positions": 10,
                "max_hold_days": 25,
                "max_exposure_pct": 1.0,
                "entry_score": 70.0,
                "exit_score": 40.0,
            }
        )
    for index in range(n):
        sleeve = _choose(list(SLEEVE_ORDER), strata["sleeve"][index])
        composite_weight = _choose(list(space["value_quality_weight"]), strata["composite_weight"][index])
        max_positions = _choose(list(space["max_positions"]), strata["max_positions"][index])
        max_hold_days = _choose(list(space["max_hold_days"]), strata["max_hold_days"][index])
        max_exposure_pct = _choose(list(space["max_exposure_pct"]), strata["max_exposure_pct"][index])
        entry_score = _choose(list(space["entry_score"]), strata["entry_score"][index])
        exit_score = _choose(list(space["exit_score"]), strata["exit_score"][index])
        rows.append(
            {
                "label": f"lhs_{index:03d}",
                "sample": index,
                "sleeve": sleeve,
                "weights": sleeve_weights(sleeve, composite_weight, prereg),
                "max_positions": int(max_positions),
                "max_hold_days": int(max_hold_days),
                "max_exposure_pct": float(max_exposure_pct),
                "entry_score": float(entry_score),
                "exit_score": float(exit_score),
            }
        )
    return rows


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, list(FUNDAMENTAL_IDS))
    panel = panel.join(
        load_existing_ext_factors(
            DATA,
            {
                "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
                "ext_alt_hs300_dividend_yield": "dividend_yield",
                "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
                "ext_alt_hs300_holder_top10_share_chg_yoy": "holder_top10_share_chg_yoy",
                "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
                "ext_alt_hs300_ocf_margin_4q": "ocf_margin_4q",
                "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol_20d_low",
            },
        ),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    for factor, direction in {
        "pb_latest": "low",
        "ocf_debt": "high",
        "debt_ratio_latest": "low",
        "accrual": "low",
    }.items():
        panel = rank_score_frame(panel, factor, direction, output_column=f"_z_{factor}")
    panel = panel.with_columns(
        (
            pl.col("_z_pb_latest").fill_null(0.0)
            + pl.col("_z_ocf_debt").fill_null(0.0)
            + pl.col("_z_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_z_pb_latest").fill_null(0.0)
            + pl.col("_z_ocf_debt").fill_null(0.0)
            + pl.col("_z_debt_ratio_latest").fill_null(0.0)
            + pl.col("_z_accrual").fill_null(0.0)
        ).alias("value_quality_safety"),
    ).filter(
        pl.col("_z_pb_latest").is_not_null()
        & pl.col("_z_ocf_debt").is_not_null()
        & pl.col("_z_debt_ratio_latest").is_not_null()
        & pl.col("_z_accrual").is_not_null()
    )
    return panel, sorted(panel["symbol"].unique().to_list())


def _failed_metrics() -> dict[str, Any]:
    """Represent a no-signal sample as a failed trial, not a runner crash."""
    return {
        "stats": {
            "annual_return": -1.0,
            "total_return": -1.0,
            "max_drawdown": -1.0,
            "sharpe": -1.0,
            "sortino": -1.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "n_trades": 0.0,
            "avg_exposure": 0.0,
            "worst": -1.0,
        },
        "curve": [],
        "trades": 0,
        "error": "no_buy_signal",
    }


def _run_config(p5: Any, service: Any, panel: pl.DataFrame, symbols: list[str], sample: dict[str, Any], *, start: str, end: str, cost_label: str) -> dict[str, Any]:
    seed = next(iter(sample["weights"]))
    seed_config = p5.portfolio._backtest_config(
        {seed: 1.0},
        {seed: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=20,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=int(sample["max_hold_days"]),
    )
    seed_config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([seed_config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, list(sample["weights"]))
    if cost_label == "5x":
        cost = {
            "fees_pct": 0.001,
            "commission_pct": 0.001,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 25.0,
        }
    else:
        cost = p5.portfolio._cost_parameters(cost_label)
    config = p5.portfolio._backtest_config(
        sample["weights"],
        {factor: "high" for factor in sample["weights"]},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(sample["max_positions"]),
        cost=cost,
        max_hold_days=int(sample["max_hold_days"]),
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(sample["entry_score"]),
        "exit_score": float(sample["exit_score"]),
    }
    config.max_exposure_pct = float(sample["max_exposure_pct"])
    config.symbols = list(prepared.market_data.symbols)
    result = service.run(config, prepared=prepared)
    if result.error:
        if "未产生买入信号" in str(result.error):
            return _failed_metrics()
        raise RuntimeError(result.error)
    return {"stats": p5._select_stats(result.stats), "curve": list(result.equity_curve), "trades": len(result.trades)}


def _training_gate(sample: dict[str, Any], prereg: dict[str, Any]) -> bool:
    train = sample["train"]["stats"]
    validation = sample["validation"]["stats"]
    gate = prereg["selection"]
    return bool(
        train["annual_return"] >= gate["train_annual_min"]
        and validation["annual_return"] >= gate["validation_annual_min"]
        and validation["sharpe"] >= gate["validation_sharpe_min"]
        and validation["max_drawdown"] >= gate["validation_mdd_min"]
    )


def _validation_bootstrap(sample: dict[str, Any], prereg: dict[str, Any], r453: Any) -> dict[str, float]:
    return r453.bootstrap(
        sample["validation"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + int(sample["sample"]) + 1000,
    )


def _neighbourhoods(samples: list[dict[str, Any]], selected: dict[str, Any], prereg: dict[str, Any]) -> list[dict[str, Any]]:
    space = prereg["parameter_space"]
    axes = {
        "value_quality_weight": sorted({round(sample["weights"].get("value_quality", sample["weights"].get("value_quality_safety", 0.0)), 8) for sample in samples}),
        "max_positions": list(space["max_positions"]),
        "max_hold_days": list(space["max_hold_days"]),
        "max_exposure_pct": list(space["max_exposure_pct"]),
        "entry_score": list(space["entry_score"]),
        "exit_score": list(space["exit_score"]),
    }
    rows: list[dict[str, Any]] = []
    for axis, values in axes.items():
        for value in values:
            candidate = dict(selected)
            if axis == "value_quality_weight":
                core = {key: val for key, val in selected["weights"].items() if key not in COMPOSITE_IDS}
                composite = next(key for key in selected["weights"] if key in COMPOSITE_IDS)
                total_core = 1.0 - float(value)
                candidate["weights"] = {**{key: total_core / len(core) for key in core}, composite: float(value)}
            else:
                candidate[axis] = value
            rows.append({"axis": axis, "value": value, "sample": candidate})
    return rows


def main() -> int:
    ensure_outputs_absent()
    prereg = load_prereg()
    p5 = load_p5()
    r453 = load_r453()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    samples = generate_samples()
    for sample in samples:
        sample["train"] = _run_config(p5, service, panel, symbols, sample, start=prereg["windows"]["train_start"], end=prereg["windows"]["train_end"], cost_label="2x")
        sample["validation"] = _run_config(p5, service, panel, symbols, sample, start=prereg["windows"]["validation_start"], end=prereg["windows"]["validation_end"], cost_label="2x")
        sample["training_gate"] = _training_gate(sample, prereg)
    eligible = [sample for sample in samples if sample["training_gate"]]
    eligible.sort(key=lambda sample: (min(sample["train"]["stats"]["annual_return"], sample["validation"]["stats"]["annual_return"]), sample["validation"]["stats"]["sharpe"]), reverse=True)
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for sample in finalists:
        sample["validation_bootstrap"] = _validation_bootstrap(sample, prereg, r453)
    bootstrap_positive = [sample for sample in finalists if sample["validation_bootstrap"]["total_p05"] > 0]
    neighborhood_finalists = bootstrap_positive[: int(prereg["selection"]["neighborhood_finalists"])]
    for sample in neighborhood_finalists:
        rows = _neighbourhoods(samples, sample, prereg)
        pass_count = 0
        for item in rows:
            item["result"] = _run_config(p5, service, panel, symbols, item["sample"], start=prereg["windows"]["train_start"], end=prereg["windows"]["train_end"], cost_label="2x")
            item["validation"] = _run_config(p5, service, panel, symbols, item["sample"], start=prereg["windows"]["validation_start"], end=prereg["windows"]["validation_end"], cost_label="2x")
            item["pass"] = bool(
                item["result"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
                and item["validation"]["stats"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
            )
            pass_count += int(item["pass"])
        sample["neighbourhood"] = {
            "points": len(rows),
            "pass_count": pass_count,
            "pass_rate": pass_count / len(rows) if rows else 0.0,
            "rows": rows,
        }
    stable = [
        sample for sample in neighborhood_finalists
        if sample["neighbourhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"]
    ]
    stable.sort(key=lambda sample: (min(sample["train"]["stats"]["annual_return"], sample["validation"]["stats"]["annual_return"]), sample["validation_bootstrap"]["total_p05"]), reverse=True)
    selected = stable[0] if stable else (bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else samples[0]))
    selected["test"] = _run_config(p5, service, panel, symbols, selected, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost_label="2x")
    selected["recent"] = _run_config(p5, service, panel, symbols, selected, start=prereg["windows"]["recent_start"], end=prereg["windows"]["test_end"], cost_label="2x")
    selected["test_3x"] = _run_config(p5, service, panel, symbols, selected, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost_label="3x")
    selected["test_5x"] = _run_config(p5, service, panel, symbols, selected, start=prereg["windows"]["test_start"], end=prereg["windows"]["test_end"], cost_label="5x")
    selected["test_bootstrap"] = r453.bootstrap(selected["test"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 1)
    selected["recent_bootstrap"] = r453.bootstrap(selected["recent"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 2)
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0,
        "neighborhood_platform_pass": bool(selected.get("neighbourhood")) and selected["neighbourhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"],
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "no_leverage": all(sample["max_exposure_pct"] <= 1.0 for sample in samples),
    }
    payload = {
        "round": "R464",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "sample_count": len(samples),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "neighborhood_stable_count": len(stable),
        "samples": samples,
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True, "test_5x_stats": selected["test_5x"]["stats"]},
        "decision": {
            "status": "watch_candidate" if all(checks.values()) else "rejected",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    for sample in payload["samples"]:
        if "train" in sample:
            sample["train"].pop("curve", None)
        if "validation" in sample:
            sample["validation"].pop("curve", None)
        if "neighbourhood" in sample:
            for item in sample["neighbourhood"]["rows"]:
                item.get("result", {}).pop("curve", None)
                item.get("validation", {}).pop("curve", None)
    for key in ("train", "validation", "test", "recent", "test_3x", "test_5x"):
        if key in payload["selected"]:
            payload["selected"][key].pop("curve", None)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R464 因子库联合蒙特卡洛",
        "",
        "本轮把已准入因子袖、R452 价值质量复合因子和 R463 近候选放进同一套预注册联合抽样；训练+验证只用于选样，检验+近一年只做确认。",
        "",
        f"- 样本数：{len(samples)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 邻域稳定：{len(stable)}",
        f"- 选中：`{selected['label']}` / {selected['sleeve']} / {selected['max_positions']} 仓 / {selected['max_hold_days']} 日 / 暴露 {selected['max_exposure_pct']:.0%}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}",
        f"- 检验 Sharpe：{selected['test']['stats']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['stats']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['stats']['annual_return']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 选中参数", "", f"- 权重：{json.dumps(selected['weights'], ensure_ascii=False, sort_keys=True)}", f"- 入场/退出：{selected['entry_score']:.0f} / {selected['exit_score']:.0f}", f"- 3× 检验年化：{selected['test_3x']['stats']['annual_return']:.2%}", f"- 5× warning 检验年化：{selected['test_5x']['stats']['annual_return']:.2%}", "", "## 边界", ""]
    lines += [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
