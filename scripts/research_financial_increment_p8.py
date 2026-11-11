#!/usr/bin/env python3
"""P8 incremental research for P7-verified financial factors."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
from app.factor_research.automation import (
    assert_portfolio_candidates_admitted,
    bind_research_evidence,
)
from app.factor_research.financial_pit import (
    BUILTIN_FINANCIAL_DIRECTIONS,
    BUILTIN_FINANCIAL_FACTOR_IDS,
)
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.sets import FactorSetStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = (
    DATA / "factor_research" / "research" / "p8_financial_increment_2026-11-12.json"
)
P5_RESULT = DATA / "factor_research" / "research" / "p5_incremental_2026-11-12.json"
P7_LATEST = DATA / "factor_research" / "pit" / "latest.json"
METHOD_VERSION = "financial_increment_p8_v1"
OPERATION = "financial_increment_scan_published"
MAX_FACTORS = 8
COST_WARNING_5X = {
    "fees_pct": 0.001,
    "commission_pct": 0.001,
    "stamp_tax_pct": 0.0005,
    "slippage_bps": 25.0,
}


def _load_sibling(name: str):
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p8_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sibling research module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p5 = _load_sibling("research_factor_increment_p5")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _resolve_data_path(data_dir: Path, raw_path: str) -> Path:
    path = (Path(data_dir) / raw_path).resolve()
    root = Path(data_dir).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"research path escapes data dir: {raw_path}")
    return path


def _audit_core(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }


def load_verified_audit(data_dir: Path) -> dict[str, Any]:
    """Load P7's exact verified run and reject any missing or stale evidence."""
    latest = _read_json(Path(data_dir) / "factor_research" / "pit" / "latest.json")
    run_json = latest.get("run_json")
    research_id = latest.get("research_id")
    if not isinstance(run_json, str) or not isinstance(research_id, str):
        raise ValueError("P7 latest pointer is malformed")
    run_path = _resolve_data_path(Path(data_dir), run_json)
    if not run_path.exists():
        raise FileNotFoundError(run_path)
    payload = _read_json(run_path)
    if payload.get("research_id") != research_id:
        raise ValueError("P7 latest research ID does not match run JSON")
    if sha256_json(_audit_core(payload)) != research_id:
        raise ValueError("P7 run JSON canonical identity mismatch")
    if payload.get("status") != "verified":
        raise ValueError("P7 financial audit is not fully verified")
    verified = set(payload.get("verified_factor_ids", []))
    missing = sorted(set(BUILTIN_FINANCIAL_FACTOR_IDS) - verified)
    if missing:
        raise ValueError(f"financial factors not P7-verified: {missing}")
    factors = payload.get("factors", {})
    not_verified = sorted(
        factor_id
        for factor_id in BUILTIN_FINANCIAL_FACTOR_IDS
        if not isinstance(factors, Mapping)
        or not isinstance(factors.get(factor_id), Mapping)
        or factors[factor_id].get("verified") is not True
    )
    if not_verified:
        raise ValueError(f"financial factors not P7-verified: {not_verified}")
    return payload


def financial_candidates(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the fixed P8 candidate list from P7's verified factor ids."""
    verified = set(str(item) for item in audit.get("verified_factor_ids", []))
    factors = audit.get("factors", {})
    missing = [
        factor_id
        for factor_id in BUILTIN_FINANCIAL_FACTOR_IDS
        if factor_id not in verified
        or not isinstance(factors, Mapping)
        or not isinstance(factors.get(factor_id), Mapping)
        or factors[factor_id].get("verified") is not True
    ]
    if missing:
        raise ValueError(f"financial factors not P7-verified: {missing}")
    return [
        {
            "candidate_key": f"p8_{factor_id}",
            "economic_key": factor_id,
            "label": factor_id,
            "source_research": "p7",
            "factor_id": factor_id,
            "direction": BUILTIN_FINANCIAL_DIRECTIONS[factor_id],
            "registered": True,
            "transform": "pit_latest",
            "set_role": "new",
            "replaces_factor_id": None,
            "dedupe_priority": 0,
        }
        for factor_id in BUILTIN_FINANCIAL_FACTOR_IDS
    ]


def load_financial_panel(
    data_dir: Path,
    symbols: Sequence[str],
    factor_ids: Sequence[str],
) -> tuple[pl.DataFrame, dict[str, dict[str, Any]]]:
    """Build the same daily panel as P5 and attach PIT-gated financial fields."""
    data_dir = Path(data_dir)
    requested = list(dict.fromkeys(str(item) for item in factor_ids))
    unknown = sorted(set(requested) - set(BUILTIN_FINANCIAL_FACTOR_IDS))
    if unknown:
        raise ValueError(f"unsupported financial factors: {unknown}")
    audit = load_verified_audit(data_dir)
    verified = set(str(item) for item in audit.get("verified_factor_ids", []))
    not_verified = sorted(set(requested) - verified)
    if not_verified:
        raise ValueError(f"financial factors not P7-verified: {not_verified}")

    panel, _ = p5.load_candidate_panel(data_dir, list(symbols), [])
    snapshot = load_fundamental_snapshot(data_dir)
    if snapshot is None or snapshot.is_empty():
        raise FileNotFoundError("financial snapshot is empty or unavailable")
    panel = attach_fundamental_factors(panel, snapshot, requested)
    missing = sorted(set(requested) - set(panel.columns))
    if missing:
        raise ValueError(f"financial factors were not materialized: {missing}")

    coverage: dict[str, dict[str, Any]] = {}
    for factor_id in requested:
        non_null = panel.filter(pl.col(factor_id).is_not_null())
        if non_null.is_empty():
            raise ValueError(f"financial factor has no non-null rows: {factor_id}")
        first_date = non_null["date"].min()
        coverage[factor_id] = {
            "rows": panel.height,
            "non_null_rows": non_null.height,
            "non_null_ratio": round(non_null.height / panel.height, 6),
            "symbol_count": int(non_null["symbol"].n_unique()),
            "first_value_date": str(first_date) if first_date is not None else None,
        }
    return panel, coverage


def admit_financial_candidates(
    data_dir: Path,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fail closed on P6 portfolio admission before any candidate backtest."""
    factor_ids = sorted({str(candidate["factor_id"]) for candidate in candidates})
    return assert_portfolio_candidates_admitted(Path(data_dir), factor_ids)


def gate_financial_candidates(
    candidates: Sequence[dict[str, Any]],
    marginal: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the existing P5 statistical gate to every financial candidate."""
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        result = dict(marginal.get(candidate["candidate_key"], {}))
        gate = p5.evaluate_statistical_gate(candidate, result)
        records.append(
            {
                **candidate,
                "marginal_ic": result,
                "statistical_gate": gate,
                "status": "statistical_pass" if gate["pass"] else "statistical_rejected",
            }
        )
    return records


def portfolio_candidates(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [record for record in records if record.get("status") == "statistical_pass"]


def select_final_set(
    passing: Sequence[Mapping[str, Any]],
    current_ids: Sequence[str],
    current_directions: Mapping[str, str],
    *,
    weakest_factors: Sequence[str],
    max_factors: int = MAX_FACTORS,
) -> tuple[list[str], dict[str, str]]:
    """Choose an equal-weight set without exceeding the configured member cap."""
    ranked = sorted(
        passing,
        key=lambda item: (
            float(item.get("marginal_ic", {}).get("directional_delta_mean") or -999.0),
            str(item.get("factor_id") or ""),
        ),
        reverse=True,
    )
    ordered_weakest = [factor_id for factor_id in weakest_factors if factor_id in current_ids]
    capacity = max(0, max_factors - len(current_ids))
    replaceable = min(len(ranked), len(ordered_weakest), max(0, len(current_ids) - 1))
    if capacity:
        added = ranked[:capacity]
        kept = list(current_ids)
    else:
        dropped = set(ordered_weakest[:replaceable])
        added = ranked[:replaceable]
        kept = [factor_id for factor_id in current_ids if factor_id not in dropped]
    selected_ids = [*kept, *(str(item["factor_id"]) for item in added)]
    selected_ids = list(dict.fromkeys(selected_ids))
    if len(selected_ids) > max_factors:
        raise ValueError("P8 factor set exceeds the eight-member cap")
    directions = {
        factor_id: current_directions[factor_id]
        for factor_id in selected_ids
        if factor_id in current_directions
    }
    for item in added:
        directions[str(item["factor_id"])] = str(item["direction"])
    return selected_ids, directions


def _current_set(data_dir: Path):
    """Use P5's fixed v1 baseline so P8 reruns remain reproducible."""
    sets = FactorSetStore(data_dir)
    head = sets.head("multi_dimension_v1")
    if head is None:
        raise RuntimeError("multi_dimension_v1 head is unavailable")
    if head.active_version is not None:
        raise ValueError("P8 requires active_version=null")
    version = sets.load_version("multi_dimension_v1", 1)
    return sets, head, version


def _manifest_group(data_dir: Path, label: str, paths: Sequence[Path]) -> dict[str, Any]:
    root = Path(data_dir).resolve()
    unique = sorted({Path(path).resolve() for path in paths})
    files: list[dict[str, Any]] = []
    for path in unique:
        if path != root and root not in path.parents:
            raise ValueError(f"manifest path escapes data dir: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "label": label,
        "file_count": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": sha256_json(files),
        "files": files,
    }


def _input_manifest(
    data_dir: Path,
    audit: Mapping[str, Any],
    p5_payload: Mapping[str, Any],
    current_version: Any,
) -> list[dict[str, Any]]:
    """Include all data and research inputs needed to reproduce P8."""
    manifest = p5._input_manifest()
    p7_latest = _read_json(Path(data_dir) / "factor_research" / "pit" / "latest.json")
    run_json = _resolve_data_path(Path(data_dir), str(p7_latest["run_json"]))
    run_markdown = Path(data_dir) / str(p7_latest["run_markdown"])
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
            _manifest_group(data_dir, "financial_tables", financial_tables),
            _manifest_group(
                data_dir,
                "financial_pit_p7",
                [
                    Path(data_dir) / "factor_research" / "pit" / "latest.json",
                    run_json,
                    run_markdown,
                ],
            ),
            _manifest_group(
                data_dir,
                "p5_increment_baseline",
                [P5_RESULT, P5_RESULT.with_suffix(".md"), current_version_path],
            ),
        ]
    )
    if catalog_path.exists():
        manifest.append(
            _manifest_group(data_dir, "catalog_snapshot", [catalog_path])
        )
    return manifest


def _service():
    return p5.portfolio._service()


def _baseline(
    service: Any,
    prepared_full: Any,
    prepared_recent: Any,
    prepared_weak: Any,
    scoring: Mapping[str, float],
    directions: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    return {
        "full_2x": p5._run_prepared(
            service,
            prepared_full,
            dict(scoring),
            dict(directions),
            start=p5.START,
            end=p5.END,
            cost_label="2x",
            label="p8_baseline_full_2x",
        ),
        "full_3x": p5._run_prepared(
            service,
            prepared_full,
            dict(scoring),
            dict(directions),
            start=p5.START,
            end=p5.END,
            cost_label="3x",
            label="p8_baseline_full_3x",
        ),
        "recent_2x": p5._run_prepared(
            service,
            prepared_recent,
            dict(scoring),
            dict(directions),
            start=p5.RECENT_START,
            end=p5.END,
            cost_label="2x",
            label="p8_baseline_recent_2x",
        ),
        "weak_2x": p5._run_prepared(
            service,
            prepared_weak,
            dict(scoring),
            dict(directions),
            start=p5.WEAK_START,
            end=p5.WEAK_END,
            cost_label="2x",
            label="p8_baseline_weak_2x",
        ),
    }


def _run_set(
    service: Any,
    prepared_full: Any,
    prepared_recent: Any,
    prepared_weak: Any,
    factor_ids: Sequence[str],
    directions: Mapping[str, str],
    baseline: Mapping[str, Mapping[str, Any]],
    *,
    label_prefix: str,
) -> dict[str, Any]:
    scoring, selected_directions = p5._equal_inputs(
        list(factor_ids), dict(directions)
    )
    full = p5._run_prepared(
        service,
        prepared_full,
        scoring,
        selected_directions,
        start=p5.START,
        end=p5.END,
        cost_label="2x",
        label=f"{label_prefix}_full_2x",
    )
    full_3x = p5._run_prepared(
        service,
        prepared_full,
        scoring,
        selected_directions,
        start=p5.START,
        end=p5.END,
        cost_label="3x",
        label=f"{label_prefix}_full_3x",
    )
    recent = p5._run_prepared(
        service,
        prepared_recent,
        scoring,
        selected_directions,
        start=p5.RECENT_START,
        end=p5.END,
        cost_label="2x",
        label=f"{label_prefix}_recent_2x",
    )
    weak = p5._run_prepared(
        service,
        prepared_weak,
        scoring,
        selected_directions,
        start=p5.WEAK_START,
        end=p5.WEAK_END,
        cost_label="2x",
        label=f"{label_prefix}_weak_2x",
    )
    base_gate = p5._gate_metrics(
        baseline["full_2x"], baseline["recent_2x"], baseline["full_3x"]
    )
    candidate_gate = p5._gate_metrics(full, recent, full_3x)
    return {
        "factor_ids": list(factor_ids),
        "scoring": scoring,
        "directions": selected_directions,
        "full_2x": full,
        "full_3x": full_3x,
        "recent_2x": recent,
        "weak_2x": weak,
        "portfolio_gate": p5.portfolio_gate(base_gate, candidate_gate),
    }


def _five_x_warning(
    service: Any,
    prepared_full: Any,
    factor_ids: Sequence[str],
    directions: Mapping[str, str],
) -> dict[str, Any]:
    scoring, selected_directions = p5._equal_inputs(
        list(factor_ids), dict(directions)
    )
    result = p5._run_prepared(
        service,
        prepared_full,
        scoring,
        selected_directions,
        start=p5.START,
        end=p5.END,
        cost_label="5x_warning",
        cost_override=COST_WARNING_5X,
        label="p8_final_full_5x_warning",
    )
    return {
        "cost": COST_WARNING_5X,
        "result": result,
        "warning_only": True,
        "hard_gate": False,
    }


def _reasons_for_record(record: Mapping[str, Any]) -> list[str]:
    reasons = list(record.get("statistical_gate", {}).get("reasons") or [])
    portfolio_gate = record.get("portfolio_gate")
    if isinstance(portfolio_gate, Mapping) and portfolio_gate.get("pass") is False:
        reasons.extend(
            f"portfolio_gate_failed:{key}"
            for key, passed in portfolio_gate.get("checks", {}).items()
            if not passed
        )
    status = str(record.get("status") or "")
    if status in {"ablation_rejected", "rolling_rejected", "combined_portfolio_rejected"}:
        reasons.append(status)
    return sorted(set(reasons)) or [status or "rejected"]


def _decision_status(
    *,
    statistical: Sequence[Mapping[str, Any]],
    passing_screen: Sequence[Mapping[str, Any]],
    final_ids: Sequence[str] | None,
    set_version: Any,
) -> str:
    if final_ids is not None and set_version is not None:
        return "watch_version_published"
    if not statistical:
        return "no_statistical_candidate"
    if not passing_screen:
        return "no_portfolio_candidate"
    return "no_new_watch_version"


def run_financial_research(
    data_dir: Path = DATA,
    *,
    output_path: Path = DEFAULT_OUTPUT,
    stats_only: bool = False,
) -> dict[str, Any]:
    """Execute the P8 financial incremental research pipeline."""
    data_dir = Path(data_dir)
    audit = load_verified_audit(data_dir)
    candidates = financial_candidates(audit)
    admission = admit_financial_candidates(data_dir, candidates)
    sets, head, current_version = _current_set(data_dir)
    current_weights, current_directions, current_ids = p5._current_inputs(current_version)
    from app.hs300.current import current_symbols

    symbols = current_symbols()
    panel, coverage = load_financial_panel(
        data_dir, symbols, [candidate["factor_id"] for candidate in candidates]
    )
    scored, marginal = p5.score_candidates(
        panel, candidates, current_weights, current_directions
    )
    records = gate_financial_candidates(candidates, marginal)
    statistical = list(portfolio_candidates(records))
    if stats_only:
        preview = {
            "catalog_snapshot_id": _read_json(P5_RESULT).get("catalog_snapshot_id"),
            "p7_research_id": audit["research_id"],
            "base_set_content_sha256": current_version.content_sha256,
            "current_ids": current_ids,
            "panel_rows": scored.height,
            "symbol_count": len(symbols),
            "admission_catalog_snapshot_id": admission.get("catalog_snapshot_id"),
            "statistical_pass_count": len(statistical),
            "candidates": [
                {
                    "candidate_key": record["candidate_key"],
                    "factor_id": record["factor_id"],
                    "direction": record["direction"],
                    "coverage": coverage.get(record["factor_id"]),
                    "marginal_ic": record["marginal_ic"],
                    "statistical_gate": record["statistical_gate"],
                    "status": record["status"],
                }
                for record in records
            ],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return preview

    p5_payload = _read_json(P5_RESULT)
    if p5_payload.get("research_id") is None:
        raise ValueError("P5 baseline research ID is missing")
    all_factor_ids = list(
        dict.fromkeys(
            [
                *current_ids,
                *[record["factor_id"] for record in statistical],
            ]
        )
    )
    service = _service()
    prepared_full = p5._prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=p5.START,
        end=p5.END,
    )
    prepared_recent = p5._prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=p5.RECENT_START,
        end=p5.END,
    )
    prepared_weak = p5._prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=p5.WEAK_START,
        end=p5.WEAK_END,
    )
    baseline = _baseline(
        service,
        prepared_full,
        prepared_recent,
        prepared_weak,
        current_weights,
        current_directions,
    )
    baseline_gate = p5._gate_metrics(
        baseline["full_2x"], baseline["recent_2x"], baseline["full_3x"]
    )

    screen: dict[str, dict[str, Any]] = {}
    passing_screen: list[dict[str, Any]] = []
    final_ids: list[str] | None = None
    final_directions: dict[str, str] = {}
    final_portfolio: dict[str, Any] | None = None
    final_portfolio_gate: dict[str, Any] | None = None
    combined_attempt: dict[str, Any] | None = None
    ablation: dict[str, Any] | None = None
    ablation_gate: dict[str, Any] | None = None
    rolling: dict[str, Any] | None = None
    warning_5x: dict[str, Any] | None = None

    if statistical:
        loo = p5._loo_contributions(
            service,
            symbols,
            scored,
            current_weights,
            current_directions,
            current_ids,
            prepared_full=prepared_full,
            prepared_recent=prepared_recent,
            baseline_full=baseline["full_2x"],
            baseline_recent=baseline["recent_2x"],
        )
        weakest_factors = sorted(
            loo,
            key=lambda factor_id: (
                loo[factor_id]["full_marginal_contribution"],
                loo[factor_id]["recent_marginal_contribution"],
                factor_id,
            ),
        )
        screen = p5._run_candidate_screen(
            service,
            statistical,
            marginal,
            current_ids,
            current_directions,
            weakest_factors[0],
            prepared_full,
            prepared_recent,
            prepared_weak,
            baseline["full_2x"],
            baseline["recent_2x"],
            baseline["full_3x"],
            baseline["weak_2x"],
        )
        for record in records:
            screen_record = screen.get(record["candidate_key"])
            if screen_record is None:
                continue
            record["portfolio_result"] = screen_record["portfolio"]
            record["portfolio_gate"] = screen_record["portfolio_gate"]
            record["status"] = screen_record["status"]
        passing_screen = [
            record for record in statistical if record["status"] == "portfolio_pass"
        ]
        if passing_screen:
            proposed_ids, proposed_directions = select_final_set(
                passing_screen,
                current_ids,
                current_directions,
                weakest_factors=weakest_factors,
            )
            final_candidates = [
                record for record in passing_screen if record["factor_id"] in proposed_ids
            ]
            if len(final_candidates) == 1:
                candidate_result = final_candidates[0]["portfolio_result"]
                if set(candidate_result["factor_ids"]) == set(proposed_ids):
                    final_ids = list(candidate_result["factor_ids"])
                    final_directions = {
                        **current_directions,
                        final_candidates[0]["factor_id"]: final_candidates[0]["direction"],
                    }
                    final_portfolio = candidate_result
                    final_portfolio_gate = final_candidates[0]["portfolio_gate"]
            if final_ids is None:
                combined_attempt = _run_set(
                    service,
                    prepared_full,
                    prepared_recent,
                    prepared_weak,
                    proposed_ids,
                    proposed_directions,
                    baseline,
                    label_prefix="p8_combined",
                )
                if combined_attempt["portfolio_gate"]["pass"]:
                    final_ids = list(proposed_ids)
                    final_directions = dict(proposed_directions)
                    final_portfolio = combined_attempt
                    final_portfolio_gate = combined_attempt["portfolio_gate"]
                else:
                    fallback = sorted(
                        passing_screen,
                        key=lambda item: (
                            item["marginal_ic"].get("directional_delta_mean") or -999.0,
                            item["factor_id"],
                        ),
                        reverse=True,
                    )[0]
                    final_ids = list(fallback["portfolio_result"]["factor_ids"])
                    final_directions = {
                        **current_directions,
                        fallback["factor_id"]: fallback["direction"],
                    }
                    final_portfolio = fallback["portfolio_result"]
                    final_portfolio_gate = fallback["portfolio_gate"]
                    fallback["status"] = "combined_portfolio_rejected_fallback"
                    for record in records:
                        if record in final_candidates and record is not fallback:
                            record["status"] = "combined_portfolio_rejected"

            if final_ids is not None:
                final_scoring, selected_final_directions = p5._equal_inputs(
                    final_ids, final_directions
                )
                del final_scoring
                ablation = p5._run_ablation(
                    service,
                    final_ids,
                    selected_final_directions,
                    {
                        "full": prepared_full,
                        "recent": prepared_recent,
                        "weak": prepared_weak,
                    },
                    {
                        "full": (p5.START, p5.END),
                        "recent": (p5.RECENT_START, p5.END),
                        "weak": (p5.WEAK_START, p5.WEAK_END),
                    },
                )
                new_ids = [factor_id for factor_id in final_ids if factor_id not in current_ids]
                ablation_gate = p5._ablation_candidate_gate(ablation, new_ids)
                if not ablation_gate["pass"]:
                    for record in records:
                        if record["factor_id"] in final_ids:
                            record["status"] = "ablation_rejected"
                    final_ids = None
                    final_directions = {}
                    final_portfolio = None
                    final_portfolio_gate = None

            if final_ids is not None:
                final_scoring, selected_final_directions = p5._equal_inputs(
                    final_ids, final_directions
                )
                rolling = p5._rolling_comparison(
                    service,
                    symbols,
                    scored,
                    all_factor_ids,
                    current_weights,
                    current_directions,
                    final_scoring,
                    selected_final_directions,
                )
                if not rolling["gate"]["pass"]:
                    for record in records:
                        if record["factor_id"] in final_ids:
                            record["status"] = "rolling_rejected"
                    final_ids = None
                    final_directions = {}
                    final_portfolio = None
                    final_portfolio_gate = None

            if final_ids is not None:
                warning_5x = _five_x_warning(
                    service, prepared_full, final_ids, final_directions
                )

    candidate_by_id = {record["factor_id"]: record for record in records}
    set_version = None
    set_version_created = False
    if final_ids is not None:
        output_path = _normalize_output_path(output_path)
        set_version, _set_head, set_version_created = p5._ensure_watch_version(
            sets,
            head,
            current_version,
            final_ids,
            final_directions,
            candidate_by_id,
            output_path,
            evidence_kind="financial_increment_scan",
            method_label="P8",
            rationale_suffix="通过 P8 财务 PIT 增量统计与 T+1 组合门槛。",
            change_reason="P8 财务增量研究通过：加入已验证 PIT 财务 legs",
            head_reason="P8 财务增量研究通过，保持 watch 且不激活",
        )

    output_path = _normalize_output_path(output_path)
    input_manifest = _input_manifest(data_dir, audit, p5_payload, current_version)
    input_manifest_sha = sha256_json(input_manifest)
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha,
            "financial_pit_research_id": audit["research_id"],
            "p5_research_id": p5_payload["research_id"],
            "p6_admission_catalog_snapshot_id": admission.get("catalog_snapshot_id"),
            "panel_rows": scored.height,
            "symbol_count": len(symbols),
            "coverage": coverage,
        }
    )
    candidate_records: list[dict[str, Any]] = []
    for record in records:
        candidate_records.append(
            {
                "candidate_key": record["candidate_key"],
                "economic_key": record["economic_key"],
                "factor_id": record["factor_id"],
                "direction": record["direction"],
                "label": record["label"],
                "transform": record["transform"],
                "coverage": coverage.get(record["factor_id"]),
                "marginal_ic": record.get("marginal_ic", {}),
                "statistical_gate": record.get("statistical_gate", {}),
                "portfolio_result": record.get("portfolio_result"),
                "portfolio_gate": record.get("portfolio_gate"),
                "status": record.get("status"),
            }
        )
    negative_knowledge = []
    for record in candidate_records:
        if final_ids is not None and record["factor_id"] in final_ids:
            continue
        negative_knowledge.append(
            {
                "candidate_key": record["candidate_key"],
                "factor_id": record["factor_id"],
                "status": record["status"],
                "reasons": _reasons_for_record(record),
            }
        )
    decision_status = _decision_status(
        statistical=statistical,
        passing_screen=passing_screen,
        final_ids=final_ids,
        set_version=set_version,
    )
    decision = {
        "status": decision_status,
        "active_version": None,
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "factor_ids": final_ids,
        "new_watch_version": set_version.version if set_version is not None else None,
        "set_version_created": set_version_created,
    }
    core = {
        "schema_version": 1,
        "method_version": METHOD_VERSION,
        "financial_pit_research_id": audit["research_id"],
        "p5_research_id": p5_payload["research_id"],
        "catalog_snapshot_id": p5_payload.get("catalog_snapshot_id"),
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
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
        },
        "base_set": {
            "set_id": current_version.set_id,
            "version": current_version.version,
            "content_sha256": current_version.content_sha256,
            "factor_ids": current_ids,
        },
        "candidate_count": len(candidate_records),
        "statistical_pass_count": len(statistical),
        "portfolio_pass_count": len(passing_screen),
        "candidates": candidate_records,
        "portfolio": {
            "baseline": baseline,
            "baseline_gate": baseline_gate,
            "combined_attempt": combined_attempt,
            "final": final_portfolio,
            "final_gate": final_portfolio_gate,
        },
        "ablation": ablation,
        "ablation_gate": ablation_gate,
        "rolling_year": rolling,
        "cost_warning_5x": warning_5x,
        "negative_knowledge": negative_knowledge,
        "decision": decision,
        "caveats": [
            "Current HS300 membership is applied to history; survivorship bias remains.",
            "Financial factors are PIT-verified by P7, but the result is a research admission test, not a profit guarantee.",
            "2x is the normal cost baseline and 3x is a hard stress gate; 5x is warning-only.",
            "No leverage, paper target, order, or production activation is produced.",
            "The factor set remains watch-only; active_version is null.",
        ],
    }
    research_id = sha256_json(core)
    payload = {**core, "research_id": research_id, "generated_at": utc_now()}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    published = _publish_json(output_path, payload)
    markdown_path = output_path.with_suffix(".md")
    _publish_markdown(markdown_path, render_markdown(published))
    ledger_record, _ledger_created = bind_research_evidence(
        data_dir,
        operation=OPERATION,
        evidence_paths=[output_path, markdown_path],
        research_id=published["research_id"],
        payload={
            "catalog_snapshot_id": published.get("catalog_snapshot_id"),
            "input_fingerprints": {
                "financial_pit_research_id": published["financial_pit_research_id"],
                "p5_research_id": published["p5_research_id"],
                "input_manifest_sha256": published["input_manifest_sha256"],
                "data_fingerprint": published["data_fingerprint"],
            },
            "metrics": {
                "candidate_count": published["candidate_count"],
                "statistical_pass_count": published["statistical_pass_count"],
                "portfolio_pass_count": published["portfolio_pass_count"],
                "final_factor_count": len(published["decision"]["factor_ids"] or []),
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
                "portfolio_pass_count": published["portfolio_pass_count"],
                "final_factor_ids": published["decision"]["factor_ids"],
                "decision": published["decision"]["status"],
                "new_watch_version": published["decision"]["new_watch_version"],
                "active_version": None,
                "ledger_record_id": ledger_record.record_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return published


def _normalize_output_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _publish_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = _read_json(path)
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"P8 result already exists: {path}")
        return existing
    atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"P8 markdown already exists: {path}")
        return
    atomic_write_text(path, text)


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# P8 财务因子增量准入报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- P7 财务 PIT：`{payload['financial_pit_research_id']}`",
        f"- P5 基线：`{payload['p5_research_id']}`",
        f"- 候选数：{payload['candidate_count']}",
        f"- 统计通过：{payload['statistical_pass_count']}",
        f"- 组合通过：{payload['portfolio_pass_count']}",
        f"- 决策：`{payload['decision']['status']}`",
        "- 结论只允许形成 watch，`active_version` 保持 null。",
        "",
        "## 候选增量",
        "",
        "| 候选 | 方向 | 覆盖率 | 边际 IC | NW p | q | 最大相关 | 统计门槛 | 组合门槛 | 结论 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for candidate in payload["candidates"]:
        marginal = candidate.get("marginal_ic") or {}
        portfolio_gate = candidate.get("portfolio_gate") or {}
        coverage = candidate.get("coverage") or {}
        lines.append(
            f"| `{candidate['factor_id']}` | {candidate['direction']} | "
            f"{_fmt(coverage.get('non_null_ratio'))} | "
            f"{_fmt(marginal.get('directional_delta_mean'))} | "
            f"{_fmt(marginal.get('delta_p_value'))} | "
            f"{_fmt(marginal.get('delta_q_value'))} | "
            f"{_fmt(marginal.get('max_abs_correlation_for_gate'))} | "
            f"{'pass' if candidate['statistical_gate'].get('pass') else 'reject'} | "
            f"{'pass' if portfolio_gate.get('pass') else ('-' if not portfolio_gate else 'reject')} | "
            f"{candidate['status']} |"
        )
    lines.extend(
        [
            "",
            "## 组合基线",
            "",
            "| 场景 | 总收益 | 年化 | Sharpe | 最大回撤 | 交易数 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for label, result in payload["portfolio"]["baseline"].items():
        stats = result["stats"]
        lines.append(
            f"| {label} | {_fmt(stats.get('total_return'))} | "
            f"{_fmt(stats.get('annual_return'))} | {_fmt(stats.get('sharpe'))} | "
            f"{_fmt(stats.get('max_drawdown'))} | {_fmt(stats.get('n_trades'))} |"
        )
    lines.extend(
        [
            "",
            "## 最终结论",
            "",
            f"- 状态：`{payload['decision']['status']}`",
            f"- 最终因子：{payload['decision']['factor_ids'] or '无'}",
            f"- 新 watch 版本：{payload['decision']['new_watch_version'] or '无'}",
            "- `active_version`：None",
            "- 纸面目标/订单/生产启用：否",
            "",
            "## 负知识",
            "",
        ]
    )
    if payload["negative_knowledge"]:
        for item in payload["negative_knowledge"]:
            lines.append(f"- `{item['candidate_key']}`：{', '.join(item['reasons'])}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 门槛说明",
            "",
            "- 财务因子必须先由 P7 的 `verified_factor_ids` 证明 PIT 合法，并且 P6 标记为 portfolio-ready。",
            "- 边际 IC：基准组合 80% + 候选 20%，NW lag=5，p≤0.10 且 BH-FDR q≤0.10。",
            "- 相关性：候选与现有成员的最大平均绝对日截面相关≤0.90。",
            "- 组合：20 仓等权、open_t+1、无杠杆；2x 基准、3x 硬压力；5x 只作 warning。",
            "- 滚动一年：正收益窗口比例不得低于基线，中位收益不得比基线差超过 1pp。",
            "- 当前 HS300 名单回填历史存在幸存者偏差。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_financial_research(DATA, output_path=args.output, stats_only=args.stats_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
