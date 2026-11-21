#!/usr/bin/env python3
"""R412: incremental 5-day IC screening of PIT-verified financial base fields."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.incremental import (
    marginal_ic,
    pairwise_cross_sectional_correlation,
    rank_score_frame,
)
from app.factor_research.sets import FactorSetStore
from app.hs300.current import current_symbols

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r412_financial_base_preregistration.json"
OUTPUT = EVIDENCE / "r412_financial_base_2026-11-22.json"
RESULT = EVIDENCE / "r412_financial_base_2026-11-22.md"
RETURN_COLUMN = "forward_return_5d"
MINIMUM_CROSS_SECTION = 30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p8() -> Any:
    path = ROOT / "scripts" / "research_financial_increment_p8.py"
    spec = importlib.util.spec_from_file_location("r412_p8", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_inputs() -> tuple[dict[str, Any], pl.DataFrame]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    catalog = load_latest_catalog(DATA)
    sets = FactorSetStore(DATA, catalog=catalog)
    head = sets.head("multi_dimension_v1")
    if head is None or head.watch_version is None:
        raise RuntimeError("multi_dimension_v1 watch version is unavailable")
    sets.load_version("multi_dimension_v1", head.watch_version)
    p8 = load_p8()
    panel, _ = p8.p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot is unavailable")
    panel = attach_fundamental_factors(panel, snapshot, prereg["candidates"])
    return prereg, panel


def prepare_core(prereg: dict[str, Any], panel: pl.DataFrame) -> pl.DataFrame:
    work = panel
    missing = [
        factor
        for factor in prereg["core_members"]
        if factor not in work.columns
    ]
    if missing:
        raise RuntimeError(f"missing core factors: {missing}")
    for factor in prereg["core_members"]:
        work = rank_score_frame(
            work,
            factor,
            "high",
            output_column=f"_r412_core_{factor}",
        )
    return work.with_columns(
        sum(pl.col(f"_r412_core_{factor}").fill_null(0.0) for factor in prereg["core_members"])
        .alias("_r412_core_composite")
    )


def correlation_summary(
    frame: pl.DataFrame,
    left: str,
    right: str,
) -> float | None:
    if left not in frame.columns or right not in frame.columns:
        return None
    _, summaries = pairwise_cross_sectional_correlation(
        frame,
        {"core": left, "candidate": right},
        minimum_symbols=MINIMUM_CROSS_SECTION,
    )
    summary = summaries.get("core|candidate") or summaries.get("candidate|core")
    if summary is None:
        return None
    return round(abs(float(summary["mean_abs"])), 6)


def evaluate_candidate(
    prereg: dict[str, Any],
    work: pl.DataFrame,
    factor: str,
) -> dict[str, Any]:
    if RETURN_COLUMN not in work.columns:
        raise RuntimeError(f"missing forward return column: {RETURN_COLUMN}")
    direction = prereg["directions"][factor]
    scored = rank_score_frame(
        work,
        factor,
        direction,
        output_column=f"_r412_{factor}",
    )
    combined = scored.with_columns(
        (
            0.8 * pl.col("_r412_core_composite")
            + 0.2 * pl.col(f"_r412_{factor}")
        ).alias("_r412_combined")
    )
    marginal = marginal_ic(
        combined,
        "_r412_core_composite",
        f"_r412_{factor}",
        RETURN_COLUMN,
        recent_start=date.fromisoformat(prereg["windows"]["recent_start"]),
        minimum_symbols=MINIMUM_CROSS_SECTION,
        horizon=int(prereg["horizon_days"]),
    )
    coverage = (
        combined.select(
            pl.col(factor).is_not_null().sum().alias("non_null_rows"),
            pl.len().alias("rows"),
            pl.col("date").min().alias("first_date"),
            pl.col("date").max().alias("last_date"),
        )
        .row(0, named=True)
    )
    return {
        "direction": direction,
        "coverage": {
            "rows": int(coverage["rows"]),
            "non_null_rows": int(coverage["non_null_rows"]),
            "non_null_ratio": round(
                float(coverage["non_null_rows"]) / float(coverage["rows"]), 6
            ) if coverage["rows"] else 0.0,
            "first_date": str(coverage["first_date"]) if coverage["first_date"] else None,
            "last_date": str(coverage["last_date"]) if coverage["last_date"] else None,
        },
        "marginal": marginal,
        "max_correlation": correlation_summary(
            combined,
            "_r412_core_composite",
            f"_r412_{factor}",
        ),
    }


def classify(rows: dict[str, dict[str, Any]]) -> None:
    pvalues = [
        row["marginal"].get("delta_p_value")
        for row in rows.values()
    ]
    qvalues = stats_v2.bh_fdr_qvalues(
        [value if isinstance(value, (int, float)) else 1.0 for value in pvalues]
    )
    for (_factor, row), qvalue in zip(rows.items(), qvalues, strict=True):
        marginal = row["marginal"]
        delta = marginal.get("directional_delta_mean")
        pvalue = marginal.get("delta_p_value")
        correlation = row.get("max_correlation")
        row["q_value"] = round(float(qvalue), 8)
        if row["coverage"]["non_null_rows"] == 0 or marginal.get("n_dates", 0) < 120:
            row["status"] = "data_unavailable"
        elif (
            isinstance(delta, (int, float))
            and delta > 0
            and isinstance(pvalue, (int, float))
            and pvalue <= 0.10
            and isinstance(qvalue, (int, float))
            and qvalue <= 0.10
            and (correlation is None or correlation < 0.90)
        ):
            row["status"] = "statistical_pass"
        else:
            row["status"] = "statistical_rejected"


def build_payload(prereg: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "round": "R412",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "candidates": rows,
        "pass_count": sum(row["status"] == "statistical_pass" for row in rows.values()),
        "data_unavailable_count": sum(
            row["status"] == "data_unavailable" for row in rows.values()
        ),
        "decision": {
            "status": "financial_base_stats_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def write_outputs(payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    lines = [
        "# R412 财报基础字段增量筛查",
        "",
        "| 因子 | 方向 | 覆盖率 | 边际IC | NW p | q | 相关 | 结论 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for factor, row in payload["candidates"].items():
        marginal = row["marginal"]
        delta = marginal.get("directional_delta_mean")
        pvalue = marginal.get("delta_p_value")
        correlation = row.get("max_correlation")
        lines.append(
            f"| {factor} | {row['direction']} | {row['coverage']['non_null_ratio']:.2%} "
            f"| {delta if delta is not None else '-'} "
            f"| {pvalue if pvalue is not None else '-'} "
            f"| {row['q_value']:.4f} "
            f"| {correlation if correlation is not None else '-'} "
            f"| {row['status']} |"
        )
    lines += [
        "",
        "## 边界",
        "",
        "- 这是 5 日增量 IC 统计初筛，不是组合收益或交易结论。",
        "- 财报字段使用公告日下一交易日生效的 PIT 面板。",
        "- 当前 HS300 成分回填历史，幸存者偏差仍在。",
        "- 不生成订单、纸面目标或生产配置。",
        "",
    ]
    RESULT.write_text("\n".join(lines), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n",
        encoding="ascii",
    )


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R412 output exists: {path.name}")
    prereg, panel = load_inputs()
    work = prepare_core(prereg, panel)
    rows = {
        factor: evaluate_candidate(prereg, work, factor)
        for factor in prereg["candidates"]
    }
    classify(rows)
    payload = build_payload(prereg, rows)
    write_outputs(payload)
    print(RESULT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
