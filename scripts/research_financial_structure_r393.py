#!/usr/bin/env python3
"""R393 financial structure/cash-quality factor statistical research."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "r393_financial_structure_2026-11-22.json"
P7_RESEARCH_ID = "sha256:c6d20e2f1c402c41f3baa020ad58775dd8c39712eac4abfae29d617fb13d38cf"
P6_RESEARCH_ID = "sha256:27168a49c05d64e163c22ca0bb6d7cec00f763f8a0613cfc8bd048c6fe86e50c"
FACTOR_IDS = (
    "sales_expense_ratio_ttm_low",
    "management_expense_ratio_ttm_low",
    "ocf_to_revenue_ttm",
    "fcf_proxy_to_revenue_ttm",
    "cash_to_assets_latest",
    "payables_to_cogs_ttm",
)
DIRECTIONS = {
    "sales_expense_ratio_ttm_low": "low",
    "management_expense_ratio_ttm_low": "low",
    "ocf_to_revenue_ttm": "high",
    "fcf_proxy_to_revenue_ttm": "high",
    "cash_to_assets_latest": "high",
    "payables_to_cogs_ttm": "low",
}


def _load_p8():
    path = ROOT / "scripts" / "research_financial_increment_p8.py"
    spec = importlib.util.spec_from_file_location("r393_p8", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_audit() -> dict[str, Any]:
    path = DATA / "factor_research" / "pit" / "runs" / f"{P7_RESEARCH_ID.removeprefix('sha256:')}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("research_id") != P7_RESEARCH_ID or payload.get("status") != "verified":
        raise ValueError("R393 P7 audit identity/status mismatch")
    missing = [fid for fid in FACTOR_IDS if fid not in set(payload.get("verified_factor_ids", []))]
    if missing:
        raise ValueError(f"R393 factors not P7 verified: {missing}")
    return payload


def candidate_rows() -> list[dict[str, Any]]:
    return [
        {
            "candidate_key": f"r393_{fid}",
            "economic_key": fid,
            "label": fid,
            "source_research": "p7",
            "factor_id": fid,
            "direction": DIRECTIONS[fid],
            "registered": True,
            "transform": "pit_latest",
            "set_role": "new",
            "replaces_factor_id": None,
            "dedupe_priority": 0,
        }
        for fid in FACTOR_IDS
    ]


def load_panel(data_dir: Path, symbols: list[str], factor_ids: tuple[str, ...]):
    p8 = _load_p8()
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    requested = list(dict.fromkeys(factor_ids))
    audit = load_audit()
    verified = set(audit.get("verified_factor_ids", []))
    missing = sorted(set(requested) - verified)
    if missing:
        raise ValueError(f"R393 factors not P7 verified: {missing}")
    panel, _ = p8.p5.load_candidate_panel(Path(data_dir), list(symbols), [])
    snapshot = load_fundamental_snapshot(Path(data_dir))
    if snapshot is None or snapshot.is_empty():
        raise FileNotFoundError("financial snapshot is empty")
    panel = attach_fundamental_factors(panel, snapshot, requested)
    missing_columns = sorted(set(requested) - set(panel.columns))
    if missing_columns:
        raise ValueError(f"R393 factors not materialized: {missing_columns}")
    coverage = {}
    for factor_id in requested:
        non_null = panel.filter(pl.col(factor_id).is_not_null())
        if non_null.is_empty():
            raise ValueError(f"R393 factor has no non-null rows: {factor_id}")
        coverage[factor_id] = {
            "rows": panel.height,
            "non_null_rows": non_null.height,
            "non_null_ratio": round(non_null.height / panel.height, 6),
            "symbol_count": int(non_null["symbol"].n_unique()),
            "first_value_date": str(non_null["date"].min()),
        }
    return panel, coverage


def run() -> dict[str, Any]:
    p8 = _load_p8()
    p5 = p8.p5
    candidates = candidate_rows()
    from app.factor_research.catalog import load_latest_catalog
    from app.factor_research.sets import FactorSetStore
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog missing")
    sets = FactorSetStore(DATA, catalog=catalog)
    head = sets.head("multi_dimension_v1")
    if head is None or head.watch_version is None:
        raise RuntimeError("multi_dimension_v1 watch version missing")
    version = sets.load_version("multi_dimension_v1", head.watch_version)
    weights, directions, _current_ids = p5._current_inputs(version)
    from app.hs300.current import current_symbols
    symbols = current_symbols()
    panel, coverage = load_panel(DATA, symbols, FACTOR_IDS)
    scored, marginal = p5.score_candidates(panel, candidates, weights, directions)
    records = p8.gate_financial_candidates(candidates, marginal)
    return {
        "round": "R393",
        "method_version": "financial_structure_r393_stats_v1",
        "p7_research_id": P7_RESEARCH_ID,
        "p6_research_id": P6_RESEARCH_ID,
        "candidate_count": len(candidates),
        "panel_rows": scored.height,
        "symbol_count": len(symbols),
        "coverage": coverage,
        "candidates": records,
        "statistical_pass_count": sum(1 for row in records if row.get("statistical_gate", {}).get("pass")),
        "decision": {
            "status": "stats_only_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_count": payload["candidate_count"], "statistical_pass_count": payload["statistical_pass_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
