#!/usr/bin/env python3
"""R416: preregistered sentiment-interaction factor statistical screen."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r416_sentiment_interaction_preregistration.json"
OUTPUT = E / "r416_sentiment_interaction_2026-11-22.json"
RESULT = E / "r416_sentiment_interaction_2026-11-22.md"
RETURN_COLUMN = "forward_return_5d"
MINIMUM = 30


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r416_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def states() -> pl.DataFrame:
    regime = pl.read_parquet(DATA / "regime_history" / "part.parquet").sort("date")
    regime = regime.with_columns(
        pl.col("state").shift(1).alias("state_lag"),
        pl.col("above_ma20_pct").shift(1).alias("breadth_lag"),
    )
    return regime.select(["date", "state_lag", "breadth_lag"])


def build_factors(panel: pl.DataFrame) -> pl.DataFrame:
    frame = panel.sort(["symbol", "date"])
    frame = frame.with_columns(
        pl.col("close").pct_change().over("symbol").alias("_ret1"),
    )
    frame = frame.with_columns(
        (pl.col("_ret1").rolling_sum(5).over("symbol")).alias("_ret5"),
        (pl.col("_ret1").rolling_sum(10).over("symbol")).alias("_ret10"),
        (pl.col("_ret1").rolling_sum(20).over("symbol")).alias("_ret20"),
        (pl.col("_ret1") > 0).cast(pl.Float64).rolling_mean(10).over("symbol").alias("_up10"),
        (pl.col("volume").rolling_mean(20).over("symbol")).alias("_vol20"),
    )
    frame = frame.with_columns(
        (pl.col("volume") / pl.col("_vol20")).clip(1.0, 3.0).alias("_vr"),
    )
    frame = frame.join(states(), on="date", how="left")
    weak = pl.col("state_lag").is_in(["weak", "lean_weak"])
    strong = pl.col("state_lag").is_in(["strong", "lean_strong"]) & (pl.col("breadth_lag") >= 0.50)
    frame = frame.with_columns(
        pl.when(weak)
        .then(-pl.col("_ret5") * pl.col("_vr"))
        .otherwise(None)
        .alias("new_panic_reversal_5d"),
        pl.when(strong)
        .then(pl.col("_ret10") * pl.col("_up10"))
        .otherwise(None)
        .alias("new_repair_momentum_10d"),
        pl.when(pl.col("_vr") >= 1.5)
        .then(-pl.col("_ret20") * pl.col("_vr"))
        .otherwise(None)
        .alias("new_crowding_reversal_20d"),
    )
    return frame.drop([c for c in frame.columns if c.startswith("_")])


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R416 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R416 preregistration checksum mismatch")
    p5 = load_p5()
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    frame = build_factors(panel)
    from app.backtest import stats_v2
    from app.factor_research.incremental import (
        marginal_ic,
        pairwise_cross_sectional_correlation,
        rank_score_frame,
    )
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    base_columns = prereg["baseline_members"]
    for factor in base_columns:
        if factor not in frame.columns:
            raise RuntimeError(f"R416 missing baseline member: {factor}")
    work = frame
    for factor in base_columns:
        work = rank_score_frame(work, factor, "high", output_column=f"_r416_base_{factor}")
    work = work.with_columns(sum(pl.col(f"_r416_base_{factor}").fill_null(0.0) for factor in base_columns).alias("_r416_base"))
    rows: dict[str, dict[str, Any]] = {}
    recent = date.fromisoformat(prereg["windows"]["recent_start"])
    for candidate in prereg["candidates"]:
        factor = candidate["factor_id"]
        if factor not in work.columns:
            raise RuntimeError(f"R416 missing candidate: {factor}")
        work = rank_score_frame(work, factor, candidate["direction"], output_column=f"_r416_cand_{factor}")
        raw_ic = calculate_rank_ic(work, factor, RETURN_COLUMN, minimum=MINIMUM)
        raw_summary = summarise_ic(raw_ic, recent_start=recent, direction=candidate["direction"])
        inc = marginal_ic(work, "_r416_base", f"_r416_cand_{factor}", RETURN_COLUMN, recent_start=recent, minimum_symbols=MINIMUM, horizon=int(prereg["horizon_days"]))
        _, corr = pairwise_cross_sectional_correlation(work, {"base": "_r416_base", "candidate": f"_r416_cand_{factor}"}, minimum_symbols=MINIMUM)
        summary = corr.get("base|candidate") or corr.get("candidate|base") or {}
        coverage = work.select(
            pl.col(factor).is_not_null().sum().alias("non_null"),
            pl.len().alias("rows"),
        ).row(0, named=True)
        rows[factor] = {
            "direction": candidate["direction"],
            "coverage_ratio": round(float(coverage["non_null"]) / float(coverage["rows"]), 6) if coverage["rows"] else 0.0,
            "raw_ic": raw_summary,
            "marginal": inc,
            "max_correlation": round(abs(float(summary.get("mean_abs"))), 6) if summary.get("mean_abs") is not None else None,
        }
    qvalues = stats_v2.bh_fdr_qvalues([rows[f]["marginal"].get("delta_p_value") for f in rows])
    for (_factor, row), q in zip(rows.items(), qvalues, strict=True):
        row["q_value"] = round(float(q), 8)
        delta = row["marginal"].get("directional_delta_mean")
        pvalue = row["marginal"].get("delta_p_value")
        recent_delta = row["marginal"].get("recent_directional_delta_mean")
        checks = {
            "marginal_ic_positive": isinstance(delta, (int, float)) and delta > 0,
            "nw_p_max": isinstance(pvalue, (int, float)) and pvalue <= 0.10,
            "bh_q_max": isinstance(q, (int, float)) and q <= 0.10,
            "max_correlation_max": row["max_correlation"] is None or row["max_correlation"] < 0.90,
            "minimum_n_dates": row["marginal"].get("n_dates", 0) >= 120,
            "recent_delta_positive": isinstance(recent_delta, (int, float)) and recent_delta > 0,
        }
        row["gate"] = {"pass": all(checks.values()), "checks": checks}
        row["status"] = "statistical_pass" if row["gate"]["pass"] else "statistical_rejected"
    payload = {
        "round": "R416",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "candidates": rows,
        "pass_count": sum(row["status"] == "statistical_pass" for row in rows.values()),
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R416 情绪交互因子统计筛查",
        "",
        "| 因子 | 覆盖 | 边际IC | NW p | q | 相关 | 近一年增量 | 结论 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for factor, row in rows.items():
        marginal = row["marginal"]
        lines.append(
            f"| {factor} | {row['coverage_ratio']:.2%} | {marginal.get('directional_delta_mean')} | "
            f"{marginal.get('delta_p_value')} | {row['q_value']:.4f} | "
            f"{row['max_correlation'] if row['max_correlation'] is not None else '-'} | "
            f"{marginal.get('recent_directional_delta_mean')} | {row['status']} |"
        )
    lines += ["", "## 门槛", ""]
    for factor, row in rows.items():
        lines.append(f"- {factor}: " + ", ".join(f"{k}={'通过' if v else '未过'}" for k, v in row["gate"]["checks"].items()))
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
