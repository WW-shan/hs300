#!/usr/bin/env python3
"""Pairwise crowding and incremental IC of IC-scan leaders versus core_v1 legs."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.backtest.engine import BacktestEngine
from app.backtest.factor import FactorBacktestService, FactorBatchConfig
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.sets import FactorSetStore
from app.factor_research.signal_scan import calculate_rank_ic
from app.factors.store import load_into_registry
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_IC = DATA / "factor_research" / "research" / "alt_hs300_rank_ic_5d_2026-11-10_v2.json"
DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "alt_hs300_incremental_2026-11-10.json"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 10, 14)
HORIZON = 5
METHOD_VERSION = "factor_increment_v1"


def _rank_score(factor_id: str, direction: str) -> pl.Expr:
    rank = pl.col(factor_id).rank(method="average").over("date")
    centered = rank - rank.mean().over("date")
    std = rank.std().over("date")
    score = pl.when(std > 1e-12).then(centered / std).otherwise(None)
    return (-score if direction == "low" else score).alias(f"_score_{factor_id}")


def _pairwise_correlation(panel: pl.DataFrame, score_columns: dict[str, str]) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {factor_id: {} for factor_id in score_columns}
    for left_id, left_col in score_columns.items():
        matrix[left_id][left_id] = 1.0
        for right_id, right_col in score_columns.items():
            if right_id <= left_id:
                continue
            pair = panel.select([left_col, right_col]).drop_nulls()
            value = pair.select(
                pl.corr(pl.col(left_col), pl.col(right_col), method="spearman")
            ).item() if pair.height >= 30 else None
            value = round(float(value), 6) if value is not None else None
            matrix[left_id][right_id] = value
            matrix[right_id][left_id] = value
    average_abs = {}
    for factor_id, row in matrix.items():
        peers = [abs(value) for key, value in row.items() if key != factor_id and value is not None]
        average_abs[factor_id] = round(sum(peers) / len(peers), 6) if peers else 0.0
    return matrix, average_abs


def _incremental(base_ic: pl.DataFrame, combined_ic: pl.DataFrame, direction: str) -> dict[str, Any]:
    sign = -1 if direction == "low" else 1
    paired = (
        base_ic.select(["date", pl.col("ic").alias("base_ic")])
        .join(combined_ic.select(["date", pl.col("ic").alias("combined_ic")]), on="date", how="inner")
        .sort("date")
    )
    delta = (paired["combined_ic"] - paired["base_ic"]).to_numpy() * sign
    nw = stats_v2.newey_west_t(delta, lag=HORIZON)
    recent = paired.filter(pl.col("date") >= RECENT_START)
    recent_delta = (recent["combined_ic"] - recent["base_ic"]).to_numpy() * sign
    return {
        "n_dates": int(delta.size),
        "base_ic_mean": round(float(paired["base_ic"].mean()), 6),
        "combined_ic_mean": round(float(paired["combined_ic"].mean()), 6),
        "directional_delta_mean": round(float(delta.mean()), 6) if delta.size else None,
        "delta_nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "delta_p_value": round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None,
        "recent_directional_delta_mean": round(float(recent_delta.mean()), 6) if recent_delta.size else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ic-research", type=Path, default=DEFAULT_IC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    ic_payload = json.loads(args.ic_research.read_text(encoding="utf-8"))
    promising = [
        item for item in ic_payload["results"].values()
        if item["tier"] == "promising"
    ]
    promising.sort(key=lambda item: -(item["variants"]["raw"]["full"]["directional_ic_mean"] or 0.0))
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise FileNotFoundError("factor catalog latest pointer missing")
    load_into_registry(DATA)
    sets = FactorSetStore(DATA, catalog=catalog)
    head = sets.head("core_v1")
    if head is None or head.watch_version is None:
        raise RuntimeError("core_v1 watch version is missing")
    core_version = sets.load_version("core_v1", head.watch_version)
    core_members = [
        item for item in core_version.members
        if item.role == "alpha" and item.membership_status == "watch"
    ]
    core_ids = [item.factor_id for item in core_members]
    core_weights = {item.factor_id: float(item.weight or 0.0) for item in core_members}
    candidate_ids = [item["factor_id"] for item in promising]
    all_ids = list(dict.fromkeys([*core_ids, *candidate_ids]))

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    service = FactorBacktestService(BacktestEngine(KlineRepository(DataStore(DATA))))
    config = FactorBatchConfig(
        factor_names=all_ids,
        symbols=symbols,
        start=START,
        end=END,
        n_groups=5,
        rebalance="daily",
        weight="equal",
        fees_pct=0.0004,
        commission_pct=0.0004,
        stamp_tax_pct=0.0005,
        slippage_bps=10.0,
        asset_type="stock",
        hs300=False,
    )
    generation = service._data_generation(config.asset_type)
    panel = service._load_factor_panel(config, all_ids, expected_generation=generation)
    trading_dates = service._global_trading_dates(config)
    panel = service._attach_shared_next_return(panel, config, trading_dates=trading_dates)
    panel = panel.filter(
        (pl.col("date") >= START)
        & (pl.col("date") <= END)
        & pl.col("_forward_return_5d").is_not_null()
    )

    directions = {item.factor_id: item.direction for item in catalog.factors}
    score_exprs = [_rank_score(factor_id, directions[factor_id]) for factor_id in all_ids if factor_id in panel.columns]
    panel = panel.with_columns(score_exprs)
    score_columns = {factor_id: f"_score_{factor_id}" for factor_id in all_ids if f"_score_{factor_id}" in panel.columns}

    numerator = pl.lit(0.0)
    denominator = pl.lit(0.0)
    for factor_id in core_ids:
        score = score_columns[factor_id]
        weight = core_weights[factor_id]
        numerator = numerator + pl.col(score).fill_null(0.0) * weight
        denominator = denominator + pl.when(pl.col(score).is_not_null()).then(weight).otherwise(0.0)
    panel = panel.with_columns(
        pl.when(denominator >= 0.5).then(numerator / denominator).otherwise(None).alias("_core_composite")
    )

    corr_matrix, average_abs_corr = _pairwise_correlation(panel, score_columns)
    incremental: dict[str, dict[str, Any]] = {}
    for item in promising:
        factor_id = item["factor_id"]
        score = score_columns[factor_id]
        working = panel.select([
            "date", "symbol", "_forward_return_5d", "_core_composite", score
        ]).filter(
            pl.col("_core_composite").is_not_null() & pl.col(score).is_not_null()
        ).with_columns(
            (0.8 * pl.col("_core_composite") + 0.2 * pl.col(score)).alias("_combined")
        )
        if working.is_empty():
            continue
        base_ic = calculate_rank_ic(working, "_core_composite", "_forward_return_5d", minimum=30)
        combined_ic = calculate_rank_ic(working, "_combined", "_forward_return_5d", minimum=30)
        record = _incremental(base_ic, combined_ic, item["direction"])
        record.update({
            "factor_id": factor_id,
            "data_class": item["data_class"],
            "raw_full_ic": item["variants"]["raw"]["full"]["directional_ic_mean"],
            "industry_size_neutral_ic": item["variants"]["industry_size_neutral"]["full"]["directional_ic_mean"] if item["variants"]["industry_size_neutral"] else None,
            "average_abs_correlation": average_abs_corr.get(factor_id),
            "max_correlation": max(
                (abs(value) for key, value in corr_matrix.get(factor_id, {}).items() if key != factor_id and value is not None),
                default=0.0,
            ),
            "q_value": None,
        })
        incremental[factor_id] = record

    p_values = [incremental[factor_id]["delta_p_value"] for factor_id in incremental]
    q_values = stats_v2.bh_fdr_qvalues(p_values)
    for factor_id, q_value in zip(incremental, q_values, strict=True):
        incremental[factor_id]["q_value"] = round(float(q_value), 8) if q_value is not None else None
    ranked = sorted(
        incremental.values(),
        key=lambda item: -(item["directional_delta_mean"] or -999.0),
    )
    payload = {
        "schema_version": 1,
        "research_id": sha256_json({
            "method_version": METHOD_VERSION,
            "ic_research_id": ic_payload.get("research_id"),
            "core_set": f"core_v1:v{core_version.version}",
            "candidates": candidate_ids,
        }),
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "ic_research_id": ic_payload.get("research_id"),
        "catalog_snapshot_id": catalog.snapshot_id,
        "core_set": {"set_id": "core_v1", "version": core_version.version, "members": core_ids},
        "candidate_count": len(candidate_ids),
        "method": {
            "signal_correlation": "average pairwise daily cross-sectional Spearman correlation",
            "incremental_ic": "base core equal-weight risk legs plus 20% candidate versus same-date base IC",
            "direction": "low-direction factors are multiplied by -1 before composite construction",
            "hac_lag": HORIZON,
            "multiple_testing": "BH-FDR across candidate incremental tests",
            "caveat": "watch-only research; no portfolio cost or capacity claim",
        },
        "average_abs_correlation": average_abs_corr,
        "correlation_matrix": corr_matrix,
        "incremental": incremental,
        "ranked_factor_ids": [item["factor_id"] for item in ranked],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_increment_scan_published"
        and record.input_fingerprints.get("research_id") == payload["research_id"]
        for record in ledger.records()
    ):
        ledger.append(
            "factor_increment_scan_published",
            payload={
                "catalog_snapshot_id": catalog.snapshot_id,
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "output_sha256": sha256_file(args.output),
                },
                "evidence_refs": [{
                    "kind": "factor_increment_scan",
                    "path": str(args.output.relative_to(DATA)),
                }],
                "metrics": {"candidate_count": len(candidate_ids)},
                "verdict": "watch_only",
            },
        )
    print(json.dumps({
        "research_id": payload["research_id"],
        "core_set_version": core_version.version,
        "candidate_count": len(candidate_ids),
        "output": str(args.output),
        "top_incremental": [
            {
                "factor_id": item["factor_id"],
                "delta_ic": item["directional_delta_mean"],
                "delta_t": item["delta_nw_t"],
                "q_value": item["q_value"],
                "avg_abs_corr": item["average_abs_correlation"],
                "max_corr": item["max_correlation"],
            }
            for item in ranked[:10]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
