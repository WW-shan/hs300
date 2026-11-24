#!/usr/bin/env python3
"""R530: unified-PIT Monte Carlo revalidation of the whole factor library."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_TYPE = "statistical_screen"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r530_unified_factor_library_mc_preregistration.json"
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research"
PARAMETER_ROOT = RESEARCH / "parameters"
MC_ROOT = RESEARCH / "research" / "parameters"
PIT_BUNDLE = RESEARCH / "research" / "r526_unified_pit_bundle_window"
CATALOG_LATEST = RESEARCH / "catalog" / "latest.json"
PREREG = ROOT / RESEARCH_PREREGISTRATION
RESULT = RESEARCH / "research" / "r530_unified_factor_library_mc_result.json"
MARKDOWN = RESEARCH / "research" / "r530_unified_factor_library_mc_result.md"

WINDOW_START = date(2022, 5, 5)
END = date(2026, 11, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
LOAD_START = date(2019, 1, 1)
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
INITIAL_MC = 1000
FINAL_MC = 5000
BLOCK_SIZES = (10, 20, 40)
FDR_THRESHOLD = 0.10
SEED = 20261125

SOURCE_RESULTS = (
    ("P1", MC_ROOT / "p1_parameter_mc_2026-11-11.json"),
    ("P2", MC_ROOT / "p2_builtin_parameter_mc_2026-11-11_v2.json"),
    ("P3", MC_ROOT / "p3_extended_parameter_mc_2026-11-11_v3.json"),
    ("P4", MC_ROOT / "p4_threshold_parameter_mc_2026-11-11.json"),
)


@dataclass(frozen=True)
class Candidate:
    source_tier: str
    source_result: str
    source_result_sha256: str
    family_id: str
    family: dict[str, Any]
    parameter: int | float
    source_recommended_parameter: int | float
    source_recommended_trial: dict[str, Any]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    side = path.with_name(path.name + ".sha256")
    if not side.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = side.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _registry() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    windows = json.loads((PARAMETER_ROOT / "families.json").read_text(encoding="utf-8"))
    thresholds = json.loads((PARAMETER_ROOT / "thresholds.json").read_text(encoding="utf-8"))
    return (
        {row["family_id"]: row for row in windows["families"]},
        {row["family_id"]: row for row in thresholds["families"]},
    )


def load_recommended_candidates(root: Path) -> list[Candidate]:
    root = Path(root)
    window, threshold = _registry()
    family_by_id = {**window, **threshold}
    candidates: list[Candidate] = []
    for tier, source in SOURCE_RESULTS:
        source_path = root / source.relative_to(ROOT)
        source_hash = verify_sidecar(source_path)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        for family_id, result in payload["families"].items():
            if result.get("recommendation_status") != "recommended":
                continue
            family = family_by_id.get(family_id)
            if family is None:
                raise RuntimeError(f"recommended family is not registered: {family_id}")
            parameter = result.get("recommended_parameter")
            if parameter is None:
                raise RuntimeError(f"recommended result has no parameter: {family_id}")
            if not any(math.isclose(float(parameter), float(item), abs_tol=1e-12) for item in family["parameter_grid"]):
                raise RuntimeError(f"recommended parameter outside frozen grid: {family_id}")
            trial = result.get("recommended_trial")
            if not isinstance(trial, dict):
                raise RuntimeError(f"recommended result has no frozen trial: {family_id}")
            candidates.append(
                Candidate(
                    source_tier=tier,
                    source_result=source.relative_to(ROOT).as_posix(),
                    source_result_sha256=source_hash,
                    family_id=family_id,
                    family=family,
                    parameter=parameter,
                    source_recommended_parameter=parameter,
                    source_recommended_trial=trial,
                )
            )
    candidates.sort(key=lambda item: (item.source_tier, item.family_id))
    if len(candidates) != 15:
        raise RuntimeError(f"expected 15 frozen recommended candidates, got {len(candidates)}")
    return candidates


def load_pit_membership(root: Path) -> pl.DataFrame:
    path = Path(root) / PIT_BUNDLE.relative_to(ROOT) / "daily_snapshots.parquet"
    frame = pl.read_parquet(path, columns=["query_date", "symbol"])
    out = (
        frame.select(
            pl.col("query_date").cast(pl.Date).alias("date"),
            pl.col("symbol").cast(pl.String),
        )
        .drop_nulls()
        .unique()
        .sort(["date", "symbol"])
    )
    counts = out.group_by("date").len()["len"].unique().to_list()
    if counts != [300]:
        raise RuntimeError(f"R526 membership must contain 300 symbols per session: {counts}")
    return out


def load_pit_industry(root: Path) -> pl.DataFrame:
    path = Path(root) / PIT_BUNDLE.relative_to(ROOT) / "industry_history.parquet"
    return pl.read_parquet(path).select(
        pl.col("symbol").cast(pl.String),
        pl.col("effective_date").cast(pl.Date),
        pl.col("industry_pit").cast(pl.String),
    ).sort(["symbol", "effective_date"])


def map_industry_asof(panel: pl.DataFrame, industry: pl.DataFrame) -> pl.DataFrame:
    left = panel.with_columns(pl.col("date").cast(pl.Date)).sort(["symbol", "date"])
    right = industry.select(
        "symbol",
        pl.col("effective_date"),
        pl.col("industry_pit").alias("industry_l1"),
    ).sort(["symbol", "effective_date"])
    return left.join_asof(
        right,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
    )


def library_census(root: Path) -> dict[str, Any]:
    root = Path(root)
    latest_path = root / CATALOG_LATEST.relative_to(ROOT)
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    snapshot_id = str(latest["snapshot_id"]).removeprefix("sha256:")
    snapshot_path = root / RESEARCH.relative_to(ROOT) / "catalog" / "snapshots" / f"{snapshot_id}.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    window, threshold = _registry()
    source_payloads = []
    for _, path in SOURCE_RESULTS:
        source_path = root / path.relative_to(ROOT)
        verify_sidecar(source_path)
        source_payloads.append(json.loads(source_path.read_text(encoding="utf-8")))
    return {
        "catalog_snapshot_id": latest["snapshot_id"],
        "factor_count": int(snapshot["factor_count"]),
        "factor_data_class_counts": dict(sorted(Counter(row["data_class"] for row in snapshot["factors"]).items())),
        "factor_pit_status_counts": dict(sorted(Counter(row["pit_status"] for row in snapshot["factors"]).items())),
        "window_family_count": len(window),
        "threshold_family_count": len(threshold),
        "parameter_family_count": len(window) + len(threshold),
        "parameter_trial_count": sum(int(payload["trial_count"]) for payload in source_payloads),
        "prior_finalist_count": sum(int(payload["finalist_count"]) for payload in source_payloads),
        "prior_recommended_candidate_count": sum(int(payload["recommended_parameter_count"]) for payload in source_payloads),
        "prior_rejected_family_count": sum(int(payload["rejected_family_count"]) for payload in source_payloads),
        "source_results": [
            {"tier": tier, "path": path.relative_to(ROOT).as_posix(), "sha256": sha256(root / path.relative_to(ROOT))}
            for tier, path in SOURCE_RESULTS
        ],
        "universe": {
            "name": "r526_unified_daily_hs300",
            "membership_sessions_per_day": 300,
            "membership_source": (PIT_BUNDLE / "daily_snapshots.parquet").relative_to(ROOT).as_posix(),
            "industry_source": (PIT_BUNDLE / "industry_history.parquet").relative_to(ROOT).as_posix(),
        },
        "known_limits": [
            "Historical constituent and industry vintages remain point-in-time reconstructions, not provider publication snapshots.",
            "Parameter recommendations are frozen from earlier MC and are not a new independent discovery sample.",
            "Statistical IC evidence is not a portfolio return or a production authorization.",
            "No leverage, orders, paper target or active version are produced.",
        ],
    }


def _add_scripts_path() -> None:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _module(name: str) -> Any:
    _add_scripts_path()
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module






def _standard_factor_frame(raw: pl.DataFrame, output_column: str) -> pl.DataFrame:
    return raw.select(
        "symbol",
        pl.col("date").cast(pl.Date),
        pl.col(output_column).cast(pl.Float64).alias("factor_value"),
    ).drop_nulls(["symbol", "date"])


def _full_prices(symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(DATA / "kline_daily_enriched")
    return (
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
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
                "consecutive_limit_ups",
            ]
        )
        .collect()
        .sort(["symbol", "date"])
    ).with_columns(
        (
            pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0
        ).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias("log_float_mv"),
    )


@dataclass
class _Context:
    p1: Any
    p2: Any
    p3: Any
    p4: Any
    prices: pl.DataFrame
    market: pl.DataFrame
    extended: dict[str, Any]
    threshold_panel: pl.DataFrame


def _load_context(root: Path, membership: pl.DataFrame) -> _Context:
    root = Path(root)
    p1 = _module("research_alt_factor_parameters")
    p2 = _module("research_builtin_parameter_families")
    p3 = _module("research_extended_parameter_families")
    p4 = _module("research_threshold_parameter_families")
    symbols = sorted(membership["symbol"].unique().to_list())
    prices = _full_prices(symbols)
    industry = load_pit_industry(root)
    prices = map_industry_asof(prices, industry).sort(["symbol", "date"])
    market = p1._market_panel()
    extended = p3.build_research_context(DATA, symbols, END)
    extended["prices"] = prices
    extended["industry_source"] = "r526_industry_history"
    threshold_sources = {
        family.source_factor_id: family.source_factor_id.removeprefix("ext_alt_hs300_")
        for family in p4.load_p4_families(DATA)
        if family.source_factor_id is not None
    }
    # load_existing_ext_factors takes output-name -> source-field; it is the
    # project data loader used by the historical P4 run.
    threshold_panel = p4.load_existing_ext_factors(
        DATA,
        {factor_id: source for factor_id, source in threshold_sources.items()},
    )
    return _Context(p1, p2, p3, p4, prices, market, extended, threshold_panel)


def _candidate_kind(candidate: Candidate) -> str:
    return str(candidate.family.get("kind") or "window")


def _factor_frame(
    candidate: Candidate, parameter: int | float, context: _Context
) -> pl.DataFrame:
    family = candidate.family
    kind = _candidate_kind(candidate)
    if kind == "threshold":
        source_id = family["source_factor_id"]
        if source_id not in context.threshold_panel.columns:
            raise ValueError(f"threshold factor missing from project ext data: {source_id}")
        source = context.threshold_panel.select(
            "symbol", "date", pl.col(source_id).cast(pl.Float64).alias("factor_value")
        )
        raw = context.p4.threshold_indicator(
            source,
            float(parameter),
            family["mode"],
            family["direction"],
        )
        return _standard_factor_frame(raw, "factor_value")
    if kind == "event_holding":
        evaluator = context.p4.EVENT_HOLDING_EVALUATORS[family["event_key"]]
        raw, output, _formula = evaluator(context.extended, parameter)
        return _standard_factor_frame(raw, output)
    if candidate.source_tier == "P1":
        raw, output = context.p1.EVALUATORS[family["evaluator"]](
            context.prices, context.market, int(parameter)
        )
        return _standard_factor_frame(raw, output)
    if candidate.source_tier == "P2":
        raw, output, _formula = context.p2.BUILTIN_EVALUATORS[family["evaluator"]](
            context.prices, int(parameter)
        )
        return _standard_factor_frame(raw, output)
    raw, output, _formula = context.p3.EXTENDED_EVALUATORS[family["evaluator"]](
        context.extended, int(parameter)
    )
    return _standard_factor_frame(raw, output)


def _evaluation_frame(
    candidate: Candidate,
    parameter: int | float,
    context: _Context,
    membership: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    factor = _factor_frame(candidate, parameter, context)
    base = context.prices.select(
        "symbol",
        "date",
        "forward_return_5d",
        "log_float_mv",
        "industry_l1",
    )
    joined = (
        base.join(membership, on=["symbol", "date"], how="inner")
        .join(factor, on=["symbol", "date"], how="inner")
        .filter(
            pl.col("forward_return_5d").is_not_null()
            & (pl.col("date") >= WINDOW_START)
            & (pl.col("date") <= END)
        )
        .sort(["date", "symbol"])
    )
    return joined, context.p3._coverage(joined)


def _trial_stats(
    candidate: Candidate,
    parameter: int | float,
    context: _Context,
    membership: pl.DataFrame,
) -> tuple[dict[str, Any], pl.DataFrame]:
    joined, coverage = _evaluation_frame(candidate, parameter, context, membership)
    sign = -1 if candidate.family["direction"] == "low" else 1
    raw_ic = context.p3.calculate_rank_ic(
        joined, "factor_value", "forward_return_5d", minimum=MINIMUM_CROSS_SECTION
    )
    industry_ic = context.p3._neutralized_ic(
        joined, ["industry"], minimum_cross_section=MINIMUM_CROSS_SECTION
    )
    size_ic = context.p3._neutralized_ic(
        joined, ["size"], minimum_cross_section=MINIMUM_CROSS_SECTION
    )
    combined_ic = context.p3._neutralized_ic(
        joined,
        ["industry", "size"],
        minimum_cross_section=MINIMUM_CROSS_SECTION,
    )
    gate_ic = context.p3._gate_ic(
        raw_ic,
        industry_ic,
        size_ic,
        combined_ic,
        candidate.family["neutralization"],
    )
    block = context.p3._ic_block(gate_ic, WINDOW_START, END, sign)
    trial = {
        "parameter": parameter,
        "coverage": coverage,
        "full": block["full"],
        "train": block["train"],
        "test": block["test"],
        "recent": block["recent"],
        "gate_neutral": block,
        "industry_neutral": context.p3._ic_block(industry_ic, WINDOW_START, END, sign),
        "size_neutral": context.p3._ic_block(size_ic, WINDOW_START, END, sign),
        "industry_size_neutral": context.p3._ic_block(
            combined_ic, WINDOW_START, END, sign
        ),
        "raw_ic": raw_ic,
        "gate_ic": gate_ic,
    }
    return trial, gate_ic


def _family_trials(
    candidate: Candidate, context: _Context, membership: pl.DataFrame
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ics: dict[str, dict[str, Any]] = {}
    for parameter in candidate.family["parameter_grid"]:
        trial, gate_ic = _trial_stats(candidate, parameter, context, membership)
        trial["variant_id"] = f"{candidate.family_id}__p_{parameter}"
        trial["is_current"] = math.isclose(
            float(parameter), float(candidate.family["current_parameter"]), abs_tol=1e-12
        )
        rows.append(trial)
        ics[str(parameter)] = gate_ic
    context.p3.annotate_plateau(rows)
    return {str(row["parameter"]): row for row in rows}


def _seed(candidate: Candidate, block: int) -> int:
    token = hashlib.sha256(
        f"{SEED}:{candidate.family_id}:{candidate.parameter}:{block}:{FINAL_MC}".encode()
    ).hexdigest()
    return int(token[:8], 16)


def _candidate_result(
    candidate: Candidate,
    trial: dict[str, Any],
    gate_ic: pl.DataFrame,
    context: _Context,
) -> dict[str, Any]:
    sign = -1 if candidate.family["direction"] == "low" else 1
    final = {
        str(block): context.p3._bootstrap_ic_or_empty(
            gate_ic,
            WINDOW_START,
            END,
            sign,
            block_size=block,
            n_bootstrap=FINAL_MC,
            seed=_seed(candidate, block),
        )
        for block in BLOCK_SIZES
    }
    coverage = trial["coverage"]
    full = trial["full"]
    train = trial["train"]
    test = trial["test"]
    recent = trial["recent"]
    prior_robust = bool(
        candidate.source_recommended_trial.get("robust_plateau", False)
    )
    ci_positive = all(
        item.get("ci_low") is not None and item["ci_low"] > 0 for item in final.values()
    )
    gates = {
        "coverage_ge_0_90": isinstance(coverage.get("factor_non_null_ratio"), (int, float))
        and coverage["factor_non_null_ratio"] >= 0.90,
        "full_ic_ge_0_01": isinstance(full.get("directional_ic"), (int, float))
        and full["directional_ic"] >= 0.01,
        "train_ic_ge_0_01": isinstance(train.get("directional_ic"), (int, float))
        and train["directional_ic"] >= 0.01,
        "validation_ic_positive": isinstance(test.get("directional_ic"), (int, float))
        and test["directional_ic"] > 0,
        "recent_ic_positive": isinstance(recent.get("directional_ic"), (int, float))
        and recent["directional_ic"] > 0,
        "bootstrap_10_20_40_ci_positive": ci_positive,
        "plateau_positive": bool(trial.get("robust_plateau")),
        "prior_plateau_positive": prior_robust,
    }
    full_positive = gates["full_ic_ge_0_01"]
    validation_positive = gates["validation_ic_positive"]
    if not full_positive or not validation_positive or not gates["train_ic_ge_0_01"]:
        conclusion = "rejected"
    elif all(gates.values()) and trial.get("q_value") is not None and trial["q_value"] <= FDR_THRESHOLD:
        conclusion = "admitted_for_research"
    elif trial.get("full", {}).get("n_dates", 0) < MINIMUM_CROSS_SECTION:
        conclusion = "inconclusive"
    else:
        conclusion = "rejected"
    return {
        "source_tier": candidate.source_tier,
        "source_result": candidate.source_result,
        "source_result_sha256": candidate.source_result_sha256,
        "family_id": candidate.family_id,
        "label": candidate.family["label"],
        "data_class": candidate.family["data_class"],
        "kind": _candidate_kind(candidate),
        "parameter": candidate.parameter,
        "source_recommended_parameter": candidate.source_recommended_parameter,
        "coverage": coverage,
        "full": full,
        "train": train,
        "test": test,
        "recent": recent,
        "industry_neutral": trial["industry_neutral"],
        "size_neutral": trial["size_neutral"],
        "industry_size_neutral": trial["industry_size_neutral"],
        "plateau_after_pit": trial.get("plateau_status"),
        "prior_plateau": candidate.source_recommended_trial.get("plateau_status"),
        "gate_results": gates,
        "final_bootstrap": final,
        "q_value": trial.get("q_value"),
        "conclusion": conclusion,
        "rejection_reasons": [key for key, value in gates.items() if not value],
    }


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = load_recommended_candidates(root)
    membership = load_pit_membership(root)
    census = library_census(root)
    context = _load_context(root, membership)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        family_trials = _family_trials(candidate, context, membership)
        parameter_key = str(candidate.parameter)
        if parameter_key not in family_trials:
            # Numeric formatting for thresholds can differ from Python's str.
            parameter_key = next(
                key for key, row in family_trials.items()
                if math.isclose(float(row["parameter"]), float(candidate.parameter), abs_tol=1e-12)
            )
        trial = family_trials[parameter_key]
        # Recompute the selected gate series for final 5000-run MC.
        _, gate_ic = _trial_stats(candidate, candidate.parameter, context, membership)
        results.append(_candidate_result(candidate, trial, gate_ic, context))
    # Multiple testing across the 15 frozen finalists.
    context.p3.assign_family_q_values(results)
    for row in results:
        gates = row["gate_results"]
        gates["fdr_q_le_0_10"] = row.get("q_value") is not None and row["q_value"] <= FDR_THRESHOLD
        row["rejection_reasons"] = [key for key, value in gates.items() if not value]
        if not gates["full_ic_ge_0_01"] or not gates["validation_ic_positive"] or not gates["train_ic_ge_0_01"]:
            row["conclusion"] = "rejected"
        elif all(gates.values()):
            row["conclusion"] = "admitted_for_research"
        else:
            row["conclusion"] = "rejected"
    counts = Counter(row["conclusion"] for row in results)
    data_class_capability: dict[str, dict[str, int]] = {}
    for row in results:
        bucket = data_class_capability.setdefault(
            row["data_class"], {"candidates": 0, "admitted": 0, "rejected": 0}
        )
        bucket["candidates"] += 1
        bucket[row["conclusion"]] = bucket.get(row["conclusion"], 0) + 1
    payload = {
        "schema_version": 1,
        "research_id": "R530",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "library_census": census,
        "candidates": results,
        "conclusion": (
            "admitted_for_research"
            if counts["admitted_for_research"]
            else "rejected"
            if counts["rejected"]
            else "inconclusive"
        ),
        "conclusion_counts": dict(sorted(counts.items())),
        "data_class_capability": dict(sorted(data_class_capability.items())),
        "gates": {
            "frozen_candidate_count": 15,
            "frozen_candidate_source": [path.relative_to(root).as_posix() for _, path in SOURCE_RESULTS],
            "membership": "r526_unified_daily_hs300",
            "industry": "r526_industry_history",
            "horizon_days": HORIZON,
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "initial_mc": INITIAL_MC,
            "final_mc": FINAL_MC,
            "block_sizes": list(BLOCK_SIZES),
            "fdr_threshold": FDR_THRESHOLD,
        },
        "known_limits": [
            "Frozen historical recommendations are revalidated, not newly discovered.",
            "The R526 membership and industry vintages are reconstructed research inputs.",
            "Statistical admission is not portfolio performance evidence.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload, census


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R530 统一 PIT 因子库蒙特卡洛复核",
        "",
        "## 因子库规模",
        "",
        f"- 因子：{payload['library_census']['factor_count']}",
        f"- 参数族：{payload['library_census']['parameter_family_count']}",
        f"- 历史参数点：{payload['library_census']['parameter_trial_count']}",
        f"- 冻结候选：{payload['gates']['frozen_candidate_count']}",
        "",
        "## 候选结论",
        "",
        "| 家族 | 参数 | 数据类 | full IC | validation IC | recent IC | q | bootstrap 10/20/40 | 结论 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload["candidates"]:
        b = row["final_bootstrap"]
        ci = "/".join(
            "n/a" if b[str(block)].get("ci_low") is None else f"{b[str(block)]['ci_low']:.4f}"
            for block in BLOCK_SIZES
        )
        lines.append(
            f"| {row['family_id']} | {row['parameter']} | {row['data_class']} | "
            f"{row['full']['directional_ic']} | {row['test']['directional_ic']} | "
            f"{row['recent']['directional_ic']} | {row['q_value']} | {ci} | {row['conclusion']} |"
        )
    lines += [
        "",
        "## 维度能力",
        "",
        "| 数据类 | 候选 | 研究准入 | 拒绝 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, bucket in payload["data_class_capability"].items():
        lines.append(
            f"| {name} | {bucket['candidates']} | "
            f"{bucket.get('admitted_for_research', 0)} | {bucket['rejected']} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 结论计数：{payload['conclusion_counts']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    return "\n".join(lines) + "\n"


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R530 preregistration already exists: {PREREG}")
    candidates = load_recommended_candidates(root)
    census = library_census(root)
    bundle_manifest = RESEARCH / "research" / "r526_unified_pit_bundle_manifest_2026-11-23.json"
    if not bundle_manifest.exists():
        raise FileNotFoundError(bundle_manifest)
    input_hashes = {
        "catalog_snapshot": sha256(RESEARCH / "catalog" / "latest.json"),
        "source_results": sha256_json(
            {tier: sha256(path) for tier, path in SOURCE_RESULTS}
        ),
        "r526_manifest": sha256(bundle_manifest),
        "r526_membership": sha256(PIT_BUNDLE / "daily_snapshots.parquet"),
        "r526_industry": sha256(PIT_BUNDLE / "industry_history.parquet"),
        "r526_alt_fields": sha256(PIT_BUNDLE / "alt_hs300_required_fields.parquet"),
    }
    payload = {
        "schema_version": 1,
        "research_id": "R530",
        "research_type": RESEARCH_TYPE,
        "question": "Do the library's frozen parameter recommendations survive a common PIT membership and strict moving-block Monte Carlo?",
        "hypothesis": "A subset of prior recommendations survives; candidates relying on current membership or unstable parameters are rejected.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": input_hashes,
        "parameters": {
            "candidates": [
                {
                    "source_tier": c.source_tier,
                    "family_id": c.family_id,
                    "parameter": c.parameter,
                    "source_result": c.source_result,
                    "source_result_sha256": c.source_result_sha256,
                }
                for c in candidates
            ],
            "final_mc": FINAL_MC,
            "block_sizes": list(BLOCK_SIZES),
            "seed": SEED,
        },
        "sample": {
            "start": str(WINDOW_START),
            "end": str(END),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
            "horizon_days": HORIZON,
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "factor_count": census["factor_count"],
            "parameter_family_count": census["parameter_family_count"],
            "parameter_trial_count": census["parameter_trial_count"],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "coverage_min": 0.90,
            "full_ic_min": 0.01,
            "train_ic_min": 0.01,
            "validation_ic_positive": True,
            "recent_ic_positive": True,
            "fdr_q_max": FDR_THRESHOLD,
            "bootstrap_blocks": list(BLOCK_SIZES),
            "bootstrap_ci_low_positive": True,
            "plateau_positive": True,
        },
        "falsification_checks": [
            "frozen_parameter_neighbors",
            "fixed_seed_block_bootstrap_10_20_40",
            "benjamini_hochberg_fdr_across_15_finalists",
            "unified_pit_membership_filter",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PREREG.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    PREREG.with_name(PREREG.name + ".sha256").write_text(
        f"{sha256(PREREG)}  {PREREG.name}\n", encoding="ascii"
    )
    return PREREG


def publish_result(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    root = Path(root)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError(f"R530 result already exists: {RESULT}")
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": prereg["research_id"],
        "research_type": RESEARCH_TYPE,
        "runner": prereg["runner"],
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["gates"],
        "metrics": {
            "library_census": payload["library_census"],
            "data_class_capability": payload["data_class_capability"],
            "conclusion_counts": payload["conclusion_counts"],
            "candidates": payload["candidates"],
        },
        "source_result_hashes": sorted({row["source_result_sha256"] for row in payload["candidates"]}),
        "known_limits": payload["known_limits"],
        "negative_knowledge": [
            row["family_id"] for row in payload["candidates"] if row["conclusion"] == "rejected"
        ],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r530_unified_factor_library_mc_published",
    ).__dict__


def sha256_json(value: Any) -> str:
    from app.factor_research.fingerprints import sha256_json as _sha256_json

    return _sha256_json(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--freeze-preregistration", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        candidates = load_recommended_candidates(ROOT)
        membership = load_pit_membership(ROOT)
        print(json.dumps({
            "candidate_count": len(candidates),
            "membership_rows": membership.height,
            "census": library_census(ROOT),
        }, ensure_ascii=False, indent=2))
        return 0
    if not PREREG.exists():
        raise FileNotFoundError("R530 preregistration is missing; run --freeze-preregistration")
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R530 results already exist; refusing overwrite")
    candidates = load_recommended_candidates(ROOT)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen = prereg["parameters"]["candidates"]
    if len(frozen) != len(candidates):
        raise RuntimeError("R530 candidate set changed after preregistration")
    payload, _census = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "counts": payload["conclusion_counts"],
        "published_result": str(published.get("result_path")),
        "published_markdown": str(published.get("markdown_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
