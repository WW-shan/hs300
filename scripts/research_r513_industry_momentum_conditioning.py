#!/usr/bin/env python3
"""R513: industry-momentum conditional IC audit for R506 sleeves."""

from __future__ import annotations

import hashlib
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
PREREG = E / "r513_industry_momentum_conditioning_preregistration.json"
OUTPUT = E / "r513_industry_momentum_conditioning_2026-11-22.json"
RESULT = E / "r513_industry_momentum_conditioning_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R513 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def label_states(rows: list[dict[str, Any]], *, min_history: int) -> list[dict[str, Any]]:
    values: list[float] = []
    output: list[dict[str, Any]] = []
    for row in rows:
        value = row.get("conditioner")
        if value is None or not math.isfinite(float(value)):
            output.append({**row, "state": None})
            continue
        if len(values) < min_history:
            state = None
        else:
            low, high = np.quantile(values, [1 / 3, 2 / 3])
            state = "low" if value <= low else "high" if value >= high else "mid"
        output.append({**row, "state": state})
        values.append(float(value))
    return output


def daily_ic(panel: pl.DataFrame, factor: str, return_column: str, *, minimum: int) -> pl.DataFrame:
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
    return (
        work.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col("_rank_factor"), pl.col("_rank_return")).alias("ic"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null())
        .sort("date")
    )


def build_panel(p5: Any, prereg: dict[str, Any]) -> pl.DataFrame:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import build_rank_composite, rank_score_frame
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
            "industry_mom_20d": "industry_mom_20d",
            "industry_rps_20d": "industry_rps_20d",
            "industry_breadth_20d": "industry_breadth_20d",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r513_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r513_vq_pb_latest").fill_null(0.0)
            + pl.col("_r513_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r513_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r513_vq_pb_latest").is_not_null()
            & pl.col("_r513_vq_ocf_debt").is_not_null()
            & pl.col("_r513_vq_debt_ratio_latest").is_not_null()
        ).alias("_r513_vq_valid"),
    ).filter(pl.col("_r513_vq_valid"))
    panel, _ = build_rank_composite(
        panel,
        prereg["sleeves"]["admitted_core"]["members"],
        prereg["sleeves"]["admitted_core"]["directions"],
        "admitted_core",
        minimum_weight_coverage=0.75,
    )
    panel = panel.with_columns(pl.col("value_quality").alias("value_quality_sleeve"))
    return panel


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r513_r453")
    p5 = r453.load_p5()
    panel = build_panel(p5, prereg)
    from app.backtest import stats_v2
    from app.strategy.scoring import neutralize_scoring_columns

    windows = prereg["windows"]
    panel = panel.filter(
        (pl.col("date") >= date.fromisoformat(windows["start"]))
        & (pl.col("date") <= date.fromisoformat(windows["end"]))
    )
    for horizon in prereg["return_horizons"]:
        panel = panel.with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(
                f"forward_return_{horizon}d"
            )
        )
    result: dict[str, Any] = {}
    raw_p: list[tuple[str, str, int, float | None]] = []
    for conditioner in prereg["conditioners"]:
        daily_conditioner = (
            panel.group_by("date")
            .agg(pl.col(conditioner).median().alias("conditioner"))
            .sort("date")
        )
        labelled = label_states(
            daily_conditioner.to_dicts(),
            min_history=int(prereg["conditioning"]["minimum_history_days"]),
        )
        state_by_date = {str(row["date"])[:10]: row["state"] for row in labelled}
        result[conditioner] = {}
        for sleeve in ("admitted_core", "value_quality_sleeve"):
            result[conditioner][sleeve] = {}
            for horizon in prereg["return_horizons"]:
                return_col = f"forward_return_{horizon}d"
                neutral = neutralize_scoring_columns(
                    panel.select(
                        ["date", "symbol", sleeve, return_col, "industry_l1", "log_float_mv"]
                    ),
                    [sleeve],
                    prereg["neutralization"],
                )
                daily = daily_ic(
                    neutral, sleeve, return_col, minimum=int(prereg["minimum_cross_section"])
                ).with_columns(
                    pl.col("date")
                    .cast(pl.Utf8)
                    .str.slice(0, 10)
                    .replace_strict(state_by_date, default=None)
                    .alias("state")
                )
                by_state = {
                    state: daily.filter(pl.col("state") == state)["ic"].to_numpy()
                    for state in ("low", "mid", "high")
                }
                low, high = by_state["low"], by_state["high"]
                difference = float(high.mean() - low.mean()) if low.size and high.size else None
                p_value = None
                if low.size >= 3 and high.size >= 3:
                    nw = stats_v2.newey_west_t(np.concatenate([high, -low]), lag=int(horizon))
                    p_value = (
                        round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
                        if nw is not None
                        else None
                    )
                result[conditioner][sleeve][str(horizon)] = {
                    "low_ic": round(float(low.mean()), 8) if low.size else None,
                    "mid_ic": round(float(by_state["mid"].mean()), 8)
                    if by_state["mid"].size
                    else None,
                    "high_ic": round(float(high.mean()), 8) if high.size else None,
                    "high_minus_low": round(difference, 8) if difference is not None else None,
                    "p_value": p_value,
                    "low_days": int(low.size),
                    "high_days": int(high.size),
                }
                raw_p.append((conditioner, sleeve, int(horizon), p_value))
    qvalues = stats_v2.bh_fdr_qvalues(
        [p if isinstance(p, (int, float)) else None for _, _, _, p in raw_p]
    )
    qmap = {
        (conditioner, sleeve, horizon): q
        for (conditioner, sleeve, horizon, _), q in zip(raw_p, qvalues, strict=True)
    }
    for conditioner, by_sleeve in result.items():
        for sleeve, by_horizon in by_sleeve.items():
            for horizon, row in by_horizon.items():
                q = qmap[(conditioner, sleeve, int(horizon))]
                row["q_value"] = round(float(q), 8) if q is not None else None
                checks = {
                    "abs_effect": isinstance(row["high_minus_low"], (int, float))
                    and abs(row["high_minus_low"]) >= prereg["gates"]["high_minus_low_abs_min"],
                    "nw_p_max": isinstance(row["p_value"], (int, float))
                    and row["p_value"] <= prereg["gates"]["newey_west_p_max"],
                    "bh_q_max": isinstance(q, (int, float)) and q <= prereg["gates"]["bh_q_max"],
                    "minimum_days": row["low_days"] >= prereg["gates"]["minimum_days_per_state"]
                    and row["high_days"] >= prereg["gates"]["minimum_days_per_state"],
                }
                row["gate"] = {"pass": all(checks.values()), "checks": checks}
    payload = {
        "round": "R513",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selection_performed": False,
        "conditioners": result,
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R513 行业动量条件化 IC 审计",
        "",
        "| 条件变量 | 股票袖 | 周期 | 高-低 IC | NW p | q | 高/低天数 | 门槛 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for conditioner, by_sleeve in result.items():
        for sleeve, by_horizon in by_sleeve.items():
            for horizon, row in by_horizon.items():
                lines.append(
                    f"| {conditioner} | {sleeve} | {horizon} | {row['high_minus_low']} | "
                    f"{row['p_value']} | {row['q_value']} | {row['high_days']}/{row['low_days']} | "
                    f"{'通过' if row['gate']['pass'] else '拒绝'} |"
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
