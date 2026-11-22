#!/usr/bin/env python3
"""R452: pre-registered value/quality/safety financial composites."""
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
PREREG = E / "r452_financial_quality_composite_preregistration.json"
OUTPUT = E / "r452_financial_quality_composite_2026-11-22.json"
RESULT = E / "r452_financial_quality_composite_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r452_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rank_z(frame: pl.DataFrame, factor: str, direction: str, output: str) -> pl.DataFrame:
    rank = pl.col(factor).rank("average").over("date")
    zscore = (rank - rank.mean().over("date")) / rank.std().over("date")
    expression = (-zscore if direction == "low" else zscore).cast(pl.Float64)
    return frame.with_columns(expression.alias(output))


def neutral_ic(
    frame: pl.DataFrame,
    factor: str,
    return_col: str,
    horizon: int,
    prereg: dict[str, Any],
) -> dict[str, Any]:
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.strategy.scoring import neutralize_scoring_columns
    work = frame.select(["date", "symbol", factor, return_col, "industry_l1", "log_float_mv"])
    neutral = neutralize_scoring_columns(
        work,
        [factor],
        {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
    )
    ic = calculate_rank_ic(neutral, factor, return_col, minimum=int(prereg["minimum_cross_section"]))
    return summarise_ic(
        ic,
        recent_start=date.fromisoformat(prereg["recent_start"]),
        direction="high",
        nw_lag=horizon,
    )


def incremental_against_current(frame: pl.DataFrame, factor: str, return_col: str, horizon: int, baseline_col: str, prereg: dict[str, Any]) -> dict[str, Any]:
    from app.backtest import stats_v2
    from app.factor_research.signal_scan import calculate_rank_ic
    by_date = "date"
    base = calculate_rank_ic(frame.with_columns(pl.col(baseline_col).alias("_base_copy")), "_base_copy", return_col, minimum=30)
    combined = calculate_rank_ic(
        frame.with_columns((0.8 * pl.col(baseline_col) + 0.2 * pl.col(factor)).alias("_combined")),
        "_combined",
        return_col,
        minimum=30,
    )
    paired = (
        base.select([by_date, pl.col("ic").alias("base")])
        .join(combined.select([by_date, pl.col("ic").alias("combined")]), on=by_date, how="inner")
        .with_columns((pl.col("combined") - pl.col("base")).alias("delta"))
        .sort(by_date)
    )
    values = paired["delta"].to_numpy() if paired.height else np.array([])
    nw = stats_v2.newey_west_t(values, lag=horizon) if values.size >= 3 else None
    recent = paired.filter(pl.col("date") >= date.fromisoformat(prereg["recent_start"]))
    recent_values = recent["delta"].to_numpy() if recent.height else np.array([])
    yearly = (
        paired.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(pl.col("delta").mean().alias("mean"))
        .sort("year")
    )
    overall = float(values.mean()) if values.size else 0.0
    yearly_values = yearly["mean"].to_numpy() if yearly.height else np.array([])
    sign = float(np.sign(overall)) if overall else 0.0
    return {
        "directional_delta": round(overall, 8),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "nw_p": round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None,
        "recent_delta": round(float(recent_values.mean()), 8) if recent_values.size else None,
        "yearly_sign_consistency": round(float(np.mean(np.sign(yearly_values) == sign)), 6) if yearly_values.size and sign else None,
        "n_dates": int(values.size),
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R452 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R452 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    raw_members = sorted({member for candidate in prereg["candidates"] for member in candidate["members"]})
    panel = attach_fundamental_factors(panel, snapshot, raw_members)
    panel = panel.join(
        load_existing_ext_factors(
            DATA,
            {
                "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
                "ext_alt_hs300_dividend_yield": "dividend_yield",
                "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
                "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
            },
        ),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    for horizon in prereg["horizons"]:
        panel = panel.with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d")
        )
    # Current dual-admitted control: equal-weight standardized ranks.
    baseline_members = [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_idio_vol_40d_low",
    ]
    for member in baseline_members:
        panel = rank_score_frame(panel, member, "high", output_column=f"_base_{member}")
    panel = panel.with_columns(
        sum(pl.col(f"_base_{member}").fill_null(0.0) for member in baseline_members).alias("_baseline")
    )
    rows: dict[str, dict[str, Any]] = {}
    raw_p: list[tuple[str, int, Any]] = []
    for candidate in prereg["candidates"]:
        name = candidate["name"]
        members = candidate["members"]
        for member in members:
            panel = rank_z(panel, member, candidate["directions"][member], f"_z_{name}_{member}")
        panel = panel.with_columns(
            sum(pl.col(f"_z_{name}_{member}").fill_null(0.0) for member in members).alias(f"_composite_{name}")
        ).with_columns(
            sum(pl.col(f"_z_{name}_{member}").is_not_null().cast(pl.Int8) for member in members).alias(f"_coverage_{name}")
        )
        scoped = panel.filter(pl.col(f"_coverage_{name}") == len(members))
        rows[name] = {"members": members, "directions": candidate["directions"], "horizons": {}}
        for horizon in prereg["horizons"]:
            return_col = f"forward_return_{horizon}d"
            summary = neutral_ic(scoped, f"_composite_{name}", return_col, horizon, prereg)
            incremental = incremental_against_current(scoped, f"_composite_{name}", return_col, horizon, "_baseline", prereg)
            rows[name]["horizons"][str(horizon)] = {"neutral_ic": summary, "incremental": incremental}
            raw_p.append((name, horizon, summary.get("p_value")))
    qvalues = stats_v2.bh_fdr_qvalues([p for _, _, p in raw_p])
    qmap = {(name, horizon): q for (name, horizon, _), q in zip(raw_p, qvalues, strict=True)}
    for name, result in rows.items():
        for horizon in prereg["horizons"]:
            item = result["horizons"][str(horizon)]
            q = qmap[(name, horizon)]
            item["q_value"] = round(float(q), 8) if q is not None else None
            checks = {
                "full_directional_ic_positive": isinstance(item["neutral_ic"]["full"].get("directional_ic_mean"), (int, float)) and item["neutral_ic"]["full"]["directional_ic_mean"] > 0,
                "newey_west_p_le": isinstance(item["neutral_ic"].get("p_value"), (int, float)) and item["neutral_ic"]["p_value"] <= prereg["gates"]["newey_west_p_le"],
                "bh_q_le": isinstance(q, (int, float)) and q <= prereg["gates"]["bh_q_le"],
                "yearly_sign_consistency_min": isinstance(item["neutral_ic"].get("yearly_sign_consistency"), (int, float)) and item["neutral_ic"]["yearly_sign_consistency"] >= prereg["gates"]["yearly_sign_consistency_min"],
                "recent_directional_ic_positive": isinstance(item["neutral_ic"]["recent"].get("directional_ic_mean"), (int, float)) and item["neutral_ic"]["recent"]["directional_ic_mean"] > 0,
                "incremental_ic_positive_vs_current": isinstance(item["incremental"].get("directional_delta"), (int, float)) and item["incremental"]["directional_delta"] > 0,
                "incremental_nw_p_le": isinstance(item["incremental"].get("nw_p"), (int, float)) and item["incremental"]["nw_p"] <= prereg["gates"]["incremental_nw_p_le"],
                "incremental_recent_positive": isinstance(item["incremental"].get("recent_delta"), (int, float)) and item["incremental"]["recent_delta"] > 0,
                "incremental_yearly_sign_consistency_min": isinstance(item["incremental"].get("yearly_sign_consistency"), (int, float)) and item["incremental"]["yearly_sign_consistency"] >= prereg["gates"]["yearly_sign_consistency_min"],
            }
            item["gate"] = {"pass": all(checks.values()), "checks": checks}
    admitted = sorted({name for name, result in rows.items() if any(result["horizons"][str(h)]["gate"]["pass"] for h in prereg["horizons"])})
    payload = {
        "round": "R452",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "candidates": rows,
        "admitted_candidates": admitted,
        "decision": {**prereg["output_policy"], "admitted_candidates": admitted},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R452 财报价值/质量/安全复合因子",
        "",
        "| 复合因子 | 5日中性IC | 20日中性IC | 60日中性IC | 相对四因子60日增量 | 60日NW p | 60日q | 全部门槛 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for name, result in rows.items():
        by_h = result["horizons"]
        gate = by_h["60"]["gate"]["pass"] and all(by_h[str(h)]["neutral_ic"]["full"]["directional_ic_mean"] > 0 for h in prereg["horizons"])
        lines.append(
            f"| `{name}` | {by_h['5']['neutral_ic']['full']['directional_ic_mean']:.6f} | "
            f"{by_h['20']['neutral_ic']['full']['directional_ic_mean']:.6f} | "
            f"{by_h['60']['neutral_ic']['full']['directional_ic_mean']:.6f} | "
            f"{by_h['60']['incremental']['directional_delta']:.6f} | "
            f"{by_h['60']['neutral_ic'].get('p_value')} | {by_h['60']['q_value']} | "
            f"{'pass' if gate else 'reject'} |"
        )
    lines += ["", f"- 通过候选：{', '.join(admitted) if admitted else '无'}", "", "## 边界", ""] + [
        f"- {item}" for item in prereg["known_limits"]
    ] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
