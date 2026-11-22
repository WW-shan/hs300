#!/usr/bin/env python3
"""R499: weak-state value-quality tilt on the frozen R463 five-leg portfolio."""

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
PREREG = E / "r499_weak_state_value_quality_tilt_preregistration.json"
R498 = E / "r498_drawdown_conditional_attribution_2026-11-22.json"
OUTPUT = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
RESULT = E / "r499_weak_state_value_quality_tilt_2026-11-22.md"
ADMITTED = (
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_idio_vol_40d_low",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R499 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def base_weights() -> dict[str, float]:
    return {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.225,
        "ext_alt_hs300_dividend_yield": 0.225,
        "ext_alt_hs300_fund_holding_ratio_low": 0.225,
        "ext_alt_hs300_idio_vol_40d_low": 0.225,
        "value_quality": 0.1,
    }


def weak_weights(weak_value_quality: float) -> dict[str, float]:
    if not 0.0 <= weak_value_quality <= 0.1:
        raise ValueError("weak value-quality weight must be within [0, 0.1]")
    admitted_weight = (1.0 - weak_value_quality) / len(ADMITTED)
    return {member: admitted_weight for member in ADMITTED} | {"value_quality": weak_value_quality}


def block_bootstrap(
    curve: list[dict[str, Any]], *, block: int = 20, paths: int = 5000, seed: int = 499
) -> dict[str, float]:
    values = [
        float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]
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


def weak_state_map(prereg: dict[str, Any]) -> dict[str, bool]:
    from scripts_support import noop  # type: ignore[import-not-found]

    del noop


def build_conditional_scores(
    panel: pl.DataFrame, weak_dates: set[str], weak_value_quality: float
) -> pl.DataFrame:
    from app.factor_research.incremental import rank_score_frame

    work = panel
    for member in ADMITTED:
        work = rank_score_frame(work, member, "high", output_column=f"_r499_{member}")
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        work = rank_score_frame(work, member, direction, output_column=f"_r499_vq_{member}")
    work = work.with_columns(
        (
            pl.col("_r499_vq_pb_latest").fill_null(0.0)
            + pl.col("_r499_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r499_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("_r499_value_quality"),
        (
            pl.col("_r499_vq_pb_latest").is_not_null()
            & pl.col("_r499_vq_ocf_debt").is_not_null()
            & pl.col("_r499_vq_debt_ratio_latest").is_not_null()
        ).alias("_r499_vq_valid"),
    )
    base = base_weights()
    tilted = weak_weights(weak_value_quality)
    day = pl.col("date").cast(pl.Utf8).str.slice(0, 10)
    score = pl.lit(0.0)
    coverage = pl.lit(0)
    for member in ADMITTED:
        weight = (
            pl.when(day.is_in(list(weak_dates)))
            .then(pl.lit(tilted[member]))
            .otherwise(pl.lit(base[member]))
        )
        score = score + pl.col(f"_r499_{member}").fill_null(0.0) * weight
        coverage = coverage + pl.col(f"_r499_{member}").is_not_null().cast(pl.Int8)
    vq_weight = (
        pl.when(day.is_in(list(weak_dates)))
        .then(pl.lit(tilted["value_quality"]))
        .otherwise(pl.lit(base["value_quality"]))
    )
    score = score + pl.col("_r499_value_quality").fill_null(0.0) * vq_weight
    coverage = coverage + pl.col("_r499_vq_valid").cast(pl.Int8)
    return work.with_columns(
        pl.when((coverage >= 4) & (vq_weight > 0) | ((coverage >= 4) & (vq_weight == 0)))
        .then(score / 5.0)
        .otherwise(None)
        .alias("_r499_conditional_composite")
    )


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
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
    return panel, sorted(panel["symbol"].unique().to_list())


def run_sample(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    weak_dates: set[str],
    weak_value_quality: float,
    prereg: dict[str, Any],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    field = "_r499_conditional_composite"
    work = build_conditional_scores(panel, weak_dates, weak_value_quality)
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(prereg["fixed_execution"]["max_positions"]),
        cost=(
            p5.portfolio._cost_parameters(cost_label)
            if cost_label != "5x"
            else {
                "fees_pct": 0.001,
                "commission_pct": 0.001,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 25.0,
            }
        ),
        max_hold_days=int(prereg["fixed_execution"]["max_hold_days"]),
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(prereg["fixed_execution"]["entry_score"]),
        "exit_score": float(prereg["fixed_execution"]["exit_score"]),
        "top_rank": int(prereg["fixed_execution"]["top_rank"]),
    }
    config.max_exposure_pct = float(prereg["fixed_execution"]["max_exposure_pct"])
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, work, [field])
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R498)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r498 = json.loads(R498.read_text(encoding="utf-8"))
    threshold = r498["weak_state"]
    if float(threshold["score_threshold"]) != float(prereg["weak_state"]["score_threshold"]):
        raise RuntimeError("R498 weak threshold changed")

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r499_r453")
    r498_mod = load_module(
        ROOT / "scripts" / "research_r498_drawdown_conditional_attribution.py", "r499_r498"
    )
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    windows = prereg["windows"]

    regime = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Utf8))
        .select(["date", "score", "above_ma20_pct"])
    )
    labelled = r498_mod.apply_weak_state(regime.to_dicts(), threshold)
    weak_dates = {str(row["date"])[:10] for row in labelled if row["weak"]}

    candidates = []
    for weak_value_quality in prereg["weak_value_quality_candidates"]:
        row = {
            "weak_value_quality": float(weak_value_quality),
            "weights": weak_weights(float(weak_value_quality)),
        }
        for window in ("train", "validation"):
            row[window] = run_sample(
                p5,
                service,
                panel,
                symbols,
                weak_dates,
                float(weak_value_quality),
                prereg,
                start=windows[f"{window}_start"],
                end=windows[f"{window}_end"],
                cost_label="2x",
            )
        row["validation_bootstrap"] = block_bootstrap(
            row["validation"]["curve"],
            block=int(prereg["bootstrap"]["block_days"]),
            paths=int(prereg["bootstrap"]["replications"]),
            seed=int(prereg["bootstrap"]["seed"]) + int(float(weak_value_quality) * 1000),
        )
        candidates.append(row)

    eligible = [
        row
        for row in candidates
        if row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
        and row["validation"]["stats"]["annual_return"]
        >= prereg["selection"]["validation_annual_min"]
        and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
        and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        and row["validation_bootstrap"]["total_p05"] > 0.0
    ]
    eligible.sort(
        key=lambda row: (
            min(
                row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]
            ),
            row["validation_bootstrap"]["total_p05"],
            -row["weak_value_quality"],
        ),
        reverse=True,
    )
    selected = (
        eligible[0]
        if eligible
        else max(
            candidates,
            key=lambda row: min(
                row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]
            ),
        )
    )
    selected["test"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weak_dates,
        float(selected["weak_value_quality"]),
        prereg,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="2x",
    )
    selected["recent"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weak_dates,
        float(selected["weak_value_quality"]),
        prereg,
        start=windows["recent_start"],
        end=windows["test_end"],
        cost_label="2x",
    )
    selected["test_3x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weak_dates,
        float(selected["weak_value_quality"]),
        prereg,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="3x",
    )
    selected["test_5x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weak_dates,
        float(selected["weak_value_quality"]),
        prereg,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="5x",
    )
    selected["test_bootstrap"] = block_bootstrap(
        selected["test"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 2,
    )
    selected["recent_bootstrap"] = block_bootstrap(
        selected["recent"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 3,
    )
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected["validation_bootstrap"]["total_p05"] > 0.0,
        "test_annual_min": selected["test"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"]
        >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"]
        >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_bootstrap_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": selected["test"]["stats"]["total_return"] > hs300_test,
        "no_leverage": selected["weights"]["value_quality"] < 1.0,
    }

    payload = {
        "round": "R499",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r498_sha256": sha(R498),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "weak_threshold": threshold,
        "candidates": [
            {
                **row,
                "train": {key: value for key, value in row["train"].items() if key != "curve"},
                "validation": {
                    key: value for key, value in row["validation"].items() if key != "curve"
                },
                "selected": row is selected,
            }
            for row in candidates
        ],
        "eligible_count": len(eligible),
        "selected": {
            **selected,
            "test": {key: value for key, value in selected["test"].items() if key != "curve"},
            "recent": {key: value for key, value in selected["recent"].items() if key != "curve"},
            "test_3x": {key: value for key, value in selected["test_3x"].items() if key != "curve"},
            "test_5x": {key: value for key, value in selected["test_5x"].items() if key != "curve"},
        },
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
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
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )

    st = selected["test"]["stats"]
    rc = selected["recent"]["stats"]
    lines = [
        "# R499 弱市价值质量降权确认",
        "",
        "本轮固定 R463 组合，只在 R498 冻结的弱市状态下降低 `value_quality` 权重。",
        "",
        f"- 弱市 `value_quality` 权重：{selected['weak_value_quality']:.1%}",
        f"- 弱市其余四腿权重：各 {selected['weights']['ext_alt_hs300_dividend_yield']:.2%}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{st['annual_return']:.2%}；Sharpe：{st['sharpe']:.3f}；MDD：{st['max_drawdown']:.2%}",
        f"- 近一年年化：{rc['annual_return']:.2%}；Sharpe：{rc['sharpe']:.3f}；MDD：{rc['max_drawdown']:.2%}",
        f"- 检验相对 HS300：{st['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{rc['total_return'] - hs300_recent:+.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
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
