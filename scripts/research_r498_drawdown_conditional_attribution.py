#!/usr/bin/env python3
"""R498: drawdown-cluster conditional attribution for the frozen R463 sleeve."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r498_drawdown_conditional_attribution_preregistration.json"
R463 = E / "r463_hold25_confirm_2026-11-22.json"
OUTPUT = E / "r498_drawdown_conditional_attribution_2026-11-22.json"
RESULT = E / "r498_drawdown_conditional_attribution_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R498 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fit_weak_threshold(
    rows: list[dict[str, Any]], *, start: str, end: str, breadth_threshold: float = 0.35
) -> dict[str, float]:
    scores = sorted(
        float(row["score"])
        for row in rows
        if start <= str(row["date"])[:10] <= end
        and row.get("score") is not None
        and math.isfinite(float(row["score"]))
    )
    if not scores:
        raise ValueError("no finite regime scores in fit window")
    index = max(0, min(len(scores) - 1, math.ceil(len(scores) / 3.0) - 1))
    return {
        "score_threshold": float(scores[index]),
        "breadth_threshold": float(breadth_threshold),
        "fit_start": start,
        "fit_end": end,
        "fit_score_count": float(len(scores)),
    }


def apply_weak_state(
    rows: list[dict[str, Any]], threshold: dict[str, float]
) -> list[dict[str, Any]]:
    """Mark each row from its own T-1 regime information only."""
    output: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in rows:
        score = previous.get("score") if previous else None
        breadth = previous.get("above_ma20_pct") if previous else None
        weak = bool(
            (isinstance(score, (int, float)) and float(score) <= threshold["score_threshold"])
            or (
                isinstance(breadth, (int, float))
                and float(breadth) < threshold["breadth_threshold"]
            )
        )
        enriched = dict(row)
        enriched["weak"] = weak
        enriched["t1_score"] = score
        enriched["t1_above_ma20_pct"] = breadth
        output.append(enriched)
        previous = row
    return output


def _window_rows(rows: list[dict[str, Any]], *, start: str, end: str) -> list[dict[str, Any]]:
    return [row for row in rows if start <= str(row["date"])[:10] <= end]


def leg_contribution(
    panel: pl.DataFrame,
    factor: str,
    return_column: str,
    *,
    minimum: int = 30,
) -> dict[str, Any]:
    required = {"date", "symbol", factor, return_column}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"leg contribution missing columns: {missing}")
    work = panel.select(["date", "symbol", factor, return_column]).filter(
        pl.col(factor).is_not_null()
        & pl.col(factor).is_finite()
        & pl.col(return_column).is_not_null()
        & pl.col(return_column).is_finite()
    )
    work = work.with_columns(
        pl.col(factor).rank("average").over("date").alias("_rank_factor"),
        pl.col(return_column).rank("average").over("date").alias("_rank_return"),
    )
    daily = (
        work.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col("_rank_factor"), pl.col("_rank_return")).alias("corr"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("corr").is_not_null())
        .sort("date")
    )
    values = daily["corr"].to_numpy() if daily.height else np.array([], dtype=float)
    return {
        "n_dates": int(values.size),
        "mean": round(float(values.mean()), 8) if values.size else None,
        "median": round(float(np.median(values)), 8) if values.size else None,
        "positive_rate": round(float((values > 0).mean()), 6) if values.size else None,
    }


def daily_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [
        (
            str(curve[index]["date"])[:10],
            float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0,
        )
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def block_bootstrap(
    values: list[float], *, block: int = 20, paths: int = 5000, seed: int = 498
) -> dict[str, float]:
    import random

    if not values:
        return {"mean_p05": 0.0, "mean_p50": 0.0, "positive_rate": 0.0}
    rng = random.Random(seed)
    means = []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        means.append(sum(selected) / len(selected))
    means.sort()
    return {
        "mean_p05": round(means[min(len(means) - 1, int(0.05 * len(means)))], 8),
        "mean_p50": round(means[min(len(means) - 1, int(0.50 * len(means)))], 8),
        "positive_rate": round(sum(value > 0 for value in means) / len(means), 6),
    }


def build_portfolio_panel(p5: Any, prereg: dict[str, Any]) -> tuple[pl.DataFrame, list[str]]:
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
        panel = rank_score_frame(panel, member, direction, output_column=f"_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_vq_pb_latest").fill_null(0.0)
            + pl.col("_vq_ocf_debt").fill_null(0.0)
            + pl.col("_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_vq_pb_latest").is_not_null()
            & pl.col("_vq_ocf_debt").is_not_null()
            & pl.col("_vq_debt_ratio_latest").is_not_null()
        ).alias("_vq_valid"),
    ).filter(pl.col("_vq_valid"))
    for horizon in (5, 20):
        panel = panel.with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(
                f"forward_return_{horizon}d"
            )
        )
    return panel, sorted(panel["symbol"].unique().to_list())


def run_portfolio_curve(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    prereg: dict[str, Any],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    from app.factor_research.incremental import build_rank_composite

    weights = dict(prereg["portfolio"]["weights"])
    directions = dict(prereg["portfolio"]["directions"])
    field = "_r498_r463_portfolio"
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel, weights, directions, field, minimum_weight_coverage=0.75
        )
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(prereg["portfolio"]["max_positions"]),
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=int(prereg["portfolio"]["max_hold_days"]),
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(prereg["portfolio"]["entry_score"]),
        "exit_score": float(prereg["portfolio"]["exit_score"]),
        "top_rank": 20,
    }
    config.max_exposure_pct = float(prereg["portfolio"]["max_exposure_pct"])
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, [field])
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return list(result.equity_curve)


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R463)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(R463.read_text(encoding="utf-8"))
    if source["selected"]["positions"] != prereg["portfolio"]["max_positions"]:
        raise RuntimeError("R463 position count changed")
    if source["selected"]["hold"] != prereg["portfolio"]["max_hold_days"]:
        raise RuntimeError("R463 hold window changed")

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r498_r453")
    p5 = r453.load_p5()
    panel, symbols = build_portfolio_panel(p5, prereg)
    service = p5.portfolio._service()
    windows = prereg["windows"]

    regime = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Utf8))
        .select(["date", "score", "above_ma20_pct"])
    )
    regime_rows = regime.to_dicts()
    threshold = fit_weak_threshold(
        regime_rows,
        start=windows["train_start"],
        end=windows["validation_end"],
        breadth_threshold=0.35,
    )
    labelled = apply_weak_state(regime_rows, threshold)
    label_by_date = {str(row["date"])[:10]: row for row in labelled}

    curves = {}
    for window in ("train", "validation", "test", "recent"):
        curves[window] = run_portfolio_curve(
            p5,
            service,
            panel,
            symbols,
            prereg,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
        )

    daily = {window: daily_returns(curve) for window, curve in curves.items()}
    window_summary = {}
    for window, pairs in daily.items():
        weak_returns = [ret for day, ret in pairs if label_by_date.get(day, {}).get("weak")]
        nonweak_returns = [ret for day, ret in pairs if not label_by_date.get(day, {}).get("weak")]
        window_summary[window] = {
            "curve_days": len(pairs),
            "weak_days": len(weak_returns),
            "nonweak_days": len(nonweak_returns),
            "weak_mean_daily": round(float(np.mean(weak_returns)), 8) if weak_returns else None,
            "nonweak_mean_daily": round(float(np.mean(nonweak_returns)), 8)
            if nonweak_returns
            else None,
            "weak_total_return": round(
                float(np.prod([1.0 + value for value in weak_returns]) - 1.0), 8
            )
            if weak_returns
            else None,
            "nonweak_total_return": round(
                float(np.prod([1.0 + value for value in nonweak_returns]) - 1.0), 8
            )
            if nonweak_returns
            else None,
            "weak_bootstrap": block_bootstrap(
                weak_returns,
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seed"]) + len(window_summary),
            ),
        }

    members = list(prereg["portfolio"]["members"])
    leg_rows: dict[str, dict[str, Any]] = {}
    for member in members:
        leg_rows[member] = {}
        for window in ("train", "validation", "test", "recent"):
            start = windows[f"{window}_start"]
            end = windows["test_end"] if window == "recent" else windows[f"{window}_end"]
            scoped = panel.filter(
                (pl.col("date") >= date.fromisoformat(start))
                & (pl.col("date") <= date.fromisoformat(end))
            )
            weak_dates = {
                date.fromisoformat(day)
                for day, row in label_by_date.items()
                if row.get("weak") and start <= day <= end
            }
            nonweak_dates = {
                date.fromisoformat(day)
                for day, row in label_by_date.items()
                if not row.get("weak") and start <= day <= end
            }
            weak = leg_contribution(
                scoped.filter(pl.col("date").is_in(weak_dates)),
                member,
                "forward_return_5d",
                minimum=int(prereg["leg_contribution"]["minimum_cross_section"]),
            )
            nonweak = leg_contribution(
                scoped.filter(pl.col("date").is_in(nonweak_dates)),
                member,
                "forward_return_5d",
                minimum=int(prereg["leg_contribution"]["minimum_cross_section"]),
            )
            weak_mean = weak["mean"] if isinstance(weak["mean"], (int, float)) else 0.0
            nonweak_mean = nonweak["mean"] if isinstance(nonweak["mean"], (int, float)) else 0.0
            leg_rows[member][window] = {
                "weak": weak,
                "nonweak": nonweak,
                "weak_minus_nonweak": round(float(weak_mean) - float(nonweak_mean), 8),
            }

    checks = {
        "weak_days_train_min": window_summary["train"]["weak_days"]
        >= prereg["gates"]["weak_days_train_min"],
        "weak_days_validation_min": window_summary["validation"]["weak_days"]
        >= prereg["gates"]["weak_days_validation_min"],
        "weak_days_test_min": window_summary["test"]["weak_days"]
        >= prereg["gates"]["weak_days_test_min"],
        "weak_days_recent_min": window_summary["recent"]["weak_days"]
        >= prereg["gates"]["weak_days_recent_min"],
        "minimum_leg_observations": all(
            row[window]["weak"]["n_dates"] >= prereg["gates"]["minimum_leg_observations"]
            for row in leg_rows.values()
            for window in ("train", "validation", "test", "recent")
        ),
        "stable_leg_sign_across_windows": all(
            len(
                {
                    np.sign(row[window]["weak"]["mean"])
                    for window in ("train", "validation", "test", "recent")
                    if isinstance(row[window]["weak"]["mean"], (int, float))
                }
            )
            <= 1
            for row in leg_rows.values()
        ),
        "no_leverage": prereg["portfolio"]["max_exposure_pct"] <= 1.0,
    }

    payload = {
        "round": "R498",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r463_sha256": sha(R463),
        "selection_basis": "train_and_validation_threshold_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "portfolio": prereg["portfolio"],
        "weak_state": threshold,
        "window_summary": window_summary,
        "leg_attribution": leg_rows,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {
            **prereg["output_policy"],
            "status": "drawdown_conditional_attribution_complete_no_promotion",
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )

    lines = [
        "# R498 回撤簇条件归因",
        "",
        "本轮冻结 R463 五腿组合，只用训练+验证期估计弱市阈值，检验和近一年只做确认。",
        "",
        f"- 弱市阈值：score <= {threshold['score_threshold']:.2f}，或 breadth < {threshold['breadth_threshold']:.0%}",
        f"- 判定：**{'通过' if all(checks.values()) else '未通过'}**",
        "",
        "## 窗口",
        "",
        "| 窗口 | 弱市天数 | 弱市日均 | 非弱市日均 | 弱市累计 | 非弱市累计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window, summary in window_summary.items():
        lines.append(
            f"| {window} | {summary['weak_days']} | {summary['weak_mean_daily']:.4%} | "
            f"{summary['nonweak_mean_daily']:.4%} | {summary['weak_total_return']:.2%} | "
            f"{summary['nonweak_total_return']:.2%} |"
        )
    lines += [
        "",
        "## 各腿弱市贡献",
        "",
        "| 因子 | 训练弱市 | 验证弱市 | 检验弱市 | 近一年弱市 | 稳定性 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for member, row in leg_rows.items():
        values = [
            row[window]["weak"]["mean"] for window in ("train", "validation", "test", "recent")
        ]
        stable = (
            "是"
            if len({np.sign(value) for value in values if isinstance(value, (int, float))}) <= 1
            else "否"
        )
        lines.append(
            f"| `{member}` | {values[0]:.6f} | {values[1]:.6f} | "
            f"{values[2]:.6f} | {values[3]:.6f} | {stable} |"
        )
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
