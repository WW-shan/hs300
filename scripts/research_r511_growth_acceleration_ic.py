#!/usr/bin/env python3
"""R511: growth acceleration and quality-confirmed growth IC audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r511_growth_acceleration_ic_preregistration.json"
OUTPUT = E / "r511_growth_acceleration_ic_2026-11-22.json"
RESULT = E / "r511_growth_acceleration_ic_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R511 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def composite_weights(name: str) -> dict[str, float]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    weights = prereg["composites"][name]["members"]
    total = sum(float(value) for value in weights.values())
    if total <= 0:
        raise ValueError("composite weights must be positive")
    return {member: float(value) / total for member, value in weights.items()}


def acceleration_frame(frame: pl.DataFrame) -> pl.DataFrame:
    required = {"symbol", "period_end", "revenue_yoy", "net_income_yoy"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"acceleration frame missing columns: {missing}")
    work = frame.sort(["symbol", "period_end"]).with_columns(
        (pl.col("revenue_yoy") - pl.col("revenue_yoy").shift(1).over("symbol")).alias(
            "_revenue_accel"
        ),
        (pl.col("net_income_yoy") - pl.col("net_income_yoy").shift(1).over("symbol")).alias(
            "_net_income_accel"
        ),
    )
    return work.with_columns(
        ((pl.col("_revenue_accel") + pl.col("_net_income_accel")) / 2.0).alias(
            "growth_acceleration"
        )
    )


def ic_detail(
    panel: pl.DataFrame, factor: str, return_column: str, *, minimum: int
) -> dict[str, Any]:
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
            pl.corr(pl.col("_rank_factor"), pl.col("_rank_return")).alias("ic"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null())
        .sort("date")
    )
    values = daily["ic"].to_numpy() if daily.height else np.array([], dtype=float)
    yearly = None
    if daily.height:
        yearly_frame = (
            daily.with_columns(pl.col("date").dt.year().alias("year"))
            .group_by("year")
            .agg(pl.col("ic").mean().alias("mean"))
            .sort("year")
        )
        yearly_values = yearly_frame["mean"].to_numpy()
        overall = float(values.mean())
        if yearly_values.size and overall != 0:
            yearly = round(float((np.sign(yearly_values) == np.sign(overall)).mean()), 6)
    return {
        "n_dates": len(values),
        "mean_ic": round(float(values.mean()), 8) if values.size else None,
        "positive_rate": round(float((values > 0).mean()), 6) if values.size else None,
        "yearly_sign_consistency": yearly,
    }


def nw_p(values: np.ndarray, lag: int) -> float | None:
    if values.size < 3:
        return None
    from app.backtest import stats_v2

    result = stats_v2.newey_west_t(values, lag=lag)
    return round(float(stats_v2.normal_two_sided_p(result[0])), 8) if result is not None else None


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.hs300.current import current_symbols
    from app.strategy.scoring import neutralize_scoring_columns

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel,
        snapshot,
        ["revenue_yoy_latest", "net_income_yoy_latest", "accrual"],
    ).sort(["symbol", "date"])
    growth_rows = (
        snapshot.select(["symbol", "period_end", "revenue_yoy", "net_income_yoy"])
        .drop_nulls(["period_end", "revenue_yoy", "net_income_yoy"])
        .sort(["symbol", "period_end"])
    )
    growth_rows = acceleration_frame(growth_rows).select(
        ["symbol", "period_end", "growth_acceleration"]
    )
    from app.backtest.fundamentals import financial_pit_date

    pit_rows = growth_rows.with_columns(
        pl.col("period_end")
        .map_elements(
            lambda value: financial_pit_date(value, None),
            return_dtype=pl.Date,
        )
        .alias("_effective")
    ).select(["symbol", "period_end", "_effective", "growth_acceleration"])
    pit_rows = pit_rows.sort(["symbol", "_effective", "period_end"]).unique(
        subset=["symbol", "_effective"], keep="last", maintain_order=True
    )
    panel = panel.join_asof(
        pit_rows,
        left_on="date",
        right_on="_effective",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    del neutralize_scoring_columns
    return panel, sorted(panel["symbol"].unique().to_list())


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    from app.backtest import stats_v2

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r511_r453")
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    del symbols
    from app.factor_research.incremental import rank_score_frame
    from app.strategy.scoring import neutralize_scoring_columns

    panel = panel.filter(
        (pl.col("date") >= date.fromisoformat(prereg["windows"]["start"]))
        & (pl.col("date") <= date.fromisoformat(prereg["windows"]["end"]))
    )
    for horizon in prereg["return_horizons"]:
        panel = panel.with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(
                f"forward_return_{horizon}d"
            )
        )
    work = panel
    for member, direction in (
        ("revenue_yoy_latest", "high"),
        ("net_income_yoy_latest", "high"),
        ("accrual", "low"),
    ):
        work = rank_score_frame(work, member, direction, output_column=f"_r511_{member}")
    work = work.with_columns(
        (
            0.35 * pl.col("_r511_revenue_yoy_latest")
            + 0.35 * pl.col("_r511_net_income_yoy_latest")
            + 0.30 * pl.col("_r511_accrual")
        ).alias("quality_confirmed_growth")
    )
    factors = ("growth_acceleration", "quality_confirmed_growth")
    output: dict[str, Any] = {}
    raw_p_values = []
    for factor in factors:
        output[factor] = {}
        for horizon in prereg["return_horizons"]:
            return_col = f"forward_return_{horizon}d"
            raw = ic_detail(work, factor, return_col, minimum=int(prereg["minimum_cross_section"]))
            neutral = neutralize_scoring_columns(
                work.select(["date", "symbol", factor, return_col, "industry_l1", "log_float_mv"]),
                [factor],
                prereg["neutralization"],
            )
            neutral_ic = ic_detail(
                neutral,
                factor,
                return_col,
                minimum=int(prereg["minimum_cross_section"]),
            )
            daily_ic = (
                neutral.group_by("date")
                .agg(
                    pl.len().alias("n"),
                    pl.corr(pl.col(factor).rank(), pl.col(return_col).rank()).alias("ic"),
                )
                .filter(
                    (pl.col("n") >= int(prereg["minimum_cross_section"]))
                    & pl.col("ic").is_not_null()
                )
                .sort("date")
            )
            values = daily_ic["ic"].to_numpy() if daily_ic.height else np.array([], dtype=float)
            nw = stats_v2.newey_west_t(values, lag=int(horizon)) if values.size >= 3 else None
            p_value = (
                round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None
            )
            output[factor][str(horizon)] = {
                "raw": raw,
                "neutral": neutral_ic,
                "p_value": p_value,
            }
            raw_p_values.append((factor, horizon, p_value))
    qvalues = stats_v2.bh_fdr_qvalues(
        [p if isinstance(p, (int, float)) else None for _, _, p in raw_p_values]
    )
    qmap = {
        (factor, horizon): q for (factor, horizon, _), q in zip(raw_p_values, qvalues, strict=True)
    }
    for factor, by_horizon in output.items():
        for horizon, item in by_horizon.items():
            q = qmap[(factor, int(horizon))]
            item["q_value"] = round(float(q), 8) if q is not None else None
            checks = {
                "neutral_ic_positive": isinstance(item["neutral"]["mean_ic"], (int, float))
                and item["neutral"]["mean_ic"] > 0,
                "nw_p_max": isinstance(item["p_value"], (int, float))
                and item["p_value"] <= prereg["gates"]["newey_west_p_max"],
                "bh_q_max": isinstance(q, (int, float)) and q <= prereg["gates"]["bh_q_max"],
                "yearly_sign_consistency_min": isinstance(
                    item["neutral"]["yearly_sign_consistency"], (int, float)
                )
                and item["neutral"]["yearly_sign_consistency"]
                >= prereg["gates"]["yearly_sign_consistency_min"],
                "minimum_valid_dates": item["neutral"]["n_dates"]
                >= prereg["gates"]["minimum_valid_dates"],
            }
            item["gate"] = {"pass": all(checks.values()), "checks": checks}
    payload = {
        "round": "R511",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selection_performed": False,
        "composites": output,
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R511 成长加速度与质量确认成长 IC 审计",
        "",
        "| 复合因子 | 周期 | 原始IC | 中性IC | NW p | q | 年同号 | 有效日期 | 门槛 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for factor, by_horizon in output.items():
        for horizon, item in by_horizon.items():
            lines.append(
                f"| `{factor}` | {horizon} | {item['raw']['mean_ic']} | "
                f"{item['neutral']['mean_ic']} | {item['p_value']} | {item['q_value']} | "
                f"{item['neutral']['yearly_sign_consistency']} | "
                f"{item['neutral']['n_dates']} | {'通过' if item['gate']['pass'] else '拒绝'} |"
            )
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
