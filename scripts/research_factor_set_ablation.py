#!/usr/bin/env python3
"""Leave-one-out and single-factor ablation for a watch factor set."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json
from app.factor_research.sets import FactorSetStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PORTFOLIO_SCRIPT = ROOT / "scripts" / "research_factor_set_portfolio.py"
DEFAULT_OUTPUT = (
    DATA / "factor_research" / "research"
    / "multi_dimension_v1_ablation_2026-11-10.json"
)
END = date(2026, 11, 5)
METHOD_VERSION = "factor_set_ablation_v1"
WINDOWS = {
    "full": (date(2021, 6, 1), END),
    "recent": (date(2025, 10, 14), END),
    "weak_2025h2_2026h1": (date(2025, 7, 1), date(2026, 6, 30)),
}


def _load_portfolio_module():
    spec = importlib.util.spec_from_file_location("factor_set_portfolio_research", PORTFOLIO_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load portfolio research helper: {PORTFOLIO_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _short_name(factor_id: str) -> str:
    return factor_id.removeprefix("ext_alt_hs300_")


def _variant_factor_sets(factors: list[str]) -> dict[str, list[str]]:
    ordered = list(factors)
    variants = {"all_factors": ordered}
    for factor_id in ordered:
        short = _short_name(factor_id)
        variants[f"drop_{short}"] = [item for item in ordered if item != factor_id]
        variants[f"single_{short}"] = [factor_id]
    return variants


def _variant_inputs(
    all_scoring: dict[str, float],
    all_directions: dict[str, str],
    factor_ids: list[str],
) -> tuple[dict[str, float], dict[str, str]]:
    weights = {factor_id: all_scoring[factor_id] for factor_id in factor_ids}
    total = sum(weights.values())
    scoring = {factor_id: weight / total for factor_id, weight in weights.items()}
    directions = {factor_id: all_directions[factor_id] for factor_id in factor_ids}
    return scoring, directions


def _factor_effects(
    window_results: dict[str, dict[str, Any]],
    factors: list[str],
) -> dict[str, dict[str, float | None]]:
    baseline = float(window_results["all_factors"]["stats"]["total_return"])
    effects: dict[str, dict[str, float | None]] = {}
    for factor_id in factors:
        short = _short_name(factor_id)
        drop_return = float(window_results[f"drop_{short}"]["stats"]["total_return"])
        single_return = float(window_results[f"single_{short}"]["stats"]["total_return"])
        effects[factor_id] = {
            "baseline_total_return": round(baseline, 6),
            "drop_total_return": round(drop_return, 6),
            "drop_delta": round(drop_return - baseline, 6),
            "marginal_contribution": round(baseline - drop_return, 6),
            "single_total_return": round(single_return, 6),
            "single_delta": round(single_return - baseline, 6),
        }
    return effects


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-id", default="multi_dimension_v1")
    parser.add_argument("--version", type=int, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing ablation output: {args.output}")

    portfolio = _load_portfolio_module()
    sets = FactorSetStore(DATA)
    head = sets.head(args.set_id)
    if head is None:
        raise FileNotFoundError(f"factor set head not found: {args.set_id}")
    if head.state != "watch" or head.active_version is not None:
        raise ValueError("factor ablation only accepts a watch set with active_version=null")
    version_number = args.version or head.watch_version
    if version_number is None:
        raise ValueError(f"factor set {args.set_id} has no watch version")
    version = sets.load_version(args.set_id, version_number)
    all_scoring, all_directions, factors = portfolio._portfolio_inputs(version)
    variants = _variant_factor_sets(factors)
    service = portfolio._service()
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for window, (start, end) in WINDOWS.items():
        results[window] = {}
        for variant, factor_ids in variants.items():
            scoring, directions = _variant_inputs(all_scoring, all_directions, factor_ids)
            results[window][variant] = portfolio._run(
                service,
                scoring,
                directions,
                label=f"{window}_{variant}",
                start=start,
                end=end,
                max_positions=portfolio.DEFAULT_POSITIONS,
                cost_label="2x",
            )

    effects = {
        window: _factor_effects(window_results, factors)
        for window, window_results in results.items()
    }
    ranking = {
        window: sorted(
            (
                {"factor_id": factor_id, **values}
                for factor_id, values in factor_effects.items()
            ),
            key=lambda item: float(item["marginal_contribution"] or 0.0),
            reverse=True,
        )
        for window, factor_effects in effects.items()
    }
    weak_window = WINDOWS["weak_2025h2_2026h1"]
    baseline_weak = results["weak_2025h2_2026h1"]["all_factors"]["stats"]["total_return"]
    weak_ranking = ranking["weak_2025h2_2026h1"]
    research_id = sha256_json({
        "method_version": METHOD_VERSION,
        "set_id": version.set_id,
        "set_version": version.version,
        "content_sha256": version.content_sha256,
        "windows": {key: [str(start), str(end)] for key, (start, end) in WINDOWS.items()},
        "positions": portfolio.DEFAULT_POSITIONS,
        "cost": "2x",
        "variants": variants,
    })
    payload = {
        "schema_version": 1,
        "research_id": research_id,
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "factor_set": {
            "set_id": version.set_id,
            "version": version.version,
            "content_sha256": version.content_sha256,
            "catalog_snapshot_id": version.catalog_snapshot_id,
            "membership_status": "watch",
            "active_version": None,
            "factor_ids": factors,
        },
        "engine": {
            "name": "StrategyBacktestService",
            "strategy_id": "factor_rank_research",
            "execution": "open_t+1",
            "max_positions": portfolio.DEFAULT_POSITIONS,
            "cost": "2x",
            "leverage": "none",
        },
        "windows": {
            key: {"start": str(start), "end": str(end)}
            for key, (start, end) in WINDOWS.items()
        },
        "variants": variants,
        "results": results,
        "factor_effects": effects,
        "factor_ranking_by_window": ranking,
        "weak_window_diagnosis": {
            "window": "weak_2025h2_2026h1",
            "start": str(weak_window[0]),
            "end": str(weak_window[1]),
            "baseline_total_return": round(float(baseline_weak), 6),
            "most_helpful_factor": weak_ranking[0],
            "most_harmful_factor": weak_ranking[-1],
        },
        "decision": {
            "status": "research_only_watch_candidate",
            "active_version": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "caveats": [
            "Drop-one and single-factor variants change both composition and score normalization; effects are descriptive, not causal.",
            "The weak window is selected after observing the rolling-year result and is therefore a diagnostic subsample, not independent validation.",
            "Current HS300 membership introduces survivorship bias.",
            "The factor set remains watch-only and is not a deployment approval.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output, payload)
    output_sha = _sha256_file(args.output)
    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_set_ablation_published"
        and record.input_fingerprints.get("research_id") == research_id
        and record.input_fingerprints.get("output_sha256") == output_sha
        for record in ledger.records()
    ):
        ledger.append(
            "factor_set_ablation_published",
            payload={
                "set_id": version.set_id,
                "set_version": version.version,
                "catalog_snapshot_id": version.catalog_snapshot_id,
                "evidence_refs": [{
                    "kind": "factor_set_ablation",
                    "path": str(args.output.relative_to(DATA)),
                    "sha256": output_sha,
                }],
                "input_fingerprints": {
                    "research_id": research_id,
                    "set_content_sha256": version.content_sha256,
                    "output_sha256": output_sha,
                },
                "metrics": {
                    "variant_count": len(variants),
                    "window_count": len(WINDOWS),
                    "baseline_weak_total_return": round(float(baseline_weak), 6),
                },
                "verdict": "research_only_watch_candidate",
            },
        )
    print(json.dumps({
        "research_id": research_id,
        "output": str(args.output),
        "baselines": {
            window: results[window]["all_factors"]["stats"]["total_return"]
            for window in WINDOWS
        },
        "weak_window_diagnosis": payload["weak_window_diagnosis"],
        "full_rank": ranking["full"][:3],
        "recent_rank": ranking["recent"][:3],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
