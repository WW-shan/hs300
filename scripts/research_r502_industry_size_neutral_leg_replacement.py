#!/usr/bin/env python3
"""R502: bounded replacement audit for weak R499 neutral legs."""

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
PREREG = E / "r502_industry_size_neutral_leg_replacement_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
R501 = E / "r501_r499_industry_size_neutral_attribution_2026-11-22.json"
OUTPUT = E / "r502_industry_size_neutral_leg_replacement_2026-11-22.json"
RESULT = E / "r502_industry_size_neutral_leg_replacement_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R502 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def base_weights() -> dict[str, float]:
    return {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }


def replaced_weights(remove: str, add: str) -> dict[str, float]:
    weights = base_weights()
    if remove not in weights:
        raise ValueError(f"replacement source is not in base: {remove}")
    if add in weights:
        raise ValueError(f"replacement target already exists: {add}")
    weights = {key: value for key, value in weights.items() if key != remove}
    weights[add] = base_weights()[remove]
    if abs(sum(weights.values()) - 1.0) > 1e-12:
        raise ValueError("replacement weights must sum to one")
    return weights


def block_bootstrap(
    curve: list[dict[str, Any]], *, block: int = 20, paths: int = 5000, seed: int = 502
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
            "ext_alt_hs300_ocf_margin_4q": "ocf_margin_4q",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r502_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r502_vq_pb_latest").fill_null(0.0)
            + pl.col("_r502_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r502_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r502_vq_pb_latest").is_not_null()
            & pl.col("_r502_vq_ocf_debt").is_not_null()
            & pl.col("_r502_vq_debt_ratio_latest").is_not_null()
        ).alias("_r502_vq_valid"),
    ).filter(pl.col("_r502_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_sample(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    weights: dict[str, float],
    prereg: dict[str, Any],
    weak_dates: set[str],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    field = "_r502_conditional_composite"
    work = panel
    from app.factor_research.incremental import build_rank_composite

    work, _ = build_rank_composite(
        work, weights, prereg["directions"], field, minimum_weight_coverage=0.75
    )
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
    del weak_dates
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    verify_sidecar(R501)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r499 = json.loads(R499.read_text(encoding="utf-8"))
    if float(r499["selected"]["weak_value_quality"]) != 0.05:
        raise RuntimeError("R499 selected sample changed")
    r501 = json.loads(R501.read_text(encoding="utf-8"))
    if r501["neutralization"]["industry"] != "industry_l1":
        raise RuntimeError("R501 neutralization changed")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r502_r453")
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
    r498_mod = load_module(
        ROOT / "scripts" / "research_r498_drawdown_conditional_attribution.py", "r502_r498"
    )
    labelled = r498_mod.apply_weak_state(regime.to_dicts(), r499["weak_threshold"])
    weak_dates = {str(row["date"])[:10] for row in labelled if row["weak"]}

    rows = []
    for replacement in prereg["replacements"]:
        weights = replaced_weights(replacement["remove"], replacement["add"])
        row = {"name": replacement["name"], "weights": weights}
        for window in ("train", "validation"):
            row[window] = run_sample(
                p5,
                service,
                panel,
                symbols,
                weights,
                prereg,
                weak_dates,
                start=windows[f"{window}_start"],
                end=windows[f"{window}_end"],
                cost_label="2x",
            )
        row["validation_bootstrap"] = block_bootstrap(row["validation"]["curve"])
        rows.append(row)
    base = {"name": "base", "weights": base_weights()}
    for window in ("train", "validation"):
        base[window] = run_sample(
            p5,
            service,
            panel,
            symbols,
            base["weights"],
            prereg,
            weak_dates,
            start=windows[f"{window}_start"],
            end=windows[f"{window}_end"],
            cost_label="2x",
        )
    base["validation_bootstrap"] = block_bootstrap(base["validation"]["curve"])
    eligible = [
        row
        for row in rows
        if row["train"]["stats"]["annual_return"] > base["train"]["stats"]["annual_return"]
        and row["validation"]["stats"]["annual_return"]
        > base["validation"]["stats"]["annual_return"]
        and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
        and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        and row["validation_bootstrap"]["total_p05"]
        >= prereg["selection"]["validation_bootstrap_p05_min_for_platform"]
    ]
    eligible.sort(
        key=lambda row: (
            min(
                row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]
            ),
            row["validation_bootstrap"]["total_p05"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else base
    if selected is not base:
        for window in ("test", "recent", "test_3x", "test_5x"):
            selected[window] = run_sample(
                p5,
                service,
                panel,
                symbols,
                selected["weights"],
                prereg,
                weak_dates,
                start=windows["test_start"]
                if window in {"test", "test_3x", "test_5x"}
                else windows["recent_start"],
                end=windows["test_end"],
                cost_label="3x" if window == "test_3x" else "5x" if window == "test_5x" else "2x",
            )
        selected["test_bootstrap"] = block_bootstrap(selected["test"]["curve"])
        selected["recent_bootstrap"] = block_bootstrap(selected["recent"]["curve"], seed=503)
        selected["test_excess_hs300"] = selected["test"]["stats"]["total_return"] - hs300_return(
            windows["test_start"], windows["test_end"]
        )
        selected["recent_excess_hs300"] = selected["recent"]["stats"][
            "total_return"
        ] - hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "has_eligible_replacement": bool(eligible),
        "selected_is_replacement": selected is not base,
        "validation_bootstrap_positive": selected["validation_bootstrap"]["total_p05"] > 0.0,
        "test_bootstrap_positive": selected.get("test_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "recent_bootstrap_positive": selected.get("recent_bootstrap", {}).get("total_p05", -1.0)
        > 0.0,
        "test_excess_hs300_positive": selected.get("test_excess_hs300", -1.0) > 0.0,
        "no_leverage": max(selected["weights"].values()) <= 1.0,
    }
    payload = {
        "round": "R502",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r499_sha256": sha(R499),
        "source_r501_sha256": sha(R501),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "candidates": [
            {
                "name": row["name"],
                "weights": row["weights"],
                "train": row["train"],
                "validation": row["validation"],
                "validation_bootstrap": row["validation_bootstrap"],
            }
            for row in rows
        ],
        "base": base,
        "selected": {
            **selected,
            **{
                key: {k: v for k, v in selected[key].items() if k != "curve"}
                for key in ("test", "recent", "test_3x", "test_5x")
                if key in selected
            },
        },
        "eligible_count": len(eligible),
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
    lines = [
        "# R502 行业+规模中性腿替换",
        "",
        "本轮只审计 R501 暴露的两条弱中性腿；如果没有候选同时超过训练和验证，保留基准并拒绝。",
        "",
        f"- 基准验证年化：{base['validation']['stats']['annual_return']:.2%}",
        f"- 可晋级替换：{len(eligible)}",
        f"- 选中：`{selected['name']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
    ]
    if "test" in selected:
        lines += [
            f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}；Sharpe：{selected['test']['stats']['sharpe']:.3f}；MDD：{selected['test']['stats']['max_drawdown']:.2%}",
            f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
            f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        ]
    lines += ["", "## 门槛", ""] + [
        f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()
    ]
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
