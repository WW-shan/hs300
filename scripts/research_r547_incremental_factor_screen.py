#!/usr/bin/env python3
"""R547: PIT incremental screen for financial, sentiment and industry factors."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import stats_v2  # noqa: E402
from app.backtest.fundamentals import (  # noqa: E402
    FUNDAMENTAL_FACTOR_NAMES,
    attach_fundamental_factors,
    load_fundamental_snapshot,
)
from app.factor_research.extended_parameter_data import load_existing_ext_factors  # noqa: E402
from app.factor_research.incremental import (  # noqa: E402
    build_rank_composite,
    pairwise_cross_sectional_correlation,
)
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.signal_scan import calculate_rank_ic  # noqa: E402

RESEARCH_TYPE = "statistical_screen"
RESEARCH_ID = "R547"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r547_incremental_factor_screen_preregistration.json"
)
RUNNER = "scripts/research_r547_incremental_factor_screen.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
CANDIDATE_ROOT = DATA / "factor_research" / "candidates" / "r547"
PREREG = RESEARCH / "r547_incremental_factor_screen_preregistration.json"
RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
MARKDOWN = RESEARCH / "r547_incremental_factor_screen_result.md"
PIT_BUNDLE = RESEARCH / "r526_unified_pit_bundle_window"
MEMBERSHIP = PIT_BUNDLE / "daily_snapshots.parquet"
INDUSTRY = PIT_BUNDLE / "industry_history.parquet"
EXT_ROOT = DATA / "ext_data" / "alt_hs300" / "timeseries"
ALT_CONFIG = DATA / "ext_data" / "alt_hs300" / "config.json"
R543_RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
R536_RESULT = RESEARCH / "r536a_finalist_cluster_fdr_result.json"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"

START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
LOAD_START = date(2019, 1, 1)
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
MINIMUM_COVERAGE = 0.75
MINIMUM_VALID_DATES = 500
MINIMUM_TRAIN_IC = 0.005
MAX_INCREMENTAL_P = 0.20
MAX_FDR_Q = 0.20
MAX_STABLE7_CORRELATION = 0.85

STABLE7 = (
    {"family_id": "amount_level", "parameter": 120, "tier": "P2"},
    {"family_id": "bollinger_width", "parameter": 10, "tier": "P2"},
    {"family_id": "dividend_yield_quantile", "parameter": 0.7, "tier": "P4"},
    {"family_id": "fund_holding_ratio_absolute", "parameter": -0.05, "tier": "P3"},
    {"family_id": "max_return", "parameter": 60, "tier": "P2"},
    {"family_id": "turnover_level", "parameter": 120, "tier": "P2"},
    {"family_id": "vol_of_vol", "parameter": 20, "tier": "P1"},
)
EXPECTED_CANDIDATES = (
    "cf_r547_value_quality",
    "cf_r547_value_quality_safety",
    "cf_r547_cash_quality",
    "cf_r547_growth_quality",
    "cf_r547_operating_efficiency",
    "cf_r547_smart_money_quality",
    "cf_r547_industry_quality",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _load_module(name: str) -> Any:
    path = ROOT / "scripts" / f"{name}.py"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"immutable research artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def _ext_timeseries_hash() -> str:
    """Hash the full extension panel, including partition paths."""
    if not EXT_ROOT.is_dir():
        raise FileNotFoundError(EXT_ROOT)
    digest = hashlib.sha256()
    for path in sorted(EXT_ROOT.glob("date=*/part.parquet")):
        digest.update(path.relative_to(DATA).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _input_paths(root: Path) -> dict[str, Path]:
    return {
        "membership": root / MEMBERSHIP.relative_to(ROOT),
        "industry": root / INDUSTRY.relative_to(ROOT),
        "metrics": root / "data" / "financials" / "metrics" / "part.parquet",
        "income": root / "data" / "financials" / "income" / "part.parquet",
        "balance_sheet": root / "data" / "financials" / "balance_sheet" / "part.parquet",
        "cash_flow": root / "data" / "financials" / "cash_flow" / "part.parquet",
        "alt_config": root / ALT_CONFIG.relative_to(ROOT),
        "r543_result": root / R543_RESULT.relative_to(ROOT),
        "r536_result": root / R536_RESULT.relative_to(ROOT),
        "r530_result": root / R530_RESULT.relative_to(ROOT),
    }


def load_candidate_definitions(root: Path) -> dict[str, dict[str, Any]]:
    root = Path(root)
    candidate_root = root / CANDIDATE_ROOT.relative_to(ROOT)
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted(candidate_root.glob("*.json")):
        if path.name.endswith(".sha256"):
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        factor_id = str(raw.get("id") or "")
        if raw.get("kind") != "composite":
            raise ValueError(f"R547 candidate must be composite: {path.name}")
        if raw.get("status") != "draft":
            raise ValueError(f"R547 candidate must remain draft: {path.name}")
        members = raw.get("members")
        if not isinstance(members, dict) or not 3 <= len(members) <= 6:
            raise ValueError(f"R547 candidate has invalid member count: {path.name}")
        weights = [float(weight) for weight in members.values()]
        if any(weight == 0.0 for weight in weights):
            raise ValueError(f"R547 candidate has zero weight: {path.name}")
        if abs(sum(abs(weight) for weight in weights) - 1.0) > 1e-9:
            raise ValueError(f"R547 candidate weights must sum to one: {path.name}")
        verify_sidecar(path)
        if factor_id in definitions:
            raise ValueError(f"duplicate R547 candidate: {factor_id}")
        definitions[factor_id] = raw
    if set(definitions) != set(EXPECTED_CANDIDATES):
        raise RuntimeError(
            f"R547 expected {sorted(EXPECTED_CANDIDATES)}, got {sorted(definitions)}"
        )
    return definitions


def _ext_members(definitions: dict[str, dict[str, Any]]) -> dict[str, str]:
    members = {
        member
        for definition in definitions.values()
        for member in definition["members"]
        if str(member).startswith("ext_alt_hs300_")
    }
    return {member: member.removeprefix("ext_alt_hs300_") for member in sorted(members)}


def materialize_composite(
    frame: pl.DataFrame,
    definition: dict[str, Any],
    *,
    minimum_weight_coverage: float = 0.5,
) -> pl.DataFrame:
    """Materialize one research composite with daily rank z-scores.

    The composite JSON weights encode direction by sign, matching the project's
    composite definition convention (negative means low is good).
    """
    weights = {
        str(member): abs(float(weight))
        for member, weight in definition["members"].items()
    }
    directions = {
        str(member): "high" if float(weight) > 0 else "low"
        for member, weight in definition["members"].items()
    }
    scored, _ = build_rank_composite(
        frame,
        weights,
        directions,
        str(definition["id"]),
        minimum_weight_coverage=minimum_weight_coverage,
    )
    return scored


def _load_membership(root: Path) -> pl.DataFrame:
    r530 = _load_module("research_r530_unified_factor_library_mc")
    return r530.load_pit_membership(root)


def _load_price_panel(root: Path, membership: pl.DataFrame) -> pl.DataFrame:
    r530 = _load_module("research_r530_unified_factor_library_mc")
    symbols = sorted(membership["symbol"].unique().to_list())
    paths = sorted((root / "data" / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(root / "data" / "kline_daily_enriched")
    prices = (
        pl.scan_parquet([str(path) for path in paths])
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= LOAD_START)
            & (pl.col("date") <= END)
        )
        .select(
            [
                "symbol",
                "date",
                "close",
                "raw_close",
                "open",
                "high",
                "low",
                "volume",
                "amount",
                "turnover_rate",
                "consecutive_limit_ups",
            ]
        )
        .collect()
        .sort(["symbol", "date"])
    ).with_columns(
        pl.coalesce([pl.col("raw_close"), pl.col("close")]).alias("raw_close"),
        (
            pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0
        ).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias("log_float_mv"),
    )
    industry = r530.load_pit_industry(root)
    prices = r530.map_industry_asof(prices, industry)
    return prices.join(membership, on=["symbol", "date"], how="inner").sort(
        ["date", "symbol"]
    )


def _add_candidate_sources(
    panel: pl.DataFrame,
    definitions: dict[str, dict[str, Any]],
) -> pl.DataFrame:
    snapshot = load_fundamental_snapshot(DATA)
    panel = attach_fundamental_factors(panel, snapshot, FUNDAMENTAL_FACTOR_NAMES)
    ext_map = _ext_members(definitions)
    if ext_map:
        ext = load_existing_ext_factors(DATA, ext_map)
        panel = panel.join(ext, on=["symbol", "date"], how="left")
    for definition in definitions.values():
        panel = materialize_composite(panel, definition)
    missing = [
        member
        for definition in definitions.values()
        for member in definition["members"]
        if member not in panel.columns
    ]
    if missing:
        raise RuntimeError(f"R547 candidate dependencies missing: {sorted(set(missing))}")
    return panel


def _stable_core(
    root: Path,
    context: Any,
    membership: pl.DataFrame,
) -> pl.DataFrame:
    r530 = _load_module("research_r530_unified_factor_library_mc")
    r531 = _load_module("research_r531_factor_library_core")
    windows, thresholds = r530._registry()
    families = {**windows, **thresholds}
    frames: dict[str, pl.DataFrame] = {}
    candidates: dict[str, Any] = {}
    members: list[dict[str, Any]] = []
    for spec in STABLE7:
        family_id = str(spec["family_id"])
        family = families.get(family_id)
        if family is None:
            raise RuntimeError(f"stable7 family missing from registry: {family_id}")
        candidate = r530.Candidate(
            source_tier=str(spec["tier"]),
            source_result="",
            source_result_sha256="",
            family_id=family_id,
            family=family,
            parameter=spec["parameter"],
            source_recommended_parameter=spec["parameter"],
            source_recommended_trial={},
        )
        candidates[family_id] = SimpleNamespace(candidate=candidate)
        frames[family_id] = r530._factor_frame(candidate, candidate.parameter, context)
        members.append({"family_id": family_id})
    return r531._core_frame(members, candidates, frames, membership)


def _candidate_panel(
    panel: pl.DataFrame,
    factor_id: str,
) -> pl.DataFrame:
    denominator = pl.col(factor_id).is_not_null().sum().over("date")
    return panel.select(
        "symbol",
        "date",
        "forward_return_5d",
        "industry_l1",
        "log_float_mv",
        pl.col(factor_id).alias("_candidate_score"),
        pl.when(denominator > 0)
        .then(pl.col(factor_id).rank("average").over("date") / denominator)
        .otherwise(None)
        .alias("_candidate_pct"),
    ).filter(pl.col("_candidate_score").is_not_null())


def _ic_summary(ic: pl.DataFrame, start: date, end: date) -> dict[str, Any]:
    from app.factor_research.parameter_mc import stats_for_period

    return stats_for_period(
        ic,
        start,
        end,
        1,
        horizon=HORIZON,
        include_distribution=True,
    )


def _delta_summary(
    paired: pl.DataFrame,
    start: date,
    end: date,
) -> dict[str, Any]:
    values = paired.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).select((pl.col("combined_ic") - pl.col("base_ic")).alias("delta"))["delta"].to_numpy()
    if values.size <= HORIZON + 2:
        return {"n_dates": int(values.size), "directional_delta": None, "nw_t": None, "p_value": None}
    nw = stats_v2.newey_west_t(values, lag=HORIZON)
    return {
        "n_dates": int(values.size),
        "directional_delta": round(float(values.mean()), 6),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": (
            round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
            if nw is not None
            else None
        ),
    }


def _coverage(panel: pl.DataFrame, factor_id: str) -> dict[str, Any]:
    valid = panel.filter(pl.col(factor_id).is_not_null())
    widths = valid.group_by("date").len().sort("date")["len"]
    return {
        "rows": panel.height,
        "valid_factor_rows": valid.height,
        "factor_non_null_ratio": round(valid.height / panel.height, 6) if panel.height else 0.0,
        "valid_dates": widths.len(),
        "minimum_cross_section": int(widths.min()) if widths.len() else 0,
        "median_cross_section": round(float(widths.median()), 3) if widths.len() else 0.0,
    }


def _evaluate_candidate(
    factor_id: str,
    definition: dict[str, Any],
    base: pl.DataFrame,
    panel: pl.DataFrame,
) -> dict[str, Any]:
    candidate = _candidate_panel(panel, factor_id)
    work = base.join(candidate, on=["symbol", "date"], how="inner").sort(["date", "symbol"])
    if work.is_empty():
        raise RuntimeError(f"R547 candidate has no overlapping rows: {factor_id}")
    work = work.with_columns(
        (0.8 * pl.col("_base_pct") + 0.2 * pl.col("_candidate_pct")).alias("_combined_pct")
    )
    base_ic = calculate_rank_ic(
        work,
        "_base_pct",
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )
    candidate_ic = calculate_rank_ic(
        work,
        "_candidate_pct",
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )
    combined_ic = calculate_rank_ic(
        work,
        "_combined_pct",
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )
    paired = (
        base_ic.select(["date", pl.col("ic").alias("base_ic")])
        .join(
            combined_ic.select(["date", pl.col("ic").alias("combined_ic")]),
            on="date",
            how="inner",
        )
        .sort("date")
    )
    raw = {
        "train": _ic_summary(candidate_ic, START, TRAIN_END),
        "validation": _ic_summary(candidate_ic, VALIDATION_START, VALIDATION_END),
        "recent": _ic_summary(candidate_ic, RECENT_START, END),
        "full": _ic_summary(candidate_ic, START, END),
    }
    incremental = {
        "train": _delta_summary(paired, START, TRAIN_END),
        "validation": _delta_summary(paired, VALIDATION_START, VALIDATION_END),
        "recent": _delta_summary(paired, RECENT_START, END),
        "full": _delta_summary(paired, START, END),
    }
    _, correlations = pairwise_cross_sectional_correlation(
        work,
        {"stable7": "_base_pct", "candidate": "_candidate_pct"},
        minimum_symbols=MINIMUM_CROSS_SECTION,
    )
    corr = correlations.get("stable7|candidate") or correlations.get("candidate|stable7") or {}
    return {
        "factor_id": factor_id,
        "label": definition["label"],
        "members": definition["members"],
        "coverage": _coverage(panel, factor_id),
        "raw_ic": raw,
        "incremental": incremental,
        "correlation_to_stable7": (
            round(abs(float(corr["mean_abs"])), 6)
            if corr.get("mean_abs") is not None
            else None
        ),
        "train_ic": raw["train"].get("directional_ic"),
        "validation_ic": raw["validation"].get("directional_ic"),
        "recent_ic": raw["recent"].get("directional_ic"),
    }


def candidate_gate(candidate: dict[str, Any]) -> dict[str, Any]:
    coverage = candidate.get("coverage") or {}
    incremental = candidate.get("incremental") or {}
    train_inc = incremental.get("train") or {}
    checks = {
        "coverage_ge_0_75": float(coverage.get("factor_non_null_ratio") or 0.0) >= MINIMUM_COVERAGE,
        "valid_dates_ge_500": int(coverage.get("valid_dates") or 0) >= MINIMUM_VALID_DATES,
        "train_ic_ge_0_005": float(candidate.get("train_ic") or 0.0) >= MINIMUM_TRAIN_IC,
        "validation_ic_positive": float(candidate.get("validation_ic") or 0.0) > 0.0,
        "incremental_train_positive": float(train_inc.get("directional_delta") or 0.0) > 0.0,
        "incremental_nw_p_le_0_20": (
            train_inc.get("nw_p") is not None and float(train_inc["nw_p"]) <= MAX_INCREMENTAL_P
        ),
        "fdr_q_le_0_20": (
            candidate.get("q_value") is not None and float(candidate["q_value"]) <= MAX_FDR_Q
        ),
        "stable7_corr_le_0_85": (
            candidate.get("correlation_to_stable7") is None
            or float(candidate["correlation_to_stable7"]) <= MAX_STABLE7_CORRELATION
        ),
    }
    diagnostics = {
        "recent_ic_positive": float(candidate.get("recent_ic") or 0.0) > 0.0,
        "recent_incremental_positive": float(
            (incremental.get("recent") or {}).get("directional_delta") or 0.0
        ) > 0.0,
    }
    selection_pass = all(checks.values())
    return {
        "checks": {**checks, **diagnostics},
        "selection_checks": checks,
        "selection_pass": selection_pass,
        "recent_is_diagnostic_only": True,
        "conclusion": "finalist" if selection_pass else "rejected",
    }


def _assign_fdr(rows: dict[str, dict[str, Any]]) -> None:
    ids = sorted(rows)
    incremental_p = [
        (rows[factor_id]["incremental"].get("train") or {}).get("p_value")
        for factor_id in ids
    ]
    raw_p = [
        (rows[factor_id]["raw_ic"].get("train") or {}).get("p_value")
        for factor_id in ids
    ]
    incremental_q = stats_v2.bh_fdr_qvalues(incremental_p)
    raw_q = stats_v2.bh_fdr_qvalues(raw_p)
    for factor_id, inc_q, raw_q_value in zip(ids, incremental_q, raw_q, strict=True):
        rows[factor_id]["q_value"] = (
            round(float(inc_q), 8) if inc_q is not None else None
        )
        rows[factor_id]["raw_q_value"] = (
            round(float(raw_q_value), 8) if raw_q_value is not None else None
        )


def scientific_design() -> dict[str, Any]:
    return {
        "stable7_members": [row["family_id"] for row in STABLE7],
        "stable7_parameters": [
            {"family_id": row["family_id"], "parameter": row["parameter"]}
            for row in STABLE7
        ],
        "candidates": list(EXPECTED_CANDIDATES),
        "score_method": "daily cross-sectional rank z-score per member; sign encodes direction; mean-available composite",
        "minimum_weight_coverage": 0.5,
        "recent_is_diagnostic_only": True,
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
        },
        "gates": {
            "coverage_min": MINIMUM_COVERAGE,
            "valid_dates_min": MINIMUM_VALID_DATES,
            "train_ic_min": MINIMUM_TRAIN_IC,
            "validation_ic_positive": True,
            "incremental_train_positive": True,
            "incremental_nw_p_max": MAX_INCREMENTAL_P,
            "fdr_q_max": MAX_FDR_Q,
            "stable7_correlation_max": MAX_STABLE7_CORRELATION,
            "recent_policy": "report_only_not_used_for_selection",
        },
        "execution": {
            "matching": "signal_close_to_t5_close_return",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R547 preregistration already exists: {PREREG}")
    definitions = load_candidate_definitions(root)
    inputs = _input_paths(root)
    candidate_hashes = {
        factor_id: sha256(root / CANDIDATE_ROOT.relative_to(ROOT) / f"{factor_id}.json")
        for factor_id in definitions
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Do financial quality/value/growth, operating efficiency, sentiment-flow "
            "and industry-quality composites add stable incremental information to the "
            "train+validation stable seven-factor baseline?"
        ),
        "hypothesis": (
            "At least one economically distinct composite will improve the stable7 "
            "composite signal in both training and validation without excessive "
            "correlation, while recent-year behavior is reported only as a risk diagnostic."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": {
            **{name: sha256(path) for name, path in inputs.items()},
            "alt_timeseries": _ext_timeseries_hash(),
            "candidate_definitions": policy_hash(candidate_hashes),
        },
        "parameters": {
            **scientific_design(),
            "candidate_definitions": candidate_hashes,
        },
        "sample": scientific_design()["windows"],
        "execution": scientific_design()["execution"],
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "selection_uses_training_and_validation_only",
            "recent_not_used_for_selection",
            "financial_factors_use_announcement_t_plus_one_gate",
            "r526_pit_membership_and_industry_used",
            "candidate_definitions_sidecar_bound",
            "no_portfolio_return_claim",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def policy_hash(value: dict[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R547 runner hash no longer matches preregistration")
    definitions = load_candidate_definitions(root)
    expected_candidate_hashes = {
        factor_id: sha256(root / CANDIDATE_ROOT.relative_to(ROOT) / f"{factor_id}.json")
        for factor_id in definitions
    }
    if prereg["parameters"]["candidate_definitions"] != expected_candidate_hashes:
        raise RuntimeError("R547 candidate definition hash changed")
    if prereg["input_hashes"]["candidate_definitions"] != policy_hash(expected_candidate_hashes):
        raise RuntimeError("R547 candidate definition aggregate hash changed")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R547 input changed: {name}")
    if _ext_timeseries_hash() != prereg["input_hashes"].get("alt_timeseries"):
        raise RuntimeError("R547 extension timeseries changed")


def _as_public(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "design": payload["design"],
        "candidates": payload["candidates"],
        "finalists": payload["finalists"],
        "data_gaps": payload["data_gaps"],
        "baseline": payload["baseline"],
        "conclusion": payload["conclusion"],
        "known_limits": payload["known_limits"],
        "output_policy": payload["output_policy"],
    }


def build_payload(
    *,
    base: pl.DataFrame,
    candidate_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _assign_fdr(candidate_rows)
    for factor_id in sorted(candidate_rows):
        candidate_rows[factor_id]["gate"] = candidate_gate(candidate_rows[factor_id])
    finalists = [
        factor_id
        for factor_id, row in sorted(candidate_rows.items())
        if row["gate"]["selection_pass"]
    ]
    data_gaps = [
        factor_id
        for factor_id, row in sorted(candidate_rows.items())
        if not row["gate"]["selection_checks"].get("coverage_ge_0_75", False)
        or not row["gate"]["selection_checks"].get("valid_dates_ge_500", False)
    ]
    base_coverage = _coverage(
        base.with_columns(pl.col("_base_pct").alias("_base_valid")),
        "_base_valid",
    )
    baseline = {
        "members": [row["family_id"] for row in STABLE7],
        "parameters": STABLE7,
        "coverage": base_coverage,
        "dataset_rows": base.height,
    }
    conclusion = "watch_only" if finalists else "rejected"
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "design": scientific_design(),
        "baseline": baseline,
        "candidates": candidate_rows,
        "finalists": finalists,
        "data_gaps": data_gaps,
        "conclusion": conclusion,
        "known_limits": [
            "Historical constituent and industry vintages are reconstructed project inputs, not provider publication snapshots.",
            "Financial fields use the project's statutory-deadline fallback and T+1 publication gate; this proves timing, not alpha.",
            "Sentiment/flow extension fields are declared PIT, not independently byte-audited in this round.",
            "Recent-year results were already visible in earlier research and are diagnostic only; finalist selection uses training and validation.",
            "A statistical incremental IC result is not a portfolio return, order instruction or production authorization.",
            "The research candidates remain draft definitions; no active version or leverage is produced.",
        ],
        "negative_knowledge": [],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R547 增量因子统计筛选",
        "",
        "> 这是项目数据上的统计筛选，不是组合收益或交易结论。finalist 只代表可以进入下一轮正式加一测试。",
        "",
        "## 基线",
        "",
        f"- stable7：{', '.join(f'`{item}`' for item in payload['baseline']['members'])}",
        f"- 基线覆盖行：{payload['baseline']['coverage']['valid_factor_rows']:,}",
        f"- 最终结论：`{payload['conclusion']}`",
        "",
        "## 候选结果",
        "",
        "| 候选 | 覆盖 | valid dates | train IC | validation IC | recent IC | 增量 IC | 增量 p | q | stable7 相关 | 筛选 | 近期诊断 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for factor_id, row in sorted(payload["candidates"].items()):
        cov = row["coverage"]
        raw = row["raw_ic"]
        inc = row["incremental"]["train"]
        gate = row["gate"]
        lines.append(
            f"| `{factor_id}` | {cov['factor_non_null_ratio']:.2%} | {cov['valid_dates']} "
            f"| {raw['train'].get('directional_ic')} | {raw['validation'].get('directional_ic')} "
            f"| {raw['recent'].get('directional_ic')} | {inc.get('directional_delta')} "
            f"| {inc.get('p_value')} | {row.get('q_value')} | "
            f"{row.get('correlation_to_stable7')} | "
            f"{'PASS' if gate['selection_pass'] else 'reject'} | "
            f"{'positive' if gate['checks']['recent_ic_positive'] else 'negative'} |"
        )
    lines += [
        "",
        "## 入选与数据缺口",
        "",
        f"- finalists：{', '.join(payload['finalists']) if payload['finalists'] else '无'}",
        f"- data gaps：{', '.join(payload['data_gaps']) if payload['data_gaps'] else '无'}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def publish_result(payload: dict[str, Any]) -> dict[str, Any]:
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate(
        {
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "runner": RUNNER,
            "runner_sha256": prereg["runner_sha256"],
            "preregistration_sha256": prereg_hash,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "conclusion": payload["conclusion"],
            "gate_results": {
                "finalists": payload["finalists"],
                "data_gaps": payload["data_gaps"],
                "selection_uses_recent": False,
            },
            "metrics": _as_public(payload),
            "source_result_hashes": [
                sha256(R543_RESULT),
                sha256(R536_RESULT),
                sha256(R530_RESULT),
            ],
            "formal_evidence": None,
            "known_limits": payload["known_limits"],
            "negative_knowledge": payload["negative_knowledge"],
            "output_policy": payload["output_policy"],
        }
    )
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r547_incremental_factor_screen_published",
    ).__dict__


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    definitions = load_candidate_definitions(root)
    membership = _load_membership(root)
    panel = _load_price_panel(root, membership)
    r530 = _load_module("research_r530_unified_factor_library_mc")
    context = r530._load_context(root, membership)
    # Reuse the same PIT price panel for every evaluator that consumes prices.
    context.prices = panel
    context.extended["prices"] = panel
    core = _stable_core(root, context, membership)
    base = core.with_columns(pl.col("factor_value").alias("_base_pct")).select(
        "symbol", "date", "_base_pct"
    )
    panel = _add_candidate_sources(panel, definitions)
    candidate_rows = {
        factor_id: _evaluate_candidate(factor_id, definition, base, panel)
        for factor_id, definition in definitions.items()
    }
    return build_payload(base=base, candidate_rows=candidate_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-preregistration", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R547 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(
        json.dumps(
            {
                "conclusion": payload["conclusion"],
                "finalists": payload["finalists"],
                "data_gaps": payload["data_gaps"],
                "published_result": str(published.get("result_path")),
                "ledger_record_id": published.get("ledger_record_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
