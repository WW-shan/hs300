#!/usr/bin/env python3
"""P5 incremental screening of P1-P4 parameter-stable candidates."""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import math
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
import polars as pl

from app.backtest import stats_v2
from app.backtest.strategy import PreparedMatrixBacktest, StrategyBacktestService
from app.factor_research.automation import assert_portfolio_candidates_admitted
from app.factor_research.catalog import build_catalog, save_catalog
from app.factor_research.extended_parameter_data import (
    load_event_panels,
    load_existing_ext_factors,
)
from app.factor_research.extended_parameter_evaluators import EXTENDED_EVALUATORS
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.incremental import (
    build_rank_composite,
    marginal_ic,
    pairwise_cross_sectional_correlation,
    portfolio_gate,
    rank_score_frame,
)
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.sets import FactorSetStore
from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.factors.ext_factors import ensure_synced
from app.factors.store import to_spec
from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load_sibling(name: str):
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p5_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sibling research module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _normalize_output_path(path: Path) -> Path:
    """Resolve relative CLI output before ledger path projection."""
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


portfolio = _load_sibling("research_factor_set_portfolio")
p3_scan = _load_sibling("research_extended_parameter_families")

DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "p5_incremental_2026-11-11.json"
P1_RESULT = DATA / "factor_research" / "research" / "parameters" / "p1_parameter_mc_2026-11-11.json"
P2_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p2_builtin_parameter_mc_2026-11-11_v2.json"
)
P3_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p3_extended_parameter_mc_2026-11-11_v3.json"
)
P4_RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "parameters"
    / "p4_threshold_parameter_mc_2026-11-11.json"
)
START = date(2021, 6, 1)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
WEAK_START = date(2025, 7, 1)
WEAK_END = date(2026, 6, 30)
LOAD_START = date(2019, 1, 1)
HORIZON = 5
MINIMUM_CROSS_SECTION = 30
FDR_THRESHOLD = 0.10
MAX_CORRELATION = 0.90
METHOD_VERSION = "factor_increment_p5_v1"
OPERATION = "factor_increment_scan_published"

RESEARCH_PATHS = {
    "p1": P1_RESULT,
    "p2": P2_RESULT,
    "p3": P3_RESULT,
    "p4": P4_RESULT,
}
CUSTOM_FACTOR_IDS = (
    "uf_p5_annual_vol10_low",
    "uf_p5_atr5_low",
    "uf_p5_boll_width10_low",
    "uf_p5_amihud120",
    "uf_p5_amount5_low",
    "uf_p5_turnover5_low",
    "uf_p5_max_ret60_low",
    "uf_p5_div_yield_abs015",
    "uf_p5_margin_q50",
)
SYNTHETIC_REPURCHASE_FACTOR = "p5_research_repurchase_decay_20"
CUSTOM_FACTOR_DIR = Path("factor_research") / "candidates" / "p5"
RESEARCH_EXT_DEPENDENCY_RE = re.compile(r"\b(ext_alt_hs300_[a-z0-9_]+)\b")


@dataclass(frozen=True)
class CandidateSpec:
    candidate_key: str
    economic_key: str
    label: str
    source_research: Literal["p1", "p2", "p3", "p4"]
    family_id: str
    parameter: float
    factor_id: str
    direction: Literal["high", "low"]
    registered: bool
    transform: str
    source_factor_id: str | None = None
    dedupe_priority: int = 0


CANDIDATE_SPECS = (
    CandidateSpec(
        "p1_idio_vol_p20",
        "idio_vol",
        "残差波动20日",
        "p1",
        "idio_vol",
        20,
        "ext_alt_hs300_idio_vol_20d_low",
        "high",
        True,
        "window",
        "ext_alt_hs300_idio_vol_20d_low",
    ),
    CandidateSpec(
        "p1_cs_spread_p60",
        "cs_spread",
        "Corwin-Schultz价差60日",
        "p1",
        "cs_spread",
        60,
        "ext_alt_hs300_cs_spread_60d",
        "low",
        True,
        "window",
        "ext_alt_hs300_cs_spread_60d",
    ),
    CandidateSpec(
        "p1_vol_of_vol_p20",
        "vol_of_vol",
        "波动率稳定性20日",
        "p1",
        "vol_of_vol",
        20,
        "ext_alt_hs300_vol_of_vol_20d_low",
        "high",
        True,
        "window",
        "ext_alt_hs300_vol_of_vol_20d_low",
    ),
    CandidateSpec(
        "p2_annual_vol_p10",
        "annual_volatility",
        "年化波动10日",
        "p2",
        "annual_volatility",
        10,
        "uf_p5_annual_vol10_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_atr_p5",
        "atr_risk",
        "ATR占价格5日",
        "p2",
        "atr_risk",
        5,
        "uf_p5_atr5_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_boll_width_p10",
        "bollinger_width",
        "布林带宽10日",
        "p2",
        "bollinger_width",
        10,
        "uf_p5_boll_width10_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_amihud_p120",
        "amihud",
        "Amihud非流动性120日",
        "p2",
        "amihud",
        120,
        "uf_p5_amihud120",
        "high",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_amount_p5",
        "amount_level",
        "成交额水平5日",
        "p2",
        "amount_level",
        5,
        "uf_p5_amount5_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_turnover_p5",
        "turnover_level",
        "换手率水平5日",
        "p2",
        "turnover_level",
        5,
        "uf_p5_turnover5_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p2_max_return_p60",
        "max_return",
        "最大单日涨幅60日",
        "p2",
        "max_return",
        60,
        "uf_p5_max_ret60_low",
        "low",
        True,
        "window",
    ),
    CandidateSpec(
        "p3_repurchase_decay_p20",
        "repurchase_event",
        "回购事件线性衰减20日",
        "p3",
        "repurchase_event_decay",
        20,
        SYNTHETIC_REPURCHASE_FACTOR,
        "high",
        False,
        "event_decay",
        "ext_alt_hs300_repurchase_event_60d",
        dedupe_priority=2,
    ),
    CandidateSpec(
        "p4_dividend_abs_0015",
        "dividend_yield",
        "股息率绝对阈值1.5%",
        "p4",
        "dividend_yield_absolute",
        0.015,
        "uf_p5_div_yield_abs015",
        "high",
        True,
        "absolute_threshold",
        "ext_alt_hs300_dividend_yield",
        dedupe_priority=2,
    ),
    CandidateSpec(
        "p4_dividend_q040",
        "dividend_yield",
        "股息率分位阈值40%",
        "p4",
        "dividend_yield_quantile",
        0.40,
        "p5_research_dividend_quantile_040",
        "high",
        False,
        "quantile_threshold",
        "ext_alt_hs300_dividend_yield",
        dedupe_priority=1,
    ),
    CandidateSpec(
        "p4_margin_q050",
        "margin_change",
        "融资变化中位阈值",
        "p4",
        "margin_change_quantile",
        0.50,
        "uf_p5_margin_q50",
        "high",
        True,
        "quantile_threshold",
        "ext_alt_hs300_margin_chg_1m_low",
        dedupe_priority=2,
    ),
    CandidateSpec(
        "p4_repurchase_holding_p20",
        "repurchase_event",
        "回购事件常值持有20日",
        "p4",
        "repurchase_holding",
        20,
        "p5_research_repurchase_holding_20",
        "high",
        False,
        "event_holding",
        "ext_alt_hs300_repurchase_event_60d",
        dedupe_priority=1,
    ),
)


def _load_payloads() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in RESEARCH_PATHS.items()
    }


def _selection_stats(result: dict[str, Any]) -> dict[str, float | None]:
    trial = result.get("recommended_trial")
    if trial is None:
        raise ValueError("candidate result has no recommended trial")
    train = trial.get("train", {}).get("directional_ic")
    test = trial.get("test", {}).get("directional_ic")
    final_ci = (
        trial.get("final_bootstrap", {}).get("20", {}).get("ci_low")
        if trial.get("final_bootstrap")
        else None
    )
    return {
        "train_ic": float(train) if isinstance(train, (int, float)) else None,
        "test_ic": float(test) if isinstance(test, (int, float)) else None,
        "min_train_test_ic": (
            float(min(train, test))
            if isinstance(train, (int, float)) and isinstance(test, (int, float))
            else None
        ),
        "final_ci_low": float(final_ci) if isinstance(final_ci, (int, float)) else None,
    }


def _candidate_evidence(
    spec: CandidateSpec,
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = payloads[spec.source_research]
    result = payload.get("families", {}).get(spec.family_id)
    if result is None:
        raise ValueError(f"missing research family: {spec.family_id}")
    recommended = result.get("recommended_parameter")
    if result.get("recommendation_status") != "recommended" or not isinstance(
        recommended, (int, float)
    ):
        raise ValueError(f"family is not parameter-stable: {spec.family_id}")
    if not math.isclose(float(recommended), float(spec.parameter), abs_tol=1e-12):
        raise ValueError(
            f"recommended parameter drift for {spec.family_id}: "
            f"{recommended} != {spec.parameter}"
        )
    return {
        "candidate_key": spec.candidate_key,
        "economic_key": spec.economic_key,
        "label": spec.label,
        "source_research": spec.source_research,
        "research_id": payload["research_id"],
        "family_id": spec.family_id,
        "variant_id": result.get("recommended_variant_id"),
        "parameter": spec.parameter,
        "transform": spec.transform,
        "factor_id": spec.factor_id,
        "source_factor_id": spec.source_factor_id,
        "direction": spec.direction,
        "registered": spec.registered,
        "dedupe_priority": spec.dedupe_priority,
        "selection": _selection_stats(result),
    }


def collect_candidates(
    payloads: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate recommendations and keep one representative per economic meaning."""
    evidence = [_candidate_evidence(spec, payloads) for spec in CANDIDATE_SPECS]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        grouped.setdefault(item["economic_key"], []).append(item)
    selected: list[dict[str, Any]] = []
    dedupe: list[dict[str, Any]] = []
    for economic_key, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: (
                item["selection"]["min_train_test_ic"]
                if item["selection"]["min_train_test_ic"] is not None
                else -999.0,
                item["selection"]["final_ci_low"]
                if item["selection"]["final_ci_low"] is not None
                else -999.0,
                item["dedupe_priority"],
                item["candidate_key"],
            ),
            reverse=True,
        )
        chosen = dict(ranked[0])
        chosen["dedupe_alternatives"] = [
            {
                "candidate_key": item["candidate_key"],
                "factor_id": item["factor_id"],
                "reason": "lower_pre_registered_selection_score",
                "selection": item["selection"],
            }
            for item in ranked[1:]
        ]
        selected.append(chosen)
        for item in ranked[1:]:
            dedupe.append(
                {
                    "economic_key": economic_key,
                    "kept": chosen["candidate_key"],
                    "dropped": item["candidate_key"],
                    "reason": "same_economic_meaning_lower_selection_score",
                    "kept_selection": chosen["selection"],
                    "dropped_selection": item["selection"],
                }
            )
    selected.sort(key=lambda item: item["candidate_key"])
    dedupe.sort(key=lambda item: item["dropped"])
    return selected, dedupe


def _custom_factor_path(data_dir: Path, factor_id: str) -> Path:
    return data_dir / CUSTOM_FACTOR_DIR / f"{factor_id}.json"


@contextmanager
def _temporary_research_dependencies(formula_texts: list[str]) -> Iterator[None]:
    """Register missing extension columns only for DSL compilation, then remove them."""
    from app.factors.registry import (
        FactorSpec,
        get_factor,
        register_factor,
        unregister_factor,
    )

    registered: list[str] = []
    try:
        for formula_text in formula_texts:
            for factor_id in sorted(set(RESEARCH_EXT_DEPENDENCY_RE.findall(formula_text))):
                if get_factor(factor_id) is not None:
                    continue
                register_factor(
                    FactorSpec(
                        id=factor_id,
                        label=f"P5 research dependency {factor_id}",
                        group="P5参数研究依赖",
                        formula_text="extension column",
                        kind="base",
                        scale_free=False,
                    )
                )
                registered.append(factor_id)
        yield
    finally:
        for factor_id in reversed(registered):
            unregister_factor(factor_id)


def _load_custom_definitions(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Load P5 draft definitions without inserting them into the global registry."""
    ensure_synced(data_dir)
    from app.factors.registry import get_factor

    raw_definitions = {}
    for factor_id in CUSTOM_FACTOR_IDS:
        path = _custom_factor_path(data_dir, factor_id)
        if not path.exists():
            raise FileNotFoundError(path)
        raw_definitions[factor_id] = json.loads(path.read_text(encoding="utf-8"))

    definitions: dict[str, dict[str, Any]] = {}
    with _temporary_research_dependencies(
        [str(definition.get("formula", "")) for definition in raw_definitions.values()]
    ):
        for factor_id, definition in raw_definitions.items():
            path = _custom_factor_path(data_dir, factor_id)
            if definition.get("id") != factor_id or definition.get("status") != "draft":
                raise ValueError(f"P5 custom factor must be draft with matching id: {path}")
            if get_factor(factor_id) is not None:
                raise ValueError(f"P5 research factor must not be globally registered: {factor_id}")
            spec = to_spec(definition)
            compiled = compile_formula(spec.formula_text)
            if not compiled.ok or compiled.frame_transform is None:
                raise ValueError(f"invalid P5 custom factor formula {factor_id}: {compiled.errors}")
            definitions[factor_id] = definition
    return definitions


def _validate_custom_factors(data_dir: Path) -> dict[str, str]:
    definitions = _load_custom_definitions(data_dir)
    return {
        factor_id: sha256_file(_custom_factor_path(data_dir, factor_id))
        for factor_id in definitions
    }


def _materialize_research_factors(
    frame: pl.DataFrame,
    factor_ids: list[str],
    definitions: dict[str, dict[str, Any]],
) -> pl.DataFrame:
    """Materialize draft research factors directly, preserving registry isolation."""
    unknown = sorted(set(factor_ids) - set(definitions))
    if unknown:
        raise ValueError(f"P5 research factor definitions missing: {unknown}")
    for factor_id in factor_ids:
        if factor_id in frame.columns:
            raise ValueError(f"P5 research factor column already exists: {factor_id}")
        formula_text = str(definitions[factor_id].get("formula", ""))
        with _temporary_research_dependencies([formula_text]):
            spec = to_spec(definitions[factor_id])
            compiled = compile_formula(spec.formula_text)
            if not compiled.ok or compiled.frame_transform is None:
                raise ValueError(
                    f"invalid P5 custom factor formula {factor_id}: {compiled.errors}"
                )
            transformed = compiled.frame_transform(frame)
            if transformed is None:
                raise ValueError(f"P5 research factor dependencies are missing: {factor_id}")
            frame = transformed.with_columns(
                pl.col(FACTOR_COLUMN).alias(factor_id)
            ).drop(FACTOR_COLUMN)
    return frame


def _load_price_panel(data_dir: Path, symbols: list[str]) -> pl.DataFrame:
    paths = sorted((data_dir / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(data_dir / "kline_daily_enriched")
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
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
            ]
        )
        .collect()
        .sort(["symbol", "date"])
    )
    prices = prices.with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        (
            pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0
        ).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias("log_float_mv"),
    )
    industry = (
        load_industry_l1(data_dir)
        .select(["symbol", "industry_l1"])
        .unique(subset=["symbol"], keep="last")
    )
    return prices.join(industry, on="symbol", how="left")


def load_candidate_panel(
    data_dir: Path,
    symbols: list[str],
    candidates: list[dict[str, Any]],
) -> tuple[pl.DataFrame, dict[str, str]]:
    panel = _load_price_panel(data_dir, symbols)
    ext_ids = {
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_holder_top10_share_chg_yoy",
        "ext_alt_hs300_idio_vol_40d_low",
        "ext_alt_hs300_margin_chg_1m_low",
        "ext_alt_hs300_ocf_margin_4q",
        "ext_alt_hs300_vol_of_vol_20d_low",
        "ext_alt_hs300_idio_vol_20d_low",
        "ext_alt_hs300_cs_spread_60d",
    }
    ext_ids.update(
        candidate["source_factor_id"]
        for candidate in candidates
        if str(candidate.get("source_factor_id") or "").startswith("ext_alt_hs300_")
    )
    ext = load_existing_ext_factors(
        data_dir,
        {
            factor_id: factor_id.removeprefix("ext_alt_hs300_")
            for factor_id in sorted(ext_ids)
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left")
    stock_custom_ids = [
        candidate["factor_id"]
        for candidate in candidates
        if candidate["registered"] and candidate["factor_id"] in CUSTOM_FACTOR_IDS
    ]
    panel = _materialize_research_factors(
        panel,
        stock_custom_ids,
        _load_custom_definitions(data_dir),
    )

    synthetic = [
        candidate for candidate in candidates
        if candidate["factor_id"] == SYNTHETIC_REPURCHASE_FACTOR
    ]
    if synthetic:
        events = load_event_panels(data_dir, symbols)
        context = {
            "calendar": panel.select(["symbol", "date"]),
            "events": events,
        }
        raw, column, _ = EXTENDED_EVALUATORS["repurchase_event_decay"](context, 20)
        panel = panel.join(
            raw.select(
                ["symbol", "date", pl.col(column).cast(pl.Float64).alias(SYNTHETIC_REPURCHASE_FACTOR)]
            ),
            on=["symbol", "date"],
            how="left",
        )

    factor_ids = [candidate["factor_id"] for candidate in candidates]
    missing = sorted(set(factor_ids) - set(panel.columns))
    if missing:
        raise ValueError(f"candidate factors were not materialized: {missing}")
    coverage = {}
    for factor_id in factor_ids:
        coverage[factor_id] = {
            "rows": panel.height,
            "non_null_rows": int(panel[factor_id].is_not_null().sum()),
            "non_null_ratio": round(
                float(panel[factor_id].is_not_null().sum()) / panel.height, 6
            )
            if panel.height
            else 0.0,
        }
    return panel, coverage


def _current_set(data_dir: Path):
    sets = FactorSetStore(data_dir)
    head = sets.head("multi_dimension_v1")
    if head is None:
        raise RuntimeError("multi_dimension_v1 head is unavailable")
    if head.active_version is not None:
        raise ValueError("P5 requires active_version=null")
    version = sets.load_version("multi_dimension_v1", 1)
    return sets, head, version


def _current_inputs(version: Any) -> tuple[dict[str, float], dict[str, str], list[str]]:
    members = [
        member
        for member in version.members
        if member.role == "alpha" and member.membership_status in {"watch", "active"}
    ]
    if not members:
        raise ValueError("current factor set has no alpha members")
    weights = {member.factor_id: float(member.weight or 0.0) for member in members}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("current factor-set weights are invalid")
    weights = {factor_id: weight / total for factor_id, weight in weights.items()}
    directions = {member.factor_id: member.direction for member in members}
    return weights, directions, sorted(weights)


CURRENT_ECONOMIC_KEYS = {
    "ext_alt_hs300_idio_vol_40d_low": "idio_vol",
    "ext_alt_hs300_dividend_yield": "dividend_yield",
    "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol",
    "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability",
    "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio",
    "ext_alt_hs300_ocf_margin_4q": "ocf_margin",
    "ext_alt_hs300_margin_chg_1m_low": "margin_change",
    "ext_alt_hs300_holder_top10_share_chg_yoy": "holder_top10_share_change",
}


def annotate_candidate_roles(
    candidates: list[dict[str, Any]],
    current_factor_ids: list[str],
) -> None:
    current_by_economic = {
        CURRENT_ECONOMIC_KEYS[factor_id]: factor_id
        for factor_id in current_factor_ids
        if factor_id in CURRENT_ECONOMIC_KEYS
    }
    for candidate in candidates:
        existing = current_by_economic.get(candidate["economic_key"])
        candidate["replaces_factor_id"] = existing
        if existing is None:
            candidate["set_role"] = "new"
        elif existing == candidate["factor_id"]:
            candidate["set_role"] = "already_in_set"
        else:
            candidate["set_role"] = "replacement"


def _current_composite(
    panel: pl.DataFrame,
    current_weights: dict[str, float],
    current_directions: dict[str, str],
    *,
    exclude: str | None = None,
) -> tuple[pl.DataFrame, dict[str, str], str]:
    weights = {
        factor_id: weight
        for factor_id, weight in current_weights.items()
        if factor_id != exclude
    }
    total = sum(weights.values())
    weights = {factor_id: weight / total for factor_id, weight in weights.items()}
    directions = {
        factor_id: current_directions[factor_id] for factor_id in weights
    }
    column = "_current_composite" if exclude is None else f"_current_composite_ex_{exclude}"
    scored, scores = build_rank_composite(
        panel,
        weights,
        directions,
        column,
    )
    return scored, scores, column


def score_candidates(
    panel: pl.DataFrame,
    candidates: list[dict[str, Any]],
    current_weights: dict[str, float],
    current_directions: dict[str, str],
) -> tuple[pl.DataFrame, dict[str, dict[str, Any]]]:
    scored, current_scores, current_column = _current_composite(
        panel, current_weights, current_directions
    )
    tested = [item for item in candidates if item["set_role"] != "already_in_set"]
    for candidate in tested:
        score_column = f"_score_{candidate['factor_id']}"
        scored = rank_score_frame(
            scored,
            candidate["factor_id"],
            candidate["direction"],
            output_column=score_column,
        )
        candidate["score_column"] = score_column

    all_scores = {**current_scores}
    for candidate in tested:
        all_scores[candidate["factor_id"]] = candidate["score_column"]
    _, correlations = pairwise_cross_sectional_correlation(
        scored,
        all_scores,
        minimum_symbols=MINIMUM_CROSS_SECTION,
    )

    replacement_frames: dict[str, tuple[pl.DataFrame, str]] = {}
    results: dict[str, dict[str, Any]] = {}
    for candidate in tested:
        excluded = candidate.get("replaces_factor_id")
        if candidate["set_role"] == "replacement" and excluded:
            if excluded not in replacement_frames:
                replaced, _, replaced_column = _current_composite(
                    scored,
                    current_weights,
                    current_directions,
                    exclude=excluded,
                )
                scored = replaced
                replacement_frames[excluded] = (scored, replaced_column)
            _, base_column = replacement_frames[excluded]
        else:
            base_column = current_column

        related = []
        for current_factor_id in current_weights:
            key = (
                f"{candidate['factor_id']}|{current_factor_id}"
                if candidate["factor_id"] < current_factor_id
                else f"{current_factor_id}|{candidate['factor_id']}"
            )
            summary = correlations.get(key)
            if summary is not None:
                related.append(
                    {
                        "current_factor_id": current_factor_id,
                        "is_replaced_slot": current_factor_id == excluded,
                        **summary,
                    }
                )
        gate_correlations = [
            abs(float(item["mean_abs"] or 0.0))
            for item in related
            if not item["is_replaced_slot"]
        ]
        info = marginal_ic(
            scored,
            base_column,
            candidate["score_column"],
            "forward_return_5d",
            recent_start=RECENT_START,
            minimum_symbols=MINIMUM_CROSS_SECTION,
            horizon=HORIZON,
        )
        results[candidate["candidate_key"]] = {
            "comparison_basis": (
                f"current_composite_without_{excluded}"
                if excluded
                else "current_composite"
            ),
            **info,
            "correlations": related,
            "max_abs_correlation_all_members": round(
                max((abs(float(item["mean_abs"] or 0.0)) for item in related), default=0.0),
                6,
            ),
            "max_abs_correlation_for_gate": round(
                max(gate_correlations, default=0.0), 6
            ),
        }

    p_values = [results[item["candidate_key"]]["delta_p_value"] for item in tested]
    q_values = stats_v2.bh_fdr_qvalues(p_values)
    for candidate, q_value in zip(tested, q_values, strict=True):
        result = results[candidate["candidate_key"]]
        result["delta_q_value"] = (
            round(float(q_value), 8) if q_value is not None else None
        )
    return scored, results


def evaluate_statistical_gate(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    delta = result.get("directional_delta_mean")
    p_value = result.get("delta_p_value")
    q_value = result.get("delta_q_value")
    if not isinstance(delta, (int, float)) or delta <= 0.0:
        reasons.append("marginal_ic_not_positive")
    if not isinstance(p_value, (int, float)) or p_value > FDR_THRESHOLD:
        reasons.append("marginal_ic_p_above_0_10")
    if not isinstance(q_value, (int, float)) or q_value > FDR_THRESHOLD:
        reasons.append("marginal_ic_q_above_0_10")
    if result.get("max_abs_correlation_for_gate", 1.0) > MAX_CORRELATION:
        reasons.append("correlation_above_0_90")
    if result.get("n_dates", 0) < 100:
        reasons.append("insufficient_marginal_ic_dates")
    if candidate["set_role"] == "already_in_set":
        reasons.append("already_in_current_set")
    if not candidate["registered"]:
        reasons.append("registration_required")
    return {"pass": not reasons, "reasons": reasons}


def _print_stats_preview(
    candidates: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> None:
    rows = []
    for candidate in candidates:
        if candidate["set_role"] == "already_in_set":
            continue
        result = results[candidate["candidate_key"]]
        gate = evaluate_statistical_gate(candidate, result)
        rows.append(
            {
                "candidate": candidate["candidate_key"],
                "role": candidate["set_role"],
                "delta_ic": result.get("directional_delta_mean"),
                "p": result.get("delta_p_value"),
                "q": result.get("delta_q_value"),
                "max_corr": result.get("max_abs_correlation_for_gate"),
                "pass": gate["pass"],
                "reasons": gate["reasons"],
            }
        )
    rows.sort(key=lambda row: row["delta_ic"] or -999.0, reverse=True)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def _market_dates(market: Any) -> list[date]:
    return [date.fromisoformat(str(label)[:10]) for label in market.timestamp_labels]


def _extend_prepared_with_panel(
    prepared: PreparedMatrixBacktest,
    panel: pl.DataFrame,
    factor_ids: list[str],
) -> PreparedMatrixBacktest:
    market = prepared.market_data
    dates = _market_dates(market)
    skeleton = pl.DataFrame({"date": dates}).join(
        pl.DataFrame({"symbol": list(market.symbols)}),
        how="cross",
    )
    available = [factor_id for factor_id in factor_ids if factor_id in panel.columns]
    missing = sorted(set(factor_ids) - set(available))
    if missing:
        raise ValueError(f"prepared matrix cannot attach missing factors: {missing}")
    values = (
        panel.filter(
            pl.col("date").is_in(dates)
            & pl.col("symbol").is_in(list(market.symbols))
        )
        .select(["symbol", "date", *available])
    )
    aligned = (
        skeleton.join(values, on=["symbol", "date"], how="left")
        .sort(["date", "symbol"])
    )
    fields = dict(market.fields)
    for factor_id in available:
        array = aligned[factor_id].cast(pl.Float32).to_numpy()
        array = array.reshape(len(dates), len(market.symbols)).copy()
        array[~np.isfinite(array)] = np.nan
        array.flags.writeable = False
        fields[factor_id] = array
    extended_market = dataclasses.replace(
        market,
        fields=MappingProxyType(fields),
    )
    return dataclasses.replace(prepared, market_data=extended_market)


def _prepare_shared_window(
    service: StrategyBacktestService,
    symbols: list[str],
    panel: pl.DataFrame,
    factor_ids: list[str],
    *,
    start: date,
    end: date,
) -> PreparedMatrixBacktest:
    seed = factor_ids[0]
    config = portfolio._backtest_config(
        {seed: 1.0},
        {seed: "high"},
        start=start,
        end=end,
        max_positions=portfolio.DEFAULT_POSITIONS,
        cost=portfolio._cost_parameters("2x"),
    )
    config.symbols = list(symbols)
    prepared = service.prepare_matrix_optimization([config])
    return _extend_prepared_with_panel(prepared, panel, factor_ids)


def _select_stats(stats: dict[str, Any]) -> dict[str, float | int | None]:
    keys = (
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "win_rate",
        "profit_factor",
        "n_trades",
        "avg_exposure",
        "worst",
    )
    return {
        key: round(float(stats[key]), 6)
        if isinstance(stats.get(key), (int, float))
        else None
        for key in keys
    }


def _run_prepared(
    service: StrategyBacktestService,
    prepared: PreparedMatrixBacktest,
    scoring: dict[str, float],
    directions: dict[str, str],
    *,
    start: date,
    end: date,
    cost_label: str,
    label: str,
) -> dict[str, Any]:
    config = portfolio._backtest_config(
        scoring,
        directions,
        start=start,
        end=end,
        max_positions=portfolio.DEFAULT_POSITIONS,
        cost=portfolio._cost_parameters(cost_label),
    )
    config.symbols = list(prepared.market_data.symbols)
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(f"{label} failed: {result.error}")
    record = {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "max_positions": portfolio.DEFAULT_POSITIONS,
        "error": None,
        "stats": _select_stats(result.stats),
        "trade_count": len(result.trades),
        "benchmark_return": portfolio._curve_return(result.benchmark_curve),
    }
    print(
        f"{label}: return={record['stats']['total_return']} "
        f"annual={record['stats']['annual_return']} "
        f"sharpe={record['stats']['sharpe']} "
        f"mdd={record['stats']['max_drawdown']}",
        flush=True,
    )
    return record


def _equal_inputs(factor_ids: list[str], directions: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    ordered = list(dict.fromkeys(factor_ids))
    if not 1 <= len(ordered) <= 8:
        raise ValueError(f"P5 factor set must have 1..8 members, got {len(ordered)}")
    weight = 1.0 / len(ordered)
    scoring = {factor_id: weight for factor_id in ordered}
    selected_directions = {factor_id: directions[factor_id] for factor_id in ordered}
    return scoring, selected_directions


def _gate_metrics(
    full_2x: dict[str, Any],
    recent_2x: dict[str, Any],
    full_3x: dict[str, Any],
) -> dict[str, dict[str, float | None]]:
    return {
        "full_2x": full_2x["stats"],
        "recent_2x": recent_2x["stats"],
        "full_3x": full_3x["stats"],
    }


def _variant_factor_set(
    candidate: dict[str, Any],
    current_ids: list[str],
    weakest_factor: str,
) -> list[str]:
    replaced = candidate.get("replaces_factor_id")
    if candidate["set_role"] == "replacement" and replaced in current_ids:
        return [candidate["factor_id"] if item == replaced else item for item in current_ids]
    remaining = [item for item in current_ids if item != weakest_factor]
    return [*remaining, candidate["factor_id"]]


def _loo_contributions(
    service: StrategyBacktestService,
    symbols: list[str],
    panel: pl.DataFrame,
    current_weights: dict[str, float],
    current_directions: dict[str, str],
    current_ids: list[str],
    *,
    prepared_full: PreparedMatrixBacktest,
    prepared_recent: PreparedMatrixBacktest,
    baseline_full: dict[str, Any],
    baseline_recent: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    del symbols, panel
    records: dict[str, dict[str, Any]] = {}
    for factor_id in current_ids:
        remaining = [item for item in current_ids if item != factor_id]
        scoring, directions = _equal_inputs(remaining, current_directions)
        full = _run_prepared(
            service,
            prepared_full,
            scoring,
            directions,
            start=START,
            end=END,
            cost_label="2x",
            label=f"drop_{factor_id}_full_2x",
        )
        recent = _run_prepared(
            service,
            prepared_recent,
            scoring,
            directions,
            start=RECENT_START,
            end=END,
            cost_label="2x",
            label=f"drop_{factor_id}_recent_2x",
        )
        full_contribution = float(baseline_full["stats"]["total_return"] or 0.0) - float(
            full["stats"]["total_return"] or 0.0
        )
        recent_contribution = float(
            baseline_recent["stats"]["total_return"] or 0.0
        ) - float(recent["stats"]["total_return"] or 0.0)
        records[factor_id] = {
            "full": full,
            "recent": recent,
            "full_marginal_contribution": round(full_contribution, 6),
            "recent_marginal_contribution": round(recent_contribution, 6),
        }
    return records


def _run_candidate_screen(
    service: StrategyBacktestService,
    candidates: list[dict[str, Any]],
    marginal: dict[str, dict[str, Any]],
    current_ids: list[str],
    current_directions: dict[str, str],
    weakest_factor: str,
    prepared_full: PreparedMatrixBacktest,
    prepared_recent: PreparedMatrixBacktest,
    prepared_weak: PreparedMatrixBacktest,
    baseline_full: dict[str, Any],
    baseline_recent: dict[str, Any],
    baseline_full_3x: dict[str, Any],
    baseline_weak: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    base_gate = _gate_metrics(baseline_full, baseline_recent, baseline_full_3x)
    results: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate["set_role"] == "already_in_set":
            continue
        result = marginal[candidate["candidate_key"]]
        gate = evaluate_statistical_gate(candidate, result)
        record = {
            "statistical_gate": gate,
            "portfolio_gate": None,
            "portfolio": None,
            "status": "incremental_rejected" if not gate["pass"] else "statistical_pass",
        }
        if gate["pass"] and candidate["registered"]:
            factor_ids = _variant_factor_set(candidate, current_ids, weakest_factor)
            directions = {
                **current_directions,
                candidate["factor_id"]: candidate["direction"],
            }
            scoring, selected_directions = _equal_inputs(factor_ids, directions)
            full = _run_prepared(
                service,
                prepared_full,
                scoring,
                selected_directions,
                start=START,
                end=END,
                cost_label="2x",
                label=f"{candidate['candidate_key']}_full_2x",
            )
            full_3x = _run_prepared(
                service,
                prepared_full,
                scoring,
                selected_directions,
                start=START,
                end=END,
                cost_label="3x",
                label=f"{candidate['candidate_key']}_full_3x",
            )
            recent = _run_prepared(
                service,
                prepared_recent,
                scoring,
                selected_directions,
                start=RECENT_START,
                end=END,
                cost_label="2x",
                label=f"{candidate['candidate_key']}_recent_2x",
            )
            weak = _run_prepared(
                service,
                prepared_weak,
                scoring,
                selected_directions,
                start=WEAK_START,
                end=WEAK_END,
                cost_label="2x",
                label=f"{candidate['candidate_key']}_weak_2x",
            )
            portfolio_gate_result = portfolio_gate(
                base_gate,
                _gate_metrics(full, recent, full_3x),
            )
            record.update(
                {
                    "portfolio": {
                        "factor_ids": factor_ids,
                        "scoring": scoring,
                        "directions": selected_directions,
                        "full_2x": full,
                        "full_3x": full_3x,
                        "recent_2x": recent,
                        "weak_2x": weak,
                    },
                    "portfolio_gate": portfolio_gate_result,
                    "status": (
                        "portfolio_pass"
                        if portfolio_gate_result["pass"]
                        else "portfolio_rejected"
                    ),
                }
            )
        elif gate["pass"] and not candidate["registered"]:
            record["status"] = "registration_required"
        results[candidate["candidate_key"]] = record
    return results


def _variant_factor_sets(factors: list[str]) -> dict[str, list[str]]:
    variants = {"all_factors": list(factors)}
    for factor_id in factors:
        short = factor_id.removeprefix("ext_alt_hs300_").removeprefix("uf_")
        variants[f"drop_{short}"] = [item for item in factors if item != factor_id]
        variants[f"single_{short}"] = [factor_id]
    return variants


def _run_ablation(
    service: StrategyBacktestService,
    final_ids: list[str],
    final_directions: dict[str, str],
    prepared_by_window: dict[str, PreparedMatrixBacktest],
    windows: dict[str, tuple[date, date]],
) -> dict[str, Any]:
    variants = _variant_factor_sets(final_ids)
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for window, (start, end) in windows.items():
        prepared = prepared_by_window[window]
        results[window] = {}
        for variant, factor_ids in variants.items():
            scoring, directions = _equal_inputs(factor_ids, final_directions)
            results[window][variant] = _run_prepared(
                service,
                prepared,
                scoring,
                directions,
                start=start,
                end=end,
                cost_label="2x",
                label=f"ablation_{window}_{variant}_2x",
            )
    effects: dict[str, list[dict[str, Any]]] = {}
    for window, window_results in results.items():
        baseline = float(window_results["all_factors"]["stats"]["total_return"] or 0.0)
        rows = []
        for factor_id in final_ids:
            short = factor_id.removeprefix("ext_alt_hs300_").removeprefix("uf_")
            drop = window_results[f"drop_{short}"]["stats"]["total_return"] or 0.0
            single = window_results[f"single_{short}"]["stats"]["total_return"] or 0.0
            rows.append(
                {
                    "factor_id": factor_id,
                    "baseline_total_return": round(baseline, 6),
                    "drop_total_return": round(float(drop), 6),
                    "marginal_contribution": round(baseline - float(drop), 6),
                    "single_total_return": round(float(single), 6),
                }
            )
        effects[window] = sorted(
            rows,
            key=lambda row: row["marginal_contribution"],
            reverse=True,
        )
    return {
        "variants": variants,
        "results": results,
        "factor_effects": effects,
    }


def _ablation_candidate_gate(
    ablation: dict[str, Any],
    new_factor_ids: list[str],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: dict[str, dict[str, float]] = {}
    for factor_id in new_factor_ids:
        full = next(
            row
            for row in ablation["factor_effects"]["full"]
            if row["factor_id"] == factor_id
        )
        recent = next(
            row
            for row in ablation["factor_effects"]["recent"]
            if row["factor_id"] == factor_id
        )
        full_value = float(full["marginal_contribution"])
        recent_value = float(recent["marginal_contribution"])
        checks[f"{factor_id}_not_materially_harmful"] = (
            full_value >= -0.005 and recent_value >= -0.005
        )
        checks[f"{factor_id}_positive_in_one_window"] = (
            full_value > 0.0 or recent_value > 0.0
        )
        details[factor_id] = {
            "full_marginal_contribution": full_value,
            "recent_marginal_contribution": recent_value,
        }
    return {"pass": all(checks.values()), "checks": checks, "details": details}


def _summarize_rolling_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    return portfolio._summarize_runs(records)


def _rolling_comparison(
    service: StrategyBacktestService,
    symbols: list[str],
    panel: pl.DataFrame,
    all_factor_ids: list[str],
    baseline_scoring: dict[str, float],
    baseline_directions: dict[str, str],
    final_scoring: dict[str, float],
    final_directions: dict[str, str],
) -> dict[str, Any]:
    records: dict[str, dict[str, list[dict[str, Any]]]] = {
        "baseline_2x": {"records": []},
        "baseline_3x": {"records": []},
        "final_2x": {"records": []},
        "final_3x": {"records": []},
    }
    for start, end in portfolio._rolling_year_windows():
        prepared = _prepare_shared_window(
            service,
            symbols,
            panel,
            all_factor_ids,
            start=start,
            end=end,
        )
        for label, scoring, directions, cost in (
            ("baseline_2x", baseline_scoring, baseline_directions, "2x"),
            ("baseline_3x", baseline_scoring, baseline_directions, "3x"),
            ("final_2x", final_scoring, final_directions, "2x"),
            ("final_3x", final_scoring, final_directions, "3x"),
        ):
            records[label]["records"].append(
                _run_prepared(
                    service,
                    prepared,
                    scoring,
                    directions,
                    start=start,
                    end=end,
                    cost_label=cost,
                    label=f"rolling_{label}_{start}",
                )
            )
    summaries = {
        label: _summarize_rolling_records(data["records"])
        for label, data in records.items()
    }
    negative = {
        label: [
            record["start"]
            for record in data["records"]
            if float(record["stats"]["total_return"] or 0.0) < 0.0
        ]
        for label, data in records.items()
    }
    checks = {
        "final_2x_positive_rate_not_lower": (
            summaries["final_2x"]["positive_rate"] or 0.0
        )
        >= (summaries["baseline_2x"]["positive_rate"] or 0.0),
        "final_3x_positive_rate_not_lower": (
            summaries["final_3x"]["positive_rate"] or 0.0
        )
        >= (summaries["baseline_3x"]["positive_rate"] or 0.0),
        "final_2x_median_return_within_1pct": (
            summaries["final_2x"]["median_total_return"] or -1.0
        )
        >= (summaries["baseline_2x"]["median_total_return"] or -1.0) - 0.01,
        "final_3x_median_return_within_1pct": (
            summaries["final_3x"]["median_total_return"] or -1.0
        )
        >= (summaries["baseline_3x"]["median_total_return"] or -1.0) - 0.01,
    }
    return {
        "records": records,
        "summaries": summaries,
        "negative_windows": negative,
        "gate": {"pass": all(checks.values()), "checks": checks},
    }


def _member_signature(members: list[Any]) -> list[tuple[str, float | None, str]]:
    return sorted(
        (
            member.factor_id,
            None if member.weight is None else round(float(member.weight), 12),
            member.direction,
        )
        for member in members
    )


def _ensure_watch_version(
    sets: FactorSetStore,
    head: Any,
    current_version: Any,
    final_ids: list[str],
    final_directions: dict[str, str],
    candidate_by_id: dict[str, dict[str, Any]],
    output_path: Path,
) -> tuple[Any, Any, bool]:
    current_members = {member.factor_id: member for member in current_version.members}
    factors = list(dict.fromkeys(final_ids))
    if not 1 <= len(factors) <= 8:
        raise ValueError("P5 watch set must contain 1..8 factors")
    new_factor_ids = sorted(set(factors) - set(current_members))
    if new_factor_ids:
        assert_portfolio_candidates_admitted(DATA, new_factor_ids)
    weight = 1.0 / len(factors)
    p5_ref = {
        "kind": "factor_increment_scan",
        "path": str(output_path.relative_to(DATA)),
    }
    members = []
    for factor_id in factors:
        candidate = candidate_by_id.get(factor_id)
        existing = current_members.get(factor_id)
        if candidate is not None:
            rationale = f"P5 增量：{candidate['label']}；{candidate['transform']}；通过边际 IC 与 T+1 组合门槛。"
            evidence_refs = [p5_ref]
        elif existing is not None:
            rationale = existing.rationale
            evidence_refs = [*existing.evidence_refs, p5_ref]
        else:
            raise ValueError(f"final member has no provenance: {factor_id}")
        members.append(
            {
                "factor_id": factor_id,
                "factor_version": None,
                "definition_fingerprint": "",
                "reference_kind": "registered_factor",
                "role": "alpha",
                "membership_status": "watch",
                "direction": final_directions[factor_id],
                "weight": weight,
                "rationale": rationale,
                "evidence_refs": evidence_refs,
            }
        )
    expected_signature = sorted((item["factor_id"], round(weight, 12), item["direction"]) for item in members)
    for version_number in (2,):
        try:
            existing_version = sets.load_version("multi_dimension_v1", version_number)
        except FileNotFoundError:
            continue
        if _member_signature(existing_version.members) == expected_signature:
            if head.watch_version != version_number:
                token = sets._head_token(head)
                head = sets.set_head(
                    "multi_dimension_v1",
                    "watch",
                    version_number,
                    token,
                    "恢复 P5 已通过的增量 watch 版本",
                )
            return existing_version, head, False
    if head.watch_version != 1:
        raise ValueError("P5 cannot create a new version from a non-v1 watch head")
    token = sets._head_token(head)
    version = sets.create_version(
        "multi_dimension_v1",
        members,
        parent_version=1,
        expected_head_sha256=token,
        change_reason="P5 增量筛选通过：加入参数稳定的边际正贡献候选",
    )
    head = sets.set_head(
        "multi_dimension_v1",
        "watch",
        version.version,
        token,
        "P5 增量筛选通过，保持 watch 且不激活",
    )
    return version, head, True


def _input_manifest() -> list[dict[str, Any]]:
    manifest = p3_scan._input_manifest(DATA)
    for label, paths in (
        ("p1_p4_research", list(RESEARCH_PATHS.values())),
        (
            "p5_research_candidates",
            [
                DATA / CUSTOM_FACTOR_DIR / f"{factor_id}.json"
                for factor_id in CUSTOM_FACTOR_IDS
            ],
        ),
        (
            "base_watch_version",
            [DATA / "factor_research" / "sets" / "multi_dimension_v1" / "versions" / "v0001.json"],
        ),
    ):
        files = [
            {
                "path": str(path.relative_to(DATA)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in paths
        ]
        manifest.append(
            {
                "label": label,
                "file_count": len(files),
                "bytes": sum(int(item["bytes"]) for item in files),
                "sha256": sha256_json(files),
                "files": files,
            }
        )
    return manifest


def _publish_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"P5 result already exists: {path}")
        return existing
    atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"P5 markdown already exists: {path}")
        return
    atomic_write_text(path, text)


def _fmt(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "-"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P5 全库增量筛选报告",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 基准集合：`{payload['base_set']['set_id']}` v{payload['base_set']['version']}",
        f"- 基准内容 SHA：`{payload['base_set']['content_sha256']}`",
        f"- 候选数：{payload['candidate_count']}；去重后：{payload['deduped_candidate_count']}",
        "- 所有结论只允许形成 watch 版本，`active_version` 保持 null。",
        "",
        "## 候选增量",
        "",
        "| 候选 | 经济含义 | 角色 | 边际 IC | NW p | q | 最大相关 | 统计门槛 | 组合门槛 | 结论 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for candidate in payload["candidates"]:
        marginal = candidate["marginal_ic"]
        portfolio_result = candidate.get("portfolio_result") or {}
        portfolio_gate_result = portfolio_result.get("portfolio_gate") or {}
        lines.append(
            f"| `{candidate['candidate_key']}` | {candidate['economic_key']} | "
            f"{candidate['set_role']} | {_fmt(marginal.get('directional_delta_mean'))} | "
            f"{_fmt(marginal.get('delta_p_value'))} | {_fmt(marginal.get('delta_q_value'))} | "
            f"{_fmt(marginal.get('max_abs_correlation_for_gate'))} | "
            f"{'pass' if candidate['statistical_gate']['pass'] else 'reject'} | "
            f"{'pass' if portfolio_gate_result.get('pass') else ('-' if not portfolio_result else 'reject')} | "
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
            f"| {label} | {_fmt(stats.get('total_return'))} | {_fmt(stats.get('annual_return'))} | "
            f"{_fmt(stats.get('sharpe'))} | {_fmt(stats.get('max_drawdown'))} | "
            f"{_fmt(stats.get('n_trades'))} |"
        )
    lines.extend(
        [
            "",
            "## 最终结论",
            "",
            f"- 决策：`{payload['decision']['status']}`",
            f"- 最终因子数：{len(payload['decision'].get('factor_ids') or [])}",
            f"- 新 watch 版本：{payload['decision'].get('new_watch_version') or '-'}",
            f"- `active_version`：{payload['decision']['active_version']}",
        ]
    )
    if payload["decision"].get("factor_ids"):
        lines.extend(["", "### 最终成员", ""])
        for factor_id in payload["decision"]["factor_ids"]:
            lines.append(f"- `{factor_id}`")
    lines.extend(
        [
            "",
            "## 拒绝与负知识",
            "",
        ]
    )
    if payload["negative_knowledge"]:
        for item in payload["negative_knowledge"]:
            lines.append(
                f"- `{item['candidate_key']}`：{', '.join(item['reasons'])}"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 门槛说明",
            "",
            "- 边际 IC：基准组合 80% + 候选 20%，NW lag=5，p≤0.10 且 BH-FDR q≤0.10。",
            "- 相关性：候选与不替换的现有成员最大平均绝对日截面相关≤0.90。",
            "- 组合：20 仓等权、open_t+1、无杠杆；2x 基准、3x 压力；收益/Sharpe 容差 1pp/0.05，回撤容差 2pp，且全周期和近一年不得同时恶化。",
            "- 滚动一年：正收益窗口比例不得低于基线，中位收益不得比基线差超过 1pp。",
            "- 拆解：新增成员在全周期或近一年至少一个窗口有正边际贡献，且两个窗口都不能出现超过 0.5pp 的实质拖累。",
            "- 当前 HS300 名单回填历史存在幸存者偏差；财务和扩展字段 PIT 仍主要是声明级证据。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    custom_hashes = _validate_custom_factors(DATA)
    catalog = build_catalog(DATA)
    save_catalog(DATA, catalog)
    payloads = _load_payloads()
    candidates, dedupe = collect_candidates(payloads)
    sets, head, current_version = _current_set(DATA)
    current_weights, current_directions, current_ids = _current_inputs(current_version)
    annotate_candidate_roles(candidates, current_ids)

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    panel, coverage = load_candidate_panel(DATA, symbols, candidates)
    scored, marginal = score_candidates(
        panel,
        candidates,
        current_weights,
        current_directions,
    )
    if args.stats_only:
        _print_stats_preview(candidates, marginal)
        print(
            json.dumps(
                {
                    "catalog_snapshot_id": catalog.snapshot_id,
                    "current_set": current_version.content_sha256,
                    "panel_rows": scored.height,
                    "candidate_count": len(candidates),
                    "dedupe_count": len(dedupe),
                    "custom_factor_hashes": custom_hashes,
                    "coverage": coverage,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    service = portfolio._service()
    all_factor_ids = list(
        dict.fromkeys(
            [
                *current_ids,
                *[candidate["factor_id"] for candidate in candidates],
            ]
        )
    )
    prepared_full = _prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=START,
        end=END,
    )
    prepared_recent = _prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=RECENT_START,
        end=END,
    )
    prepared_weak = _prepare_shared_window(
        service,
        symbols,
        scored,
        all_factor_ids,
        start=WEAK_START,
        end=WEAK_END,
    )
    baseline_full_2x = _run_prepared(
        service,
        prepared_full,
        current_weights,
        current_directions,
        start=START,
        end=END,
        cost_label="2x",
        label="baseline_full_2x",
    )
    baseline_full_3x = _run_prepared(
        service,
        prepared_full,
        current_weights,
        current_directions,
        start=START,
        end=END,
        cost_label="3x",
        label="baseline_full_3x",
    )
    baseline_recent_2x = _run_prepared(
        service,
        prepared_recent,
        current_weights,
        current_directions,
        start=RECENT_START,
        end=END,
        cost_label="2x",
        label="baseline_recent_2x",
    )
    baseline_weak_2x = _run_prepared(
        service,
        prepared_weak,
        current_weights,
        current_directions,
        start=WEAK_START,
        end=WEAK_END,
        cost_label="2x",
        label="baseline_weak_2x",
    )
    loo = _loo_contributions(
        service,
        symbols,
        scored,
        current_weights,
        current_directions,
        current_ids,
        prepared_full=prepared_full,
        prepared_recent=prepared_recent,
        baseline_full=baseline_full_2x,
        baseline_recent=baseline_recent_2x,
    )
    weakest_factor = min(
        loo,
        key=lambda factor_id: (
            loo[factor_id]["full_marginal_contribution"],
            loo[factor_id]["recent_marginal_contribution"],
            factor_id,
        ),
    )
    screen = _run_candidate_screen(
        service,
        candidates,
        marginal,
        current_ids,
        current_directions,
        weakest_factor,
        prepared_full,
        prepared_recent,
        prepared_weak,
        baseline_full_2x,
        baseline_recent_2x,
        baseline_full_3x,
        baseline_weak_2x,
    )

    candidate_records: list[dict[str, Any]] = []
    for candidate in candidates:
        record = {
            **candidate,
            "marginal_ic": marginal.get(candidate["candidate_key"], {}),
            "coverage": coverage.get(candidate["factor_id"]),
        }
        screen_record = screen.get(candidate["candidate_key"])
        if candidate["set_role"] == "already_in_set":
            record["statistical_gate"] = {
                "pass": False,
                "reasons": ["already_in_current_set"],
            }
            record["status"] = "already_in_current_set"
        elif screen_record is not None:
            record["statistical_gate"] = screen_record["statistical_gate"]
            record["portfolio_result"] = screen_record["portfolio"]
            record["portfolio_gate"] = screen_record["portfolio_gate"]
            record["status"] = screen_record["status"]
            if (
                not candidate["registered"]
                and screen_record["status"] == "registration_required"
            ):
                record["status"] = "registration_required"
        else:
            record["statistical_gate"] = {"pass": False, "reasons": ["not_tested"]}
            record["status"] = "rejected"
        candidate_records.append(record)

    passing = [
        record for record in candidate_records if record["status"] == "portfolio_pass"
    ]
    passing.sort(
        key=lambda record: (
            record["marginal_ic"].get("directional_delta_mean") or -999.0,
            record["marginal_ic"].get("delta_p_value") or 1.0,
        ),
        reverse=True,
    )
    final_ids: list[str] | None = None
    final_directions: dict[str, str] = {}
    final_candidate: dict[str, Any] | None = None
    if passing:
        final_candidate = passing[0]
        final_ids = list(final_candidate["portfolio_result"]["factor_ids"])
        final_directions = {
            **current_directions,
            final_candidate["factor_id"]: final_candidate["direction"],
        }

    ablation: dict[str, Any] | None = None
    ablation_gate: dict[str, Any] | None = None
    if final_ids is not None:
        ablation = _run_ablation(
            service,
            final_ids,
            final_directions,
            {
                "full": prepared_full,
                "recent": prepared_recent,
                "weak": prepared_weak,
            },
            {
                "full": (START, END),
                "recent": (RECENT_START, END),
                "weak": (WEAK_START, WEAK_END),
            },
        )
        new_ids = [factor_id for factor_id in final_ids if factor_id not in current_ids]
        ablation_gate = _ablation_candidate_gate(ablation, new_ids)
        if not ablation_gate["pass"]:
            if final_candidate is not None:
                final_candidate["status"] = "ablation_rejected"
            final_ids = None
            final_directions = {}

    rolling: dict[str, Any] | None = None
    if final_ids is not None:
        final_scoring, selected_final_directions = _equal_inputs(
            final_ids, final_directions
        )
        rolling = _rolling_comparison(
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
            if final_candidate is not None:
                final_candidate["status"] = "rolling_rejected"
            final_ids = None
            final_directions = {}

    input_manifest = _input_manifest()
    input_manifest_sha256 = sha256_json(input_manifest)
    data_fingerprint = sha256_json(
        {
            "input_manifest_sha256": input_manifest_sha256,
            "panel_rows": scored.height,
            "symbol_count": len(symbols),
            "start": str(START),
            "end": str(END),
            "coverage": coverage,
        }
    )
    spec_payload = [
        {
            "candidate_key": spec.candidate_key,
            "economic_key": spec.economic_key,
            "source_research": spec.source_research,
            "family_id": spec.family_id,
            "parameter": spec.parameter,
            "factor_id": spec.factor_id,
            "direction": spec.direction,
            "registered": spec.registered,
            "transform": spec.transform,
        }
        for spec in CANDIDATE_SPECS
    ]
    research_id = sha256_json(
        {
            "method_version": METHOD_VERSION,
            "p1_research_id": payloads["p1"]["research_id"],
            "p2_research_id": payloads["p2"]["research_id"],
            "p3_research_id": payloads["p3"]["research_id"],
            "p4_research_id": payloads["p4"]["research_id"],
            "catalog_snapshot_id": catalog.snapshot_id,
            "base_set_content_sha256": current_version.content_sha256,
            "candidate_specs": spec_payload,
            "dedupe": dedupe,
            "input_manifest_sha256": input_manifest_sha256,
            "data_fingerprint": data_fingerprint,
            "start": str(START),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "horizon": HORIZON,
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "fdr_threshold": FDR_THRESHOLD,
            "maximum_correlation": MAX_CORRELATION,
            "portfolio_tolerances": {"return": 0.01, "sharpe": 0.05, "drawdown": 0.02},
            "final_factor_ids": final_ids,
        }
    )
    output_path = _normalize_output_path(args.output)
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing.get("research_id") != research_id:
            raise FileExistsError(f"P5 result already exists with a different research id: {output_path}")
    markdown_path = output_path.with_suffix(".md")

    candidate_by_id = {record["factor_id"]: record for record in candidate_records}
    set_version = None
    set_version_created = False
    if final_ids is not None:
        set_version, _set_head, set_version_created = _ensure_watch_version(
            sets,
            head,
            current_version,
            final_ids,
            final_directions,
            candidate_by_id,
            output_path,
        )

    negative_knowledge: list[dict[str, Any]] = []
    for record in candidate_records:
        if record["status"] == "portfolio_pass" and final_ids is not None:
            continue
        reasons = list(record.get("statistical_gate", {}).get("reasons") or [])
        if record.get("portfolio_gate") and not record["portfolio_gate"].get("pass"):
            reasons.extend(
                f"portfolio_gate_failed:{key}"
                for key, passed in record["portfolio_gate"].get("checks", {}).items()
                if not passed
            )
        if record["status"] == "already_in_current_set":
            reasons.append("already_in_current_set")
        if record["status"] == "registration_required":
            reasons.append("registration_required")
        if record["status"] == "ablation_rejected":
            reasons.append("ablation_gate_failed")
        if record["status"] == "rolling_rejected":
            reasons.append("rolling_year_gate_failed")
        if not reasons:
            reasons.append(record["status"])
        negative_knowledge.append(
            {
                "candidate_key": record["candidate_key"],
                "factor_id": record["factor_id"],
                "status": record["status"],
                "reasons": sorted(set(reasons)),
            }
        )

    decision_status = (
        "watch_version_published"
        if final_ids is not None
        else "no_new_watch_version"
    )
    payload = {
        "schema_version": 1,
        "research_id": research_id,
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "catalog_snapshot_id": catalog.snapshot_id,
        "p1_research_id": payloads["p1"]["research_id"],
        "p2_research_id": payloads["p2"]["research_id"],
        "p3_research_id": payloads["p3"]["research_id"],
        "p4_research_id": payloads["p4"]["research_id"],
        "input_manifest_sha256": input_manifest_sha256,
        "input_manifest": input_manifest,
        "data_fingerprint": data_fingerprint,
        "period": {
            "start": str(START),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "weak_start": str(WEAK_START),
            "weak_end": str(WEAK_END),
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
            "weights": current_weights,
            "directions": current_directions,
        },
        "candidate_count": len(candidates),
        "deduped_candidate_count": len(candidates) - sum(
            1 for record in candidates if record["set_role"] == "already_in_set"
        ),
        "dedupe": dedupe,
        "candidates": candidate_records,
        "portfolio": {
            "engine": {
                "name": "StrategyBacktestService",
                "strategy_id": "factor_rank_research",
                "execution": "open_t+1",
                "max_positions": portfolio.DEFAULT_POSITIONS,
                "leverage": "none",
                "max_exposure_pct": 1.0,
            },
            "costs": {
                "2x": portfolio._cost_parameters("2x"),
                "3x": portfolio._cost_parameters("3x"),
            },
            "baseline": {
                "full_2x": baseline_full_2x,
                "full_3x": baseline_full_3x,
                "recent_2x": baseline_recent_2x,
                "weak_2x": baseline_weak_2x,
            },
            "leave_one_out": loo,
            "weakest_factor": weakest_factor,
            "screen": screen,
            "final": (
                {
                    "factor_ids": final_ids,
                    "directions": final_directions,
                }
                if final_ids is not None
                else None
            ),
        },
        "ablation": ablation,
        "ablation_gate": ablation_gate,
        "rolling_year": rolling,
        "decision": {
            "status": decision_status,
            "active_version": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
            "factor_ids": final_ids,
            "new_watch_version": (
                set_version.version if set_version is not None else None
            ),
            "set_version_created": set_version_created,
        },
        "negative_knowledge": negative_knowledge,
        "caveats": [
            "Current HS300 membership is applied to history; survivorship bias remains.",
            "Extension and financial factor PIT evidence is declared, not fully field-level verified.",
            "Marginal IC and portfolio gates are research admission rules, not a profit guarantee.",
            "No leverage, paper target, order, or production activation is produced.",
            "The P5 set remains watch-only; active_version is null.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    published = _publish_json(output_path, payload)
    _publish_markdown(markdown_path, render_markdown(published))
    output_sha = sha256_file(output_path)
    markdown_sha = sha256_file(markdown_path)
    ledger = ResearchLedger(DATA)
    ledger.verify()
    if not any(
        record.operation == OPERATION
        and record.input_fingerprints.get("research_id") == research_id
        for record in ledger.records()
    ):
        ledger.append(
            OPERATION,
            payload={
                "catalog_snapshot_id": catalog.snapshot_id,
                "input_fingerprints": {
                    "research_id": research_id,
                    "p1_research_id": payloads["p1"]["research_id"],
                    "p2_research_id": payloads["p2"]["research_id"],
                    "p3_research_id": payloads["p3"]["research_id"],
                    "p4_research_id": payloads["p4"]["research_id"],
                    "base_set_content_sha256": current_version.content_sha256,
                    "input_manifest_sha256": input_manifest_sha256,
                    "data_fingerprint": data_fingerprint,
                    "output_sha256": output_sha,
                    "markdown_sha256": markdown_sha,
                },
                "set_id": current_version.set_id,
                "set_version": set_version.version if set_version is not None else None,
                "evidence_refs": [
                    {
                        "kind": "factor_increment_scan",
                        "path": str(output_path.relative_to(DATA)),
                    },
                    {
                        "kind": "factor_increment_markdown",
                        "path": str(markdown_path.relative_to(DATA)),
                    },
                ],
                "metrics": {
                    "candidate_count": len(candidates),
                    "portfolio_pass_count": len(passing),
                    "final_factor_count": len(final_ids or []),
                    "new_watch_version": (
                        set_version.version if set_version is not None else None
                    ),
                },
                "verdict": "watch_only_no_active_version",
            },
        )
    print(
        json.dumps(
            {
                "research_id": research_id,
                "output": str(output_path.relative_to(ROOT)),
                "candidate_count": len(candidates),
                "portfolio_pass": [item["candidate_key"] for item in passing],
                "final_factor_ids": final_ids,
                "decision": decision_status,
                "new_watch_version": (
                    set_version.version if set_version is not None else None
                ),
                "active_version": None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
