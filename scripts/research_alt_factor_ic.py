#!/usr/bin/env python3
"""Audit-passing alt-factor rank-IC scan with industry and size neutralization."""
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
from app.factor_research.audit import load_audit
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import hash_token, sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.models import FactorAuditRecord
from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
from app.factors.store import load_into_registry
from app.services.industry_map import load_industry_l1
from app.strategy.scoring import neutralize_scoring_columns
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "alt_hs300_rank_ic_5d_2026-11-10_v2.json"
RECENT_START = date(2025, 10, 14)
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
HORIZON = 5
MINIMUM_CROSS_SECTION = 30

INDUSTRY_SPEC = {
    "industry": "industry_l1",
    "styles": [],
    "exclude": [],
    "shrink": 1.0,
}
SIZE_SPEC = {
    "industry": None,
    "styles": ["log_float_mv"],
    "exclude": [],
    "shrink": 1.0,
}
INDUSTRY_SIZE_SPEC = {
    "industry": "industry_l1",
    "styles": ["log_float_mv"],
    "exclude": [],
    "shrink": 1.0,
}


def _audit_factors(data_dir: Path, audit_id: str) -> tuple[dict[str, FactorAuditRecord], dict[str, dict[str, Any]], dict[str, Any]]:
    loaded = load_audit(data_dir, audit_id)
    if loaded is None:
        raise FileNotFoundError(audit_id)
    root = data_dir / "factor_research" / "audits" / hash_token(audit_id)
    metrics_path = root / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    factors = {
        str(factor_id): FactorAuditRecord.model_validate(payload)
        for factor_id, payload in metrics.get("factors", {}).items()
    }
    return factors, loaded["findings"], loaded["manifest"]


def _variant_ic(
    frame: pl.DataFrame,
    factor_id: str,
    spec: dict[str, Any] | None,
) -> pl.DataFrame:
    working = frame.select(["date", "symbol", "_forward_return_5d", factor_id, "industry_l1", "log_float_mv"])
    if spec is not None:
        working = neutralize_scoring_columns(working, [factor_id], spec)
    return calculate_rank_ic(
        working,
        factor_id,
        "_forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )


def _apply_qvalues(results: dict[str, dict[str, Any]], variant: str) -> None:
    p_values = [results[factor_id]["variants"][variant]["p_value"] if results[factor_id]["variants"][variant] else None for factor_id in results]
    q_values = stats_v2.bh_fdr_qvalues(p_values)
    for factor_id, q_value in zip(results, q_values, strict=True):
        if results[factor_id]["variants"][variant] is not None:
            results[factor_id]["variants"][variant]["q_value"] = round(float(q_value), 8) if q_value is not None else None


def _tier(raw: dict[str, Any], industry_size: dict[str, Any] | None) -> str:
    full = raw["full"]
    recent = raw["recent"]
    directional = full.get("directional_ic_mean")
    recent_directional = recent.get("directional_ic_mean")
    if directional is None or recent_directional is None:
        return "inconclusive"
    q_value = raw.get("q_value")
    consistency = raw.get("yearly_sign_consistency")
    if (
        q_value is not None
        and q_value <= 0.10
        and directional > 0
        and recent_directional > 0
        and (consistency or 0.0) >= 0.60
    ):
        return "promising"
    if directional > 0 and recent_directional > 0:
        return "watch"
    if industry_size is not None and (industry_size.get("full", {}).get("directional_ic_mean") or 0.0) > 0:
        return "neutralized_watch"
    return "weak_or_inconclusive"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--end", default=str(DEFAULT_END))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    load_into_registry(DATA)
    audit_factors, findings, manifest = _audit_factors(DATA, args.audit_id)
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise FileNotFoundError("factor catalog latest pointer missing")
    catalog_by_id = {item.factor_id: item for item in catalog.factors}
    eligible = [
        factor_id
        for factor_id, item in audit_factors.items()
        if findings.get(factor_id, {}).get("integrity") == "pass"
        and item.available
        and item.cross_section_type in {
            "stock_cross_sectional", "industry_cross_sectional", "event_sparse"
        }
    ]
    eligible.sort()
    if not eligible:
        raise RuntimeError("audit has no eligible cross-sectional factors")

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    engine = BacktestEngine(KlineRepository(DataStore(DATA)))
    service = FactorBacktestService(engine)
    config = FactorBatchConfig(
        factor_names=eligible,
        symbols=symbols,
        start=start,
        end=end,
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
    panel = service._load_factor_panel(config, eligible, expected_generation=generation)
    if panel.is_empty():
        raise RuntimeError("factor panel is empty")
    trading_dates = service._global_trading_dates(config)
    panel = service._attach_shared_next_return(panel, config, trading_dates=trading_dates)
    panel = service._compute_missing_factors(panel, {"log_float_mv"})
    industry = (
        load_industry_l1(DATA)
        .select(["symbol", "industry_l1"])
        .unique(subset=["symbol"], keep="last")
    )
    panel = panel.join(industry, on="symbol", how="left")
    panel = panel.filter(
        (pl.col("date") >= start)
        & (pl.col("date") <= end)
        & pl.col("_forward_return_5d").is_not_null()
    )
    available = sorted(set(eligible) & set(panel.columns))
    if not available:
        raise RuntimeError("no eligible factor columns materialized")

    results: dict[str, dict[str, Any]] = {}
    for factor_id in available:
        audit = audit_factors[factor_id]
        direction = catalog_by_id[factor_id].direction
        frame = panel.select([
            "date", "symbol", "_forward_return_5d", factor_id, "industry_l1", "log_float_mv"
        ])
        raw_ic = _variant_ic(frame, factor_id, None)
        raw_summary = summarise_ic(raw_ic, recent_start=RECENT_START, direction=direction)
        variants: dict[str, Any] = {"raw": raw_summary}
        if audit.cross_section_type != "industry_cross_sectional":
            variants["industry_neutral"] = summarise_ic(
                _variant_ic(frame, factor_id, INDUSTRY_SPEC),
                recent_start=RECENT_START,
                direction=direction,
            )
            variants["size_neutral"] = summarise_ic(
                _variant_ic(frame, factor_id, SIZE_SPEC),
                recent_start=RECENT_START,
                direction=direction,
            )
            variants["industry_size_neutral"] = summarise_ic(
                _variant_ic(frame, factor_id, INDUSTRY_SIZE_SPEC),
                recent_start=RECENT_START,
                direction=direction,
            )
        else:
            variants["industry_neutral"] = None
            variants["size_neutral"] = None
            variants["industry_size_neutral"] = None
        results[factor_id] = {
            "factor_id": factor_id,
            "data_class": audit.data_class,
            "cross_section_type": audit.cross_section_type,
            "pit_status": audit.pit_status,
            "direction": direction,
            "variants": variants,
        }

    for variant in ("raw", "industry_neutral", "size_neutral", "industry_size_neutral"):
        _apply_qvalues(results, variant)
    for item in results.values():
        raw = item["variants"]["raw"]
        neutral = item["variants"]["industry_size_neutral"]
        item["tier"] = _tier(raw, neutral)

    ranked = sorted(
        results.values(),
        key=lambda item: (
            -(abs(item["variants"]["raw"]["full"]["directional_ic_mean"] or 0.0)),
            -(item["variants"]["raw"]["nw_t"] or -999.0),
        ),
    )
    payload = {
        "schema_version": 1,
        "research_id": sha256_json({
            "audit_id": args.audit_id,
            "start": str(start),
            "end": str(end),
            "horizon": HORIZON,
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "factors": available,
        }),
        "generated_at": utc_now(),
        "audit_id": args.audit_id,
        "audit_generated_at": manifest.get("generated_at"),
        "catalog_snapshot_id": catalog.snapshot_id,
        "period": {"start": str(start), "end": str(end), "recent_start": str(RECENT_START)},
        "return_horizon_days": HORIZON,
        "minimum_cross_section": MINIMUM_CROSS_SECTION,
        "universe": {
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
            "note": "整段回测使用当前沪深300名单，结果存在幸存者偏差。",
        },
        "method": {
            "ic": "daily cross-sectional Spearman rank IC versus 5-day close-to-close forward return",
            "nw": "Newey-West HAC with lag=5 for overlapping 5-day returns",
            "multiple_testing": "Benjamini-Hochberg FDR within each neutralization variant",
            "industry_neutral": "within-date, within-industry demean and z-score",
            "size_neutral": "daily cross-sectional OLS residual versus log_float_mv",
            "industry_size_neutral": "industry demeaning followed by size residualization and within-industry z-score",
            "no_leverage": True,
        },
        "excluded_market_level_factors": sorted(
            factor_id for factor_id, item in audit_factors.items()
            if findings.get(factor_id, {}).get("integrity") == "pass"
            and item.cross_section_type == "market_level"
        ),
        "factor_count": len(results),
        "results": results,
        "ranked_factor_ids": [item["factor_id"] for item in ranked],
        "tier_counts": {
            tier: sum(1 for item in results.values() if item["tier"] == tier)
            for tier in sorted({item["tier"] for item in results.values()})
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_signal_scan_published"
        and record.input_fingerprints.get("research_id") == payload["research_id"]
        for record in ledger.records()
    ):
        ledger.append(
            "factor_signal_scan_published",
            payload={
                "catalog_snapshot_id": catalog.snapshot_id,
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "audit_id": args.audit_id,
                    "output_sha256": sha256_file(args.output),
                },
                "evidence_refs": [{
                    "kind": "factor_signal_scan",
                    "path": str(args.output.relative_to(DATA)),
                }],
                "metrics": {
                    "factor_count": len(results),
                    "horizon_days": HORIZON,
                    "tier_counts": payload["tier_counts"],
                },
                "verdict": "watch_only",
            },
        )
    print(json.dumps({
        "research_id": payload["research_id"],
        "factor_count": len(results),
        "tier_counts": payload["tier_counts"],
        "output": str(args.output),
        "top_10": [
            {
                "factor_id": item["factor_id"],
                "directional_ic": item["variants"]["raw"]["full"]["directional_ic_mean"],
                "nw_t": item["variants"]["raw"]["nw_t"],
                "q_value": item["variants"]["raw"]["q_value"],
                "recent_ic": item["variants"]["raw"]["recent"]["directional_ic_mean"],
                "yearly_sign_consistency": item["variants"]["raw"]["yearly_sign_consistency"],
                "tier": item["tier"],
            }
            for item in ranked[:10]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
