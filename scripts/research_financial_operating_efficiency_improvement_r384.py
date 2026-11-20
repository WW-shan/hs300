#!/usr/bin/env python3
"""R384 research for PIT operating-efficiency improvement factors."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.backtest.fundamentals import (
    attach_fundamental_factors,
    load_fundamental_snapshot,
)
from app.factor_research.automation import bind_research_evidence
from app.factor_research.fingerprints import sha256_json, utc_now
from app.factor_research.signal_scan import calculate_rank_ic
from app.strategy.scoring import neutralize_scoring_columns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA
    / "factor_research"
    / "research"
    / "r384_financial_operating_efficiency_improvement_2026-11-21.json"
)
METHOD_VERSION = "financial_operating_efficiency_improvement_r384_v1"
OPERATION = "financial_operating_efficiency_improvement_scan_published"
P7_RESEARCH_ID = (
    "sha256:c4b0b1e460c58f2f7facc0b2237dd834ab7bc494ba6225dc0194ba74b2e2bd32"
)
P6_ADMISSION_RESEARCH_ID = (
    "sha256:1513dbc1e614f6ab31552bdb18771bdecf3162ad3b343e2681bda9b2e9cba298"
)
FORWARD_HORIZONS = (5, 20, 60)
PRIMARY_HORIZON = 60
NEUTRAL_VARIANTS = ("industry", "size", "industry_size")
EXCLUDED_INDUSTRIES = ("银行", "非银金融")
FACTOR_DIRECTIONS = {
    "inventory_turnover_improvement_yoy": "high",
    "receivable_turnover_improvement_yoy": "high",
    "receivable_growth_minus_revenue_growth": "low",
    "inventory_growth_minus_cogs_growth": "low",
}
CANDIDATES = (
    {
        "candidate_key": "r384_inventory_turnover_improvement_yoy",
        "economic_key": "inventory_turnover_improvement_yoy",
        "label": "存货周转同比改善",
        "factor_id": "inventory_turnover_improvement_yoy",
        "direction": "high",
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    },
    {
        "candidate_key": "r384_receivable_turnover_improvement_yoy",
        "economic_key": "receivable_turnover_improvement_yoy",
        "label": "应收周转同比改善",
        "factor_id": "receivable_turnover_improvement_yoy",
        "direction": "high",
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    },
    {
        "candidate_key": "r384_receivable_growth_minus_revenue_growth",
        "economic_key": "receivable_growth_minus_revenue_growth",
        "label": "应收相对营收增速差",
        "factor_id": "receivable_growth_minus_revenue_growth",
        "direction": "low",
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    },
    {
        "candidate_key": "r384_inventory_growth_minus_cogs_growth",
        "economic_key": "inventory_growth_minus_cogs_growth",
        "label": "存货相对营业成本增速差",
        "factor_id": "inventory_growth_minus_cogs_growth",
        "direction": "low",
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    },
)


def _load_sibling(name: str):
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"r384_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sibling research module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p8 = _load_sibling("research_financial_increment_p8")
p5 = p8.p5


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _audit_core(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }


def load_p7_audit(data_dir: Path) -> dict[str, Any]:
    """Load and authenticate the exact P7 snapshot pre-registered by R384."""
    stem = P7_RESEARCH_ID.removeprefix("sha256:")
    run_path = Path(data_dir) / "factor_research" / "pit" / "runs" / f"{stem}.json"
    if not run_path.is_file():
        raise FileNotFoundError(run_path)
    payload = _read_json(run_path)
    if payload.get("research_id") != P7_RESEARCH_ID:
        raise ValueError("R384 P7 research ID does not match run JSON")
    if sha256_json(_audit_core(payload)) != P7_RESEARCH_ID:
        raise ValueError("R384 P7 run JSON canonical identity mismatch")
    if payload.get("status") != "verified":
        raise ValueError("R384 P7 financial audit is not fully verified")
    verified = set(str(item) for item in payload.get("verified_factor_ids", []))
    factors = payload.get("factors", {})
    missing = sorted(
        factor_id
        for factor_id in FACTOR_DIRECTIONS
        if factor_id not in verified
        or not isinstance(factors, Mapping)
        or not isinstance(factors.get(factor_id), Mapping)
        or factors[factor_id].get("verified") is not True
    )
    if missing:
        raise ValueError(f"operating-efficiency improvement factors not P7-verified: {missing}")
    return payload


def load_research_panel(
    data_dir: Path,
    symbols: Sequence[str],
    factor_ids: Sequence[str],
    audit: Mapping[str, Any],
) -> pl.DataFrame:
    """Build the common daily panel and attach only PIT-gated R384 factors."""
    requested = list(dict.fromkeys(str(item) for item in factor_ids))
    unknown = sorted(set(requested) - set(FACTOR_DIRECTIONS))
    if unknown:
        raise ValueError(f"unsupported operating-efficiency improvement factors: {unknown}")
    verified = set(str(item) for item in audit.get("verified_factor_ids", []))
    not_verified = sorted(set(requested) - verified)
    if not_verified:
        raise ValueError(f"operating-efficiency improvement factors not P7-verified: {not_verified}")

    panel, _ = p5.load_candidate_panel(Path(data_dir), list(symbols), [])
    snapshot = load_fundamental_snapshot(Path(data_dir))
    if snapshot is None or snapshot.is_empty():
        raise FileNotFoundError("financial snapshot is empty or unavailable")
    panel = attach_fundamental_factors(panel, snapshot, requested)
    missing = sorted(set(requested) - set(panel.columns))
    if missing:
        raise ValueError(f"operating-efficiency improvement factors were not materialized: {missing}")
    return panel


def _variant_spec(variant: str) -> dict[str, Any]:
    return {
        "raw": {"industry": None, "styles": [], "exclude": [], "shrink": 1.0},
        "industry": {
            "industry": "industry_l1",
            "styles": [],
            "exclude": [],
            "shrink": 1.0,
        },
        "size": {
            "industry": None,
            "styles": ["log_float_mv"],
            "exclude": [],
            "shrink": 1.0,
        },
        "industry_size": {
            "industry": "industry_l1",
            "styles": ["log_float_mv"],
            "exclude": [],
            "shrink": 1.0,
        },
    }[variant]


def filter_non_financial_universe(panel: pl.DataFrame) -> pl.DataFrame:
    """Exclude banks and non-bank financials, whose statement semantics differ."""
    if "industry_l1" not in panel.columns:
        raise ValueError("operating-efficiency universe needs industry_l1")
    return panel.filter(~pl.col("industry_l1").is_in(EXCLUDED_INDUSTRIES))


def factor_coverage(
    panel: pl.DataFrame,
    factor_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for factor_id in factor_ids:
        if factor_id not in panel.columns:
            raise ValueError(f"factor coverage column is missing: {factor_id}")
        non_null = panel.filter(pl.col(factor_id).is_not_null())
        first_date = non_null["date"].min() if non_null.height else None
        coverage[factor_id] = {
            "rows": panel.height,
            "non_null_rows": non_null.height,
            "non_null_ratio": (
                round(non_null.height / panel.height, 6) if panel.height else 0.0
            ),
            "symbol_count": int(non_null["symbol"].n_unique()),
            "first_value_date": str(first_date) if first_date is not None else None,
        }
    return coverage


def build_horizon_panel(
    panel: pl.DataFrame,
    horizons: Sequence[int] = FORWARD_HORIZONS,
) -> pl.DataFrame:
    missing = {"symbol", "date", "close"} - set(panel.columns)
    if missing:
        raise ValueError(f"horizon panel missing columns: {sorted(missing)}")
    expressions = []
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("forward horizons must be positive")
        expressions.append(
            (
                pl.col("close").shift(-int(horizon)).over("symbol")
                / pl.col("close")
                - 1.0
            ).alias(f"forward_return_{int(horizon)}d")
        )
    return panel.sort(["symbol", "date"]).with_columns(expressions)


def neutralized_ic_grid(
    panel: pl.DataFrame,
    *,
    horizons: Sequence[int] = FORWARD_HORIZONS,
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """Direction-adjusted rank IC for raw and three neutralized variants."""
    required = {"date", "symbol", "industry_l1", "log_float_mv"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"IC grid panel missing columns: {missing}")
    grid: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for factor_id, direction in FACTOR_DIRECTIONS.items():
        if factor_id not in panel.columns:
            raise ValueError(f"IC grid factor is missing: {factor_id}")
        sign = -1.0 if direction == "low" else 1.0
        signed_column = f"_r384_signed_{factor_id}"
        grid[factor_id] = {}
        for horizon in horizons:
            return_column = f"forward_return_{int(horizon)}d"
            if return_column not in panel.columns:
                raise ValueError(f"IC grid return is missing: {return_column}")
            grid[factor_id][str(int(horizon))] = {}
            for variant in ("raw", *NEUTRAL_VARIANTS):
                work = panel.select(
                    [
                        "date",
                        "symbol",
                        "industry_l1",
                        "log_float_mv",
                        factor_id,
                        return_column,
                    ]
                ).with_columns((pl.col(factor_id) * sign).alias(signed_column))
                neutral = neutralize_scoring_columns(
                    work,
                    [signed_column],
                    _variant_spec(variant),
                )
                valid = neutral.filter(
                    pl.col(signed_column).is_not_null()
                    & pl.col(signed_column).is_finite()
                    & pl.col(return_column).is_not_null()
                    & pl.col(return_column).is_finite()
                )
                ic_rows = calculate_rank_ic(
                    neutral,
                    signed_column,
                    return_column,
                    minimum=30,
                )
                values = (
                    ic_rows["ic"].drop_nulls().to_numpy() if ic_rows.height else []
                )
                nw = (
                    stats_v2.newey_west_t(values, lag=int(horizon))
                    if len(values) >= 3
                    else None
                )
                grid[factor_id][str(int(horizon))][variant] = {
                    "directional_ic": (
                        round(float(values.mean()), 6) if len(values) else None
                    ),
                    "nw_t": round(float(nw[0]), 6) if nw is not None else None,
                    "p_value": (
                        round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
                        if nw is not None
                        else None
                    ),
                    "n_dates": len(values),
                    "valid_rows": valid.height,
                }
    return grid


def factor_horizon_gate(
    variants: Mapping[str, Mapping[str, Any]],
    *,
    direction: str,
) -> dict[str, Any]:
    """Require two neutralized ICs after direction adjustment with p<=0.10."""
    if direction not in {"high", "low"}:
        raise ValueError(f"unsupported factor direction: {direction}")
    confirmations = []
    for variant in NEUTRAL_VARIANTS:
        row = variants.get(variant, {})
        ic = row.get("directional_ic")
        p_value = row.get("p_value")
        if (
            isinstance(ic, (int, float))
            and isinstance(p_value, (int, float))
            and float(ic) > 0.0
            and float(p_value) <= 0.10
        ):
            confirmations.append(variant)
    return {
        "pass": len(confirmations) >= 2,
        "confirmations": confirmations,
        "required_confirmations": 2,
        "reasons": (
            []
            if len(confirmations) >= 2
            else ["fewer_than_two_neutralized_confirmations"]
        ),
    }


def candidate_status(
    horizon_gate: Mapping[str, Any],
    incremental_gate: Mapping[str, Any],
) -> str:
    if horizon_gate.get("pass") is not True:
        return "horizon_rejected"
    if incremental_gate.get("pass") is not True:
        return "incremental_rejected"
    return "incremental_pass"


def _catalog_snapshot_id(data_dir: Path) -> str | None:
    path = data_dir / "factor_research" / "catalog" / "latest.json"
    if not path.exists():
        return None
    value = _read_json(path).get("snapshot_id")
    return str(value) if value else None


def _input_manifest(
    data_dir: Path,
    audit: Mapping[str, Any],
    p5_payload: Mapping[str, Any],
    current_version: Any,
    output_path: Path,
) -> list[dict[str, Any]]:
    """Freeze R384 inputs without following the mutable P7 latest pointer."""
    if Path(output_path).exists():
        stored = _read_json(Path(output_path)).get("input_manifest")
        if isinstance(stored, list):
            # The published manifest records the P7 pointer bytes observed when
            # R384 ran. Reuse that immutable provenance on idempotent reruns.
            return stored

    manifest = p5._input_manifest()
    run_stem = str(audit["research_id"]).removeprefix("sha256:")
    run_dir = Path(data_dir) / "factor_research" / "pit" / "runs"
    financial_tables = [
        Path(data_dir) / "financials" / table / "part.parquet"
        for table in ("metrics", "income", "balance_sheet", "cash_flow")
    ]
    catalog_id = str(p5_payload.get("catalog_snapshot_id") or "")
    catalog_path = (
        Path(data_dir)
        / "factor_research"
        / "catalog"
        / "snapshots"
        / f"{catalog_id.removeprefix('sha256:')}.json"
    )
    current_version_path = (
        Path(data_dir)
        / "factor_research"
        / "sets"
        / "multi_dimension_v1"
        / "versions"
        / f"v{int(current_version.version):04d}.json"
    )
    manifest.extend(
        [
            p8._manifest_group(data_dir, "financial_tables", financial_tables),
            p8._manifest_group(
                data_dir,
                "financial_pit_p7",
                [run_dir / f"{run_stem}.json", run_dir / f"{run_stem}.md"],
            ),
            p8._manifest_group(
                data_dir,
                "p5_increment_baseline",
                [p8.P5_RESULT, p8.P5_RESULT.with_suffix(".md"), current_version_path],
            ),
        ]
    )
    if catalog_path.exists():
        manifest.append(
            p8._manifest_group(data_dir, "catalog_snapshot", [catalog_path])
        )
    return manifest


def _portfolio_stage(
    data_dir: Path,
    panel: pl.DataFrame,
    symbols: Sequence[str],
    current_version: Any,
    current_weights: Mapping[str, float],
    current_directions: Mapping[str, str],
    current_ids: Sequence[str],
    passing: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Run real T+1 portfolio, ablation and rolling gates for incremental passes."""
    if not passing:
        return {
            "run": False,
            "reason": "no_incremental_candidate",
            "baseline": None,
            "screen": {},
            "passing_candidates": [],
            "final": None,
            "ablation": None,
            "ablation_gate": None,
            "rolling_year": None,
            "cost_warning_5x": None,
        }
    all_factor_ids = list(
        dict.fromkeys([*current_ids, *[str(item["factor_id"]) for item in passing]])
    )
    service = p8._service()
    prepared_full = p5._prepare_shared_window(
        service,
        list(symbols),
        panel,
        all_factor_ids,
        start=p5.START,
        end=p5.END,
    )
    prepared_recent = p5._prepare_shared_window(
        service,
        list(symbols),
        panel,
        all_factor_ids,
        start=p5.RECENT_START,
        end=p5.END,
    )
    prepared_weak = p5._prepare_shared_window(
        service,
        list(symbols),
        panel,
        all_factor_ids,
        start=p5.WEAK_START,
        end=p5.WEAK_END,
    )
    baseline = p8._baseline(
        service,
        prepared_full,
        prepared_recent,
        prepared_weak,
        dict(current_weights),
        dict(current_directions),
    )
    loo = p5._loo_contributions(
        service,
        list(symbols),
        panel,
        dict(current_weights),
        dict(current_directions),
        list(current_ids),
        prepared_full=prepared_full,
        prepared_recent=prepared_recent,
        baseline_full=baseline["full_2x"],
        baseline_recent=baseline["recent_2x"],
    )
    weakest_factor = min(
        loo,
        key=lambda factor_id: (
            loo[factor_id]["full_marginal_contribution"],
            loo[factor_id]["recent_marginal_contribution"],
            factor_id,
        ),
    )
    screen = p5._run_candidate_screen(
        service,
        list(passing),
        {item["candidate_key"]: item["marginal_ic"] for item in passing},
        list(current_ids),
        dict(current_directions),
        weakest_factor,
        prepared_full,
        prepared_recent,
        prepared_weak,
        baseline["full_2x"],
        baseline["recent_2x"],
        baseline["full_3x"],
        baseline["weak_2x"],
    )
    passing_screen = [
        item
        for item in passing
        if screen.get(item["candidate_key"], {}).get("status") == "portfolio_pass"
    ]
    result = {
        "run": True,
        "reason": None,
        "baseline": baseline,
        "leave_one_out": loo,
        "weakest_factor": weakest_factor,
        "screen": screen,
        "passing_candidates": [
            str(item["candidate_key"]) for item in passing_screen
        ],
        "final": None,
        "ablation": None,
        "ablation_gate": None,
        "rolling_year": None,
        "cost_warning_5x": None,
    }
    if not passing_screen:
        return result

    selected = max(
        passing_screen,
        key=lambda item: float(
            screen[item["candidate_key"]]["portfolio"]["full_2x"]["stats"][
                "total_return"
            ]
            or -999.0
        ),
    )
    selected_screen = screen[selected["candidate_key"]]
    final_portfolio = selected_screen["portfolio"]
    final_ids = list(final_portfolio["factor_ids"])
    final_directions = dict(final_portfolio["directions"])
    final_scoring, selected_directions = p5._equal_inputs(
        final_ids, final_directions
    )
    result["final"] = {
        "candidate_key": selected["candidate_key"],
        "portfolio": final_portfolio,
        "gate": selected_screen["portfolio_gate"],
    }
    if selected_screen["portfolio_gate"].get("pass") is not True:
        return result

    windows = {
        "full": (p5.START, p5.END),
        "recent": (p5.RECENT_START, p5.END),
        "weak": (p5.WEAK_START, p5.WEAK_END),
    }
    ablation = p5._run_ablation(
        service,
        final_ids,
        final_directions,
        {
            "full": prepared_full,
            "recent": prepared_recent,
            "weak": prepared_weak,
        },
        windows,
    )
    new_factor_ids = [str(selected["factor_id"])]
    ablation_gate = p5._ablation_candidate_gate(ablation, new_factor_ids)
    result["ablation"] = ablation
    result["ablation_gate"] = ablation_gate
    if not ablation_gate["pass"]:
        return result

    rolling = p5._rolling_comparison(
        service,
        list(symbols),
        panel,
        all_factor_ids,
        dict(current_weights),
        dict(current_directions),
        final_scoring,
        selected_directions,
    )
    result["rolling_year"] = rolling
    if not rolling["gate"]["pass"]:
        return result

    result["cost_warning_5x"] = p8._five_x_warning(
        service, prepared_full, final_ids, final_directions
    )
    return result


def _decision_status(
    candidates: Sequence[Mapping[str, Any]],
    portfolio: Mapping[str, Any],
) -> str:
    if not any(item["horizon_gate"].get("pass") for item in candidates):
        return "no_horizon_candidate"
    if not any(item["incremental_gate"].get("pass") for item in candidates):
        return "no_incremental_candidate"
    if not portfolio.get("passing_candidates"):
        return "no_portfolio_candidate"
    if portfolio.get("final") is None:
        return "no_portfolio_candidate"
    if not (portfolio.get("ablation_gate") or {}).get("pass"):
        return "no_ablation_candidate"
    if not (portfolio.get("rolling_year") or {}).get("gate", {}).get("pass"):
        return "no_rolling_candidate"
    return "watch_candidate_passed_no_promotion"


def _load_frozen_result(path: Path) -> dict[str, Any]:
    """Authenticate and reuse an already published R384 result."""
    payload = _read_json(path)
    if payload.get("schema_version") != 1:
        raise ValueError(f"R384 result schema mismatch: {path}")
    if payload.get("method_version") != METHOD_VERSION:
        raise ValueError(f"R384 result method version mismatch: {path}")
    core = _audit_core(payload)
    if sha256_json(core) != payload.get("research_id"):
        raise ValueError(f"R384 result canonical identity mismatch: {path}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or {
        str(item.get("factor_id"))
        for item in candidates
        if isinstance(item, Mapping)
    } != set(FACTOR_DIRECTIONS):
        raise ValueError(f"R384 result candidate set mismatch: {path}")
    markdown_path = path.with_suffix(".md")
    if not markdown_path.is_file():
        raise FileNotFoundError(markdown_path)
    return payload


def _publish_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = _read_json(path)
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"R384 result already exists: {path}")
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    p8.atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"R384 markdown already exists: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    p8.atomic_write_text(path, text)


def run_research(
    data_dir: Path = DATA,
    *,
    output_path: Path = DEFAULT_OUTPUT,
    stats_only: bool = False,
) -> dict[str, Any]:
    data_dir = Path(data_dir).resolve()
    output_path = Path(output_path).resolve()
    if output_path.exists() and not stats_only:
        return _load_frozen_result(output_path)
    audit = load_p7_audit(data_dir)
    candidates = [dict(item) for item in CANDIDATES]
    admission = p8.admit_financial_candidates(
        data_dir,
        candidates,
        admission_research_id=P6_ADMISSION_RESEARCH_ID,
    )
    _sets, _head, current_version = p8._current_set(data_dir)
    current_weights, current_directions, current_ids = p5._current_inputs(
        current_version
    )
    from app.hs300.current import current_symbols

    symbols = current_symbols()
    factor_ids = [str(item["factor_id"]) for item in candidates]
    full_panel = load_research_panel(data_dir, symbols, factor_ids, audit)
    panel = filter_non_financial_universe(full_panel)
    panel = build_horizon_panel(panel)
    coverage = factor_coverage(panel, factor_ids)
    grid = neutralized_ic_grid(panel)

    scored, marginal = p5.score_candidates(
        panel,
        candidates,
        current_weights,
        current_directions,
        return_column=f"forward_return_{PRIMARY_HORIZON}d",
        horizon=PRIMARY_HORIZON,
    )
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        factor_id = str(candidate["factor_id"])
        horizon_gate = factor_horizon_gate(
            grid[factor_id][str(PRIMARY_HORIZON)],
            direction=str(candidate["direction"]),
        )
        incremental_result = dict(marginal.get(candidate["candidate_key"], {}))
        incremental_gate = p5.evaluate_statistical_gate(
            candidate, incremental_result
        )
        records.append(
            {
                **candidate,
                "coverage": coverage[factor_id],
                "primary_horizon": PRIMARY_HORIZON,
                "horizon_gate": horizon_gate,
                "marginal_ic": incremental_result,
                "incremental_gate": incremental_gate,
                "status": candidate_status(horizon_gate, incremental_gate),
            }
        )
    passing = [item for item in records if item["status"] == "incremental_pass"]

    if stats_only:
        preview = {
            "financial_pit_research_id": audit["research_id"],
            "base_set_content_sha256": current_version.content_sha256,
            "current_ids": current_ids,
            "panel_rows": scored.height,
            "symbol_count": int(panel["symbol"].n_unique()),
            "coverage": coverage,
            "primary_horizon": PRIMARY_HORIZON,
            "horizon_grid": grid,
            "candidates": records,
            "statistical_pass_count": sum(
                item["horizon_gate"]["pass"] for item in records
            ),
            "incremental_pass_count": len(passing),
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return preview

    p5_payload = p8._read_json(p8.P5_RESULT)
    portfolio = _portfolio_stage(
        data_dir,
        scored,
        symbols,
        current_version,
        current_weights,
        current_directions,
        current_ids,
        passing,
    )
    for record in records:
        record["portfolio_result"] = portfolio.get("screen", {}).get(
            record["candidate_key"]
        )
    statistical_pass_count = sum(
        item["horizon_gate"]["pass"] for item in records
    )
    incremental_pass_count = len(passing)
    portfolio_pass_count = len(portfolio.get("passing_candidates") or [])
    decision_status = _decision_status(records, portfolio)
    input_manifest = _input_manifest(
        data_dir, audit, p5_payload, current_version, output_path
    )
    input_manifest_sha = sha256_json(input_manifest)
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha,
            "financial_pit_research_id": audit["research_id"],
            "p6_admission_catalog_snapshot_id": admission.get(
                "catalog_snapshot_id"
            ),
            "panel_rows": scored.height,
            "symbol_count": int(panel["symbol"].n_unique()),
            "coverage": coverage,
            "ic_grid": grid,
            "candidate_marginal": {
                item["candidate_key"]: item["marginal_ic"] for item in records
            },
        }
    )
    negative_knowledge = []
    portfolio_passing = set(portfolio.get("passing_candidates") or [])
    selected_final = (portfolio.get("final") or {}).get("candidate_key")
    for record in records:
        candidate_key = str(record["candidate_key"])
        reasons = [
            *record["horizon_gate"].get("reasons", []),
            *record["incremental_gate"].get("reasons", []),
        ]
        if record["status"] != "incremental_pass":
            reasons.append(str(record["status"]))
        else:
            screen = portfolio.get("screen", {}).get(candidate_key)
            if candidate_key not in portfolio_passing or screen is None:
                reasons.append("portfolio_rejected")
                gate = (screen or {}).get("portfolio_gate") or {}
                checks = gate.get("checks") if isinstance(gate, Mapping) else None
                if isinstance(checks, Mapping):
                    reasons.extend(
                        f"portfolio_check_failed:{check}"
                        for check, passed in checks.items()
                        if passed is not True
                    )
                if screen is None:
                    reasons.append("portfolio_screen_missing")
            elif candidate_key != selected_final:
                reasons.append("not_selected_as_final")
            elif not (portfolio.get("ablation_gate") or {}).get("pass"):
                reasons.append("ablation_rejected")
                reasons.extend(
                    (portfolio.get("ablation_gate") or {}).get("reasons", [])
                )
            elif not (portfolio.get("rolling_year") or {}).get("gate", {}).get("pass"):
                reasons.append("rolling_year_rejected")
                reasons.extend(
                    (portfolio.get("rolling_year") or {})
                    .get("gate", {})
                    .get("reasons", [])
                )
            else:
                continue
        negative_knowledge.append(
            {
                "candidate_key": candidate_key,
                "factor_id": record["factor_id"],
                "status": record["status"],
                "portfolio_status": (
                    portfolio.get("screen", {})
                    .get(candidate_key, {})
                    .get("status")
                ),
                "reasons": sorted(set(str(reason) for reason in reasons)),
            }
        )

    incremental_pass_factor_ids = [
        str(item["factor_id"]) for item in passing
    ]
    final_factor_ids = (
        incremental_pass_factor_ids
        if decision_status == "watch_candidate_passed_no_promotion"
        else []
    )
    core = {
        "schema_version": 1,
        "method_version": METHOD_VERSION,
        "financial_pit_research_id": audit["research_id"],
        "verified_factor_ids": list(audit.get("verified_factor_ids", [])),
        "p5_research_id": p5_payload.get("research_id"),
        "catalog_snapshot_id": _catalog_snapshot_id(data_dir),
        "p6_admission_research_id": P6_ADMISSION_RESEARCH_ID,
        "p6_admission_catalog_snapshot_id": admission.get("catalog_snapshot_id"),
        "input_manifest_sha256": input_manifest_sha,
        "input_manifest": input_manifest,
        "data_fingerprint": data_fingerprint,
        "period": {
            "start": str(p5.START),
            "recent_start": str(p5.RECENT_START),
            "end": str(p5.END),
            "weak_start": str(p5.WEAK_START),
            "weak_end": str(p5.WEAK_END),
        },
        "universe": {
            "name": "hs300_current_non_financial",
            "symbol_count": int(panel["symbol"].n_unique()),
            "excluded_industries": list(EXCLUDED_INDUSTRIES),
            "survivorship_bias": True,
        },
        "base_set": {
            "set_id": current_version.set_id,
            "version": current_version.version,
            "content_sha256": current_version.content_sha256,
            "factor_ids": current_ids,
        },
        "research_design": {
            "horizons": list(FORWARD_HORIZONS),
            "primary_horizon": PRIMARY_HORIZON,
            "newey_west_lag": PRIMARY_HORIZON,
            "minimum_cross_section": 30,
            "neutral_variants_required": 2,
            "incremental_weight": 0.2,
            "fdr_threshold": 0.10,
            "max_correlation": 0.90,
        },
        "candidate_count": len(records),
        "statistical_pass_count": statistical_pass_count,
        "incremental_pass_count": incremental_pass_count,
        "portfolio_pass_count": portfolio_pass_count,
        "ic_grid": grid,
        "candidates": records,
        "portfolio": portfolio,
        "negative_knowledge": negative_knowledge,
        "decision": {
            "status": decision_status,
            "factor_ids": final_factor_ids,
            "incremental_pass_factor_ids": incremental_pass_factor_ids,
            "active_version": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "caveats": [
            "Current HS300 membership is applied to history; survivorship bias remains.",
            "Industry classification is the current snapshot, not historical PIT.",
            "A standalone IC is not enough; the 80% base + 20% candidate incremental gate is mandatory.",
            "No leverage, paper target, order, or production activation is produced.",
            "The four operating-efficiency improvement factors are not silently added to the strategy when incremental evidence fails.",
        ],
    }
    research_id = sha256_json(core)
    payload = {**core, "research_id": research_id, "generated_at": utc_now()}
    output_path = output_path.resolve()
    published = _publish_json(output_path, payload)
    markdown_path = output_path.with_suffix(".md")
    _publish_markdown(markdown_path, render_markdown(published))
    ledger_record, _created = bind_research_evidence(
        data_dir,
        operation=OPERATION,
        evidence_paths=[output_path, markdown_path],
        research_id=published["research_id"],
        payload={
            "catalog_snapshot_id": published.get("catalog_snapshot_id"),
            "input_fingerprints": {
                "financial_pit_research_id": published[
                    "financial_pit_research_id"
                ],
                "p5_research_id": published["p5_research_id"],
                "input_manifest_sha256": published["input_manifest_sha256"],
                "data_fingerprint": published["data_fingerprint"],
            },
            "metrics": {
                "candidate_count": published["candidate_count"],
                "statistical_pass_count": published["statistical_pass_count"],
                "incremental_pass_count": published["incremental_pass_count"],
                "portfolio_pass_count": published["portfolio_pass_count"],
                "final_factor_count": len(
                    published["decision"]["factor_ids"] or []
                ),
            },
            "verdict": published["decision"]["status"],
            "reason": "watch_only_no_active_version",
            "negative_knowledge": json.dumps(
                published["negative_knowledge"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )
    print(
        json.dumps(
            {
                "research_id": published["research_id"],
                "output": str(output_path.relative_to(ROOT)),
                "candidate_count": published["candidate_count"],
                "statistical_pass_count": published["statistical_pass_count"],
                "incremental_pass_count": published["incremental_pass_count"],
                "portfolio_pass_count": published["portfolio_pass_count"],
                "decision": published["decision"]["status"],
                "active_version": None,
                "ledger_record_id": ledger_record.record_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return published


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# R384 财务营运效率改善因子研究报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- P7 财务 PIT：`{payload['financial_pit_research_id']}`",
        f"- P6 准入：`{payload['p6_admission_research_id']}`",
        f"- 样本：{payload['universe']['symbol_count']} 只非金融 HS300",
        f"- 主期限：{payload['research_design']['primary_horizon']} 交易日",
        f"- 决策：`{payload['decision']['status']}`",
        "- `active_version` 保持 null；不生成纸面目标或订单。",
        "",
        "## 单因子 IC",
        "",
        "| 因子 | 期限 | raw | industry | size | industry+size |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for factor_id in FACTOR_DIRECTIONS:
        for horizon in FORWARD_HORIZONS:
            row = payload["ic_grid"][factor_id][str(horizon)]
            lines.append(
                f"| `{factor_id}` | {horizon} | {_fmt(row['raw']['directional_ic'])} | "
                f"{_fmt(row['industry']['directional_ic'])} | "
                f"{_fmt(row['size']['directional_ic'])} | "
                f"{_fmt(row['industry_size']['directional_ic'])} |"
            )
    lines.extend(
        [
            "",
            "## 增量门槛",
            "",
            "| 因子 | 中性化确认 | 60日边际IC | NW p | BH q | 最大相关 | 结论 |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for candidate in payload["candidates"]:
        marginal = candidate["marginal_ic"]
        lines.append(
            f"| `{candidate['factor_id']}` | "
            f"{', '.join(candidate['horizon_gate']['confirmations']) or '-'} | "
            f"{_fmt(marginal.get('directional_delta_mean'))} | "
            f"{_fmt(marginal.get('delta_p_value'))} | "
            f"{_fmt(marginal.get('delta_q_value'))} | "
            f"{_fmt(marginal.get('max_abs_correlation_for_gate'))} | "
            f"{candidate['status']} |"
        )
    lines.extend(
        [
            "",
            "## 组合门槛",
            "",
            "| 因子 | 组合状态 | 全样本2x年化 | 全样本3x年化 | 近一年2x收益 | 弱市2x收益 | 未通过检查 |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for candidate in payload["candidates"]:
        screen = candidate.get("portfolio_result") or {}
        portfolio = screen.get("portfolio") or {}
        gate = screen.get("portfolio_gate") or {}
        checks = gate.get("checks") if isinstance(gate, Mapping) else None
        failed_checks = (
            [
                str(check)
                for check, passed in checks.items()
                if passed is not True
            ]
            if isinstance(checks, Mapping)
            else []
        )

        window_stats = {
            window: (portfolio.get(window) or {}).get("stats", {})
            for window in ("full_2x", "full_3x", "recent_2x", "weak_2x")
        }
        lines.append(
            f"| `{candidate['factor_id']}` | {screen.get('status', '-')} | "
            f"{_fmt(window_stats['full_2x'].get('annual_return'))} | "
            f"{_fmt(window_stats['full_3x'].get('annual_return'))} | "
            f"{_fmt(window_stats['recent_2x'].get('total_return'))} | "
            f"{_fmt(window_stats['weak_2x'].get('total_return'))} | "
            f"{', '.join(failed_checks) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 单因子期限门槛通过：{payload['statistical_pass_count']}。",
            f"- 增量门槛通过：{payload['incremental_pass_count']}。",
            f"- 组合门槛通过：{payload['portfolio_pass_count']}。",
            f"- 增量通过候选：{payload['decision']['incremental_pass_factor_ids'] or '无'}。",
            f"- 最终因子：{payload['decision']['factor_ids'] or '无'}。",
            "- 未通过任一门槛的候选只保留为负知识，不进入策略或生产版本。",
            "",
            "## 负知识",
            "",
        ]
    )
    if payload["negative_knowledge"]:
        for item in payload["negative_knowledge"]:
            portfolio_status = item.get("portfolio_status")
            suffix = f"；组合状态 `{portfolio_status}`" if portfolio_status else ""
            reasons = ", ".join(item["reasons"]) or "无"
            lines.append(f"- `{item['candidate_key']}`：{reasons}{suffix}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 当前 HS300 名单回填历史存在幸存者偏差。",
            "- 行业分类使用当前快照，不是历史 PIT。",
            "- 银行和非银金融被排除，避免营运资本口径错配。",
            "- 2x 成本为正常硬门槛，3x 为压力门槛，5x 只记录 warning。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_research(DATA, output_path=args.output, stats_only=args.stats_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
