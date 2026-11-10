#!/usr/bin/env python3
"""Run a watch factor set through the project T+1 portfolio backtest path."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factor_research.fingerprints import sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json
from app.factor_research.sets import FactorSetStore
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA / "factor_research" / "research"
    / "multi_dimension_v1_portfolio_2026-11-10.json"
)
FULL_START = date(2021, 6, 1)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
DEFAULT_POSITIONS = 20
POSITION_CAPACITIES = (10, 20, 30)
METHOD_VERSION = "factor_set_portfolio_v1"
ROLLING_STARTS = (
    date(2021, 7, 1),
    date(2021, 10, 8),
    date(2022, 1, 4),
    date(2022, 4, 1),
    date(2022, 7, 1),
    date(2022, 10, 10),
    date(2023, 1, 3),
    date(2023, 4, 3),
    date(2023, 7, 3),
    date(2023, 10, 9),
    date(2024, 1, 2),
    date(2024, 4, 1),
    date(2024, 7, 1),
    date(2024, 10, 8),
    date(2025, 1, 2),
    date(2025, 4, 1),
    date(2025, 7, 1),
    date(2025, 10, 14),
)
COST_SCENARIOS = {
    "1x": {
        "fees_pct": 0.0002,
        "commission_pct": 0.0002,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 5.0,
    },
    "2x": {
        "fees_pct": 0.0004,
        "commission_pct": 0.0004,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 10.0,
    },
    "3x": {
        "fees_pct": 0.0006,
        "commission_pct": 0.0006,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 15.0,
    },
}


def _cost_parameters(label: str) -> dict[str, float]:
    try:
        return dict(COST_SCENARIOS[label])
    except KeyError as exc:
        raise ValueError(f"unsupported cost scenario: {label}") from exc


def _one_year_end(start: date) -> date:
    try:
        return date(start.year + 1, start.month, start.day) - timedelta(days=1)
    except ValueError:  # Feb 29
        return date(start.year + 1, start.month, start.day - 1) - timedelta(days=1)


def _rolling_year_windows() -> list[tuple[date, date]]:
    return [(start, _one_year_end(start)) for start in ROLLING_STARTS]


def _summarize_runs(records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [record for record in records if record.get("error") is None]

    def values(key: str) -> list[float]:
        return [
            float(record["stats"][key])
            for record in usable
            if record.get("stats", {}).get(key) is not None
        ]

    totals = values("total_return")
    annuals = values("annual_return")
    sharpes = values("sharpe")
    drawdowns = values("max_drawdown")
    trade_counts = values("n_trades")

    def median(items: list[float]) -> float | None:
        return round(statistics.median(items), 6) if items else None

    return {
        "n": len(usable),
        "positive": sum(value > 0 for value in totals),
        "positive_rate": round(sum(value > 0 for value in totals) / len(totals), 6)
        if totals else None,
        "median_total_return": median(totals),
        "min_total_return": round(min(totals), 6) if totals else None,
        "max_total_return": round(max(totals), 6) if totals else None,
        "median_annual_return": median(annuals),
        "median_sharpe": median(sharpes),
        "worst_max_drawdown": round(min(drawdowns), 6) if drawdowns else None,
        "median_n_trades": median(trade_counts),
    }


def _service() -> StrategyBacktestService:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[
            Path(__file__).resolve().parents[1] / "backend" / "app" / "strategy" / "research",
            DATA / "strategies" / "ai",
        ],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(
        BacktestEngine(KlineRepository(DataStore(DATA))),
        engine,
    )


def _portfolio_inputs(version: Any) -> tuple[dict[str, float], dict[str, str], list[str]]:
    members = [
        member for member in version.members
        if member.role == "alpha" and member.membership_status in {"watch", "active"}
    ]
    if not members:
        raise ValueError(f"factor set {version.set_id} v{version.version} has no research alpha members")
    raw_weights = {
        member.factor_id: float(member.weight) if member.weight is not None else 1.0
        for member in members
    }
    total = sum(raw_weights.values())
    if total <= 0:
        raise ValueError("factor set alpha weights must sum to a positive value")
    scoring = {factor_id: weight / total for factor_id, weight in raw_weights.items()}
    directions = {member.factor_id: member.direction for member in members}
    if any(direction not in {"high", "low"} for direction in directions.values()):
        raise ValueError("factor set alpha member has invalid direction")
    return scoring, directions, sorted(scoring)


def _backtest_config(
    scoring: dict[str, float],
    directions: dict[str, str],
    *,
    start: date,
    end: date,
    max_positions: int,
    cost: dict[str, float],
) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id="factor_rank_research",
        symbols=None,
        start=start,
        end=end,
        params={
            "scoring": scoring,
            "directions": directions,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "top_rank": max(20, max_positions),
        },
        overrides={},
        matching="open_t+1",
        fees_pct=cost["fees_pct"],
        commission_pct=cost["commission_pct"],
        stamp_tax_pct=cost["stamp_tax_pct"],
        slippage_bps=cost["slippage_bps"],
        max_positions=max_positions,
        max_exposure_pct=1.0,
        initial_capital=1_000_000.0,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        holding_days=30,
        hs300=True,
    )


def _curve_return(curve: list[dict[str, Any]]) -> float | None:
    if not curve:
        return None
    first = float(curve[0].get("value") or 0.0)
    last = float(curve[-1].get("value") or 0.0)
    return round(last / first - 1.0, 6) if first else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _run(
    service: StrategyBacktestService,
    scoring: dict[str, float],
    directions: dict[str, str],
    *,
    label: str,
    start: date,
    end: date,
    max_positions: int,
    cost_label: str,
) -> dict[str, Any]:
    config = _backtest_config(
        scoring,
        directions,
        start=start,
        end=end,
        max_positions=max_positions,
        cost=_cost_parameters(cost_label),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(f"{label} failed: {result.error}")
    record = {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "max_positions": max_positions,
        "top_rank": max(20, max_positions),
        "error": None,
        "stats": dict(result.stats),
        "trade_count": len(result.trades),
        "benchmark_return": _curve_return(result.benchmark_curve),
    }
    print(
        f"{label}: return={result.stats.get('total_return')} "
        f"annual={result.stats.get('annual_return')} "
        f"sharpe={result.stats.get('sharpe')} mdd={result.stats.get('max_drawdown')}",
        flush=True,
    )
    return record


def _checkpoints(
    results: dict[str, dict[str, Any]],
    rolling: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    full = results["full_p20_2x"]["stats"]
    recent = results["recent_p20_2x"]["stats"]
    stress = results["full_p20_3x"]["stats"]
    rolling_2x = rolling["p20_2x"]["summary"]
    rolling_3x = rolling["p20_3x"]["summary"]
    return {
        "full_2x_annual_positive": (full.get("annual_return") or -1.0) > 0.0,
        "full_2x_sharpe_at_least_0_5": (full.get("sharpe") or -99.0) >= 0.5,
        "full_2x_max_drawdown_at_least_minus_25pct": (
            full.get("max_drawdown") or -1.0
        ) >= -0.25,
        "recent_2x_total_return_positive": (recent.get("total_return") or -1.0) > 0.0,
        "full_3x_annual_positive": (stress.get("annual_return") or -1.0) > 0.0,
        "rolling_2x_positive_rate_at_least_70pct": (
            rolling_2x.get("positive_rate") or 0.0
        ) >= 0.70,
        "rolling_2x_median_return_positive": (
            rolling_2x.get("median_total_return") or -1.0
        ) > 0.0,
        "rolling_3x_positive_rate_at_least_70pct": (
            rolling_3x.get("positive_rate") or 0.0
        ) >= 0.70,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-id", default="multi_dimension_v1")
    parser.add_argument("--version", type=int, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing research output: {args.output}")

    sets = FactorSetStore(DATA)
    head = sets.head(args.set_id)
    if head is None:
        raise FileNotFoundError(f"factor set head not found: {args.set_id}")
    if head.state != "watch" or head.active_version is not None:
        raise ValueError("portfolio research only accepts a watch set with active_version=null")
    version_number = args.version or head.watch_version
    if version_number is None:
        raise ValueError(f"factor set {args.set_id} has no watch version")
    version = sets.load_version(args.set_id, version_number)
    scoring, directions, factor_ids = _portfolio_inputs(version)
    service = _service()

    run_cache: dict[str, dict[str, Any]] = {}

    def run_once(
        *,
        label: str,
        start: date,
        end: date,
        max_positions: int,
        cost_label: str,
    ) -> dict[str, Any]:
        if label not in run_cache:
            run_cache[label] = _run(
                service,
                scoring,
                directions,
                label=label,
                start=start,
                end=end,
                max_positions=max_positions,
                cost_label=cost_label,
            )
        return run_cache[label]

    for positions in (10, 20, 30):
        for cost_label in ("1x", "2x", "3x"):
            for window, start in (("full", FULL_START), ("recent", RECENT_START)):
                run_once(
                    label=f"{window}_p{positions}_{cost_label}",
                    start=start,
                    end=END,
                    max_positions=positions,
                    cost_label=cost_label,
                )

    rolling: dict[str, dict[str, Any]] = {}
    windows = _rolling_year_windows()
    for positions, cost_label in ((10, "2x"), (20, "2x"), (20, "3x")):
        records = [
            run_once(
                label=f"rolling_p{positions}_{cost_label}_{start}",
                start=start,
                end=end,
                max_positions=positions,
                cost_label=cost_label,
            )
            for start, end in windows
        ]
        rolling[f"p{positions}_{cost_label}"] = {
            "summary": _summarize_runs(records),
            "windows": records,
        }

    results = dict(sorted(run_cache.items()))
    checkpoints = _checkpoints(results, rolling)
    research_id = sha256_json({
        "method_version": METHOD_VERSION,
        "set_id": version.set_id,
        "set_version": version.version,
        "content_sha256": version.content_sha256,
        "catalog_snapshot_id": version.catalog_snapshot_id,
        "period": [str(FULL_START), str(END)],
        "entry_score": ENTRY_SCORE,
        "exit_score": EXIT_SCORE,
        "position_capacities": POSITION_CAPACITIES,
        "rolling_starts": [str(start) for start in ROLLING_STARTS],
        "costs": COST_SCENARIOS,
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
            "factor_ids": factor_ids,
            "scoring": scoring,
            "directions": directions,
        },
        "engine": {
            "name": "StrategyBacktestService",
            "strategy_id": "factor_rank_research",
            "execution": "open_t+1",
            "max_exposure_pct": 1.0,
            "leverage": "none",
        },
        "universe": {
            "name": "current_hs300",
            "survivorship_bias": True,
        },
        "periods": {
            "full_start": str(FULL_START),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "rolling_year_windows": [
                {"start": str(start), "end": str(end)} for start, end in windows
            ],
        },
        "parameters": {
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "top_rank": "max(20, max_positions)",
            "position_capacities": list(POSITION_CAPACITIES),
            "default_positions": DEFAULT_POSITIONS,
        },
        "costs": COST_SCENARIOS,
        "results": results,
        "rolling_year": rolling,
        "research_checkpoints": checkpoints,
        "all_research_checkpoints_pass": all(checkpoints.values()),
        "decision": {
            "status": "research_only_watch_candidate",
            "preferred_position_count": DEFAULT_POSITIONS,
            "active_version": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "caveats": [
            "Current HS300 membership is applied to history; survivorship bias remains.",
            "No leverage; maximum exposure is 100%.",
            "Position-count sensitivity is not an AUM market-impact or liquidity-capacity model.",
            "Rolling 12-month windows overlap and are not independent observations.",
            "Extension and financial factor PIT evidence is declared, not fully field-level verified.",
            "The factor set remains watch-only and is not a deployment approval.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output, payload)
    output_sha = _sha256_file(args.output)
    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_set_portfolio_published"
        and record.input_fingerprints.get("research_id") == research_id
        and record.input_fingerprints.get("output_sha256") == output_sha
        for record in ledger.records()
    ):
        ledger.append(
            "factor_set_portfolio_published",
            payload={
                "set_id": version.set_id,
                "set_version": version.version,
                "catalog_snapshot_id": version.catalog_snapshot_id,
                "evidence_refs": [{
                    "kind": "factor_set_portfolio_backtest",
                    "path": str(args.output.relative_to(DATA)),
                    "sha256": output_sha,
                }],
                "input_fingerprints": {
                    "research_id": research_id,
                    "set_content_sha256": version.content_sha256,
                    "output_sha256": output_sha,
                },
                "metrics": {
                    "position_capacities": list(POSITION_CAPACITIES),
                    "rolling_window_count": len(windows),
                    "all_research_checkpoints_pass": all(checkpoints.values()),
                },
                "verdict": "research_only_watch_candidate",
            },
        )
    print(json.dumps({
        "research_id": research_id,
        "output": str(args.output),
        "full_p20_2x": results["full_p20_2x"]["stats"],
        "recent_p20_2x": results["recent_p20_2x"]["stats"],
        "rolling_p20_2x": rolling["p20_2x"]["summary"],
        "rolling_p20_3x": rolling["p20_3x"]["summary"],
        "checkpoints": checkpoints,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
