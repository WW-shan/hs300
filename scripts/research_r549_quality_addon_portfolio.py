#!/usr/bin/env python3
"""R549B: formal add-one quality composites on the stable7 vol_12 portfolio."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R549B"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r549b_quality_addon_portfolio_preregistration.json"
)
RUNNER = "scripts/research_r549_quality_addon_portfolio.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R548_RESULT = RESEARCH / "r548_multi_horizon_incremental_screen_result.json"
R548_PREREG = RESEARCH / "r548_multi_horizon_incremental_screen_preregistration.json"
R548_RUNNER = ROOT / "scripts" / "research_r548_multi_horizon_incremental_screen.py"
R549_ATTEMPT1 = RESEARCH / "r549_attempt1_prepublication_runner_failure.json"
R549_ATTEMPT2 = RESEARCH / "r549b_attempt2_preregistration_schema_failure.json"
R546_RESULT = RESEARCH / "r546_vol12_confirmation_result.json"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
R547_PREREG = RESEARCH / "r547_incremental_factor_screen_preregistration.json"
R547_RUNNER = ROOT / "scripts" / "research_r547_incremental_factor_screen.py"
PREREG = RESEARCH / "r549b_quality_addon_portfolio_preregistration.json"
RESULT = RESEARCH / "r549b_quality_addon_portfolio_result.json"
MARKDOWN = RESEARCH / "r549b_quality_addon_portfolio_result.md"
MEMBERSHIP = RESEARCH / "r526_unified_pit_bundle_window" / "daily_snapshots.parquet"
INDUSTRY = RESEARCH / "r526_unified_pit_bundle_window" / "industry_history.parquet"

START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
STOP_LOSS = -0.12

VARIANTS = {
    "control": {
        "label": "stable7 + vol_12",
        "addons": (),
    },
    "cash_quality": {
        "label": "stable7 + cash_quality",
        "addons": ("cf_r547_cash_quality",),
    },
    "safety": {
        "label": "stable7 + value_quality_safety",
        "addons": ("cf_r547_value_quality_safety",),
    },
    "cash_safety": {
        "label": "stable7 + cash_quality + value_quality_safety",
        "addons": (
            "cf_r547_cash_quality",
            "cf_r547_value_quality_safety",
        ),
    },
}
VARIANT_ORDER = tuple(VARIANTS)


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


def policy_hash(value: dict[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _candidate_frame(candidate_panel: pl.DataFrame, factor_id: str) -> pl.DataFrame:
    return candidate_panel.select(
        "symbol",
        "date",
        pl.col(factor_id).cast(pl.Float64).alias("factor_value"),
    ).drop_nulls(["symbol", "date", "factor_value"])


def build_variant_panel(
    runtime: dict[str, Any],
    candidate_panel: pl.DataFrame,
    addons: tuple[str, ...],
) -> pl.DataFrame:
    """Build a PIT score panel for stable7 plus optional add-on factors."""
    if not addons:
        return runtime["panel"].select("symbol", "date", "score", "core_score").sort(
            ["date", "symbol"]
        )
    stable = runtime["representatives"]
    members = [{"family_id": row["family_id"]} for row in stable]
    candidates = {
        row["family_id"]: runtime["candidates"][row["family_id"]] for row in stable
    }
    frames = {row["family_id"]: runtime["frames"][row["family_id"]] for row in stable}
    for factor_id in addons:
        if factor_id not in candidate_panel.columns:
            raise RuntimeError(f"R549 add-on factor missing: {factor_id}")
        members.append({"family_id": factor_id})
        candidates[factor_id] = SimpleNamespace(
            candidate=SimpleNamespace(
                family_id=factor_id,
                parameter=1,
                family={"direction": "high"},
            )
        )
        frames[factor_id] = _candidate_frame(candidate_panel, factor_id)
    study = runtime["runtime"]["study"]
    core = study["r531"]._core_frame(
        members,
        candidates,
        frames,
        study["membership"],
    )
    score = runtime["r533"].scaled_score_panel(core)
    control = runtime["panel"].select("symbol", "date", "core_score")
    return score.join(control, on=["symbol", "date"], how="inner").sort(
        ["date", "symbol"]
    )


def _training_eligible(run: dict[str, Any]) -> bool:
    stats = run.get("stats") or run
    trade_count = run.get("trade_count", stats.get("n_trades", 0))
    return (
        isinstance(stats.get("annual_return"), (int, float))
        and float(stats["annual_return"]) >= 0.08
        and isinstance(stats.get("sharpe"), (int, float))
        and float(stats["sharpe"]) >= 0.80
        and isinstance(stats.get("max_drawdown"), (int, float))
        and float(stats["max_drawdown"]) >= -0.20
        and int(trade_count or 0) >= 20
    )


def select_variant(rows: dict[str, dict[str, Any]]) -> str | None:
    """Select from training and validation only; recent is deliberately ignored."""
    eligible = []
    for variant_id, row in rows.items():
        train_run = row.get("train") or {}
        validation_run = row.get("validation") or {}
        train = train_run.get("stats") or train_run
        validation = validation_run.get("stats") or validation_run
        val_annual = validation.get("annual_return")
        if not _training_eligible(train_run):
            continue
        if not isinstance(val_annual, (int, float)) or float(val_annual) <= 0.0:
            continue
        eligible.append((variant_id, train, validation, float(val_annual)))
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            min(float(item[1]["annual_return"]), item[3]),
            item[3],
            float(item[1]["sharpe"]),
            -float(item[1]["max_drawdown"]),
            -VARIANT_ORDER.index(item[0]),
        ),
    )[0]


def _fresh_gate(values: list[float]) -> dict[str, Any]:
    positive = sum(float(value) > 0 for value in values)
    median = float(st.median(values)) if values else None
    return {
        "positive_count": positive,
        "median": median,
        "pass": positive >= 4 and median is not None and median > 0,
    }


def final_gate(
    *,
    full: dict[str, Any],
    recent: dict[str, Any],
    validation: dict[str, Any],
    fresh_values: list[float],
    stress_3x: dict[str, Any],
    warning_5x: dict[str, Any],
) -> dict[str, Any]:
    fresh = _fresh_gate(fresh_values)
    checks = {
        "full_annual_min": float(full.get("annual_return") or 0.0) >= 0.10,
        "full_sharpe_min": float(full.get("sharpe") or 0.0) >= 0.90,
        "full_mdd_min": float(full.get("max_drawdown") or 0.0) >= -0.18,
        "recent_return_positive": float(recent.get("total_return") or 0.0) > 0.0,
        "validation_annual_positive": float(validation.get("annual_return") or 0.0)
        > 0.0,
        "fresh_starts_pass": bool(fresh["pass"]),
    }
    stress = {
        "stress_3x_annual_min": float(stress_3x.get("annual_return") or 0.0) >= 0.08,
        "stress_3x_mdd_min": float(stress_3x.get("max_drawdown") or 0.0) >= -0.20,
        "stress_3x_recent_min": float(stress_3x.get("recent_total_return") or 0.0)
        >= -0.03,
    }
    warnings = []
    if float(warning_5x.get("annual_return") or 0.0) < 0.0:
        warnings.append("negative_5x_annual")
    if float(warning_5x.get("max_drawdown") or 0.0) < float(
        stress_3x.get("max_drawdown") or 0.0
    ) - 0.05:
        warnings.append("drawdown_deterioration_gt_5pp")
    core_pass = all(checks.values())
    stress_pass = all(stress.values())
    failed = [key for key, value in {**checks, **stress}.items() if not value]
    return {
        "checks": checks,
        "stress_checks": stress,
        "fresh_start": fresh,
        "core_pass": core_pass,
        "stress_pass": stress_pass,
        "five_x_hard_rejection": False,
        "five_x_warnings": warnings,
        "failed_gates": failed,
        "conclusion": "watch_only" if core_pass and stress_pass else "rejected",
    }


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def _run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
) -> dict[str, Any]:
    r545 = _load_module("research_r545_stable7_risk_budget")
    r546 = _load_module("research_r546_vol12_confirmation")
    prepared = r545.prepare_panel(
        p5,
        service,
        runtime,
        panel,
        start=start,
        end=end,
    )
    config = runtime["r533"].build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        max_hold_days=MAX_HOLD_DAYS,
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": STOP_LOSS,
        "trailing_stop": None,
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": INDUSTRY_CAP,
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "policy"
    config.position_policy = r546.VARIANT["id"]
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R549:{label}: prepared signature mismatch")
    with r545.policy_injection(r546.VARIANT):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R549:{label}")
        if result.error:
            raise RuntimeError(f"R549:{label}: {result.error}")
        trades = [
            trade if isinstance(trade, dict) else service._trade_to_dict(trade)
            for trade in result.trades
        ]
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def execution_contract() -> dict[str, Any]:
    return {
        "matching": "open_t+1",
        "cost_multiplier": 2,
        "leverage": "none",
        "max_exposure_pct": 1.0,
    }


def scientific_design() -> dict[str, Any]:
    return {
        "variants": {
            key: {"label": value["label"], "addons": list(value["addons"])}
            for key, value in VARIANTS.items()
        },
        "baseline": "stable7 + vol_12",
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "industry_cap": INDUSTRY_CAP,
            "stop_loss": STOP_LOSS,
            "trailing_stop": None,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_policy": "warning_only",
        },
        "selection_window": {
            "start": str(START),
            "end": str(TRAIN_END),
        },
        "selection_policy": "training gate then highest min(train annual, validation annual); recent excluded",
        "discovery_runs": len(VARIANTS) * 3,
        "final_runs": 11,
        "gates": {
            "finite_fresh_train_selection": True,
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.18,
            "recent_return_positive": True,
            "validation_annual_positive": True,
            "fresh_positive_min": 4,
            "fresh_median_positive": True,
            "stress_3x_annual_min": 0.08,
            "stress_3x_mdd_min": -0.20,
            "stress_3x_recent_min": -0.03,
            "five_x_hard_rejection": False,
        },
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "recent_is_independent_oos": False,
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _input_paths(root: Path) -> dict[str, Path]:
    r548 = _load_module("research_r548_multi_horizon_incremental_screen")
    return {
        **r548._input_paths(root),
        "r546_result": root / R546_RESULT.relative_to(ROOT),
        "r548_result": root / R548_RESULT.relative_to(ROOT),
        "r548_preregistration": root / R548_PREREG.relative_to(ROOT),
        "r548_runner": root / R548_RUNNER.relative_to(ROOT),
        "r549_attempt1": root / R549_ATTEMPT1.relative_to(ROOT),
        "r549_attempt2": root / R549_ATTEMPT2.relative_to(ROOT),
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R549 preregistration already exists: {PREREG}")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Does adding one or two PIT quality composites to the stable7 vol_12 "
            "portfolio improve recent-year return without breaking full-period risk, "
            "validation, stress-cost and fresh-start gates?"
        ),
        "hypothesis": (
            "The 60-day positive cash-quality and value-quality-safety signals from "
            "R548 can add return or reduce drawdown in the formal project portfolio, "
            "without using recent-year returns to select the variant."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {
            **{name: sha256(path) for name, path in _input_paths(root).items()},
            "alt_timeseries": r547._ext_timeseries_hash(),
            "candidate_definitions": policy_hash(candidate_hashes),
        },
        "parameters": {
            **scientific_design(),
            "candidate_definitions": candidate_hashes,
        },
        "sample": scientific_design()["windows"],
        "execution": execution_contract(),
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "selection_uses_training_and_validation_only",
            "recent_is_not_used_for_variant_selection",
            "formal_evidence_saved_lookahead_t1",
            "financial_factors_use_announcement_t_plus_one_gate",
            "r526_pit_membership_and_industry_used",
            "five_x_warning_only",
            "no_orders_or_active_version",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R549 runner hash no longer matches preregistration")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    if prereg["parameters"]["candidate_definitions"] != candidate_hashes:
        raise RuntimeError("R549 candidate definition hash changed")
    if prereg["input_hashes"]["candidate_definitions"] != policy_hash(candidate_hashes):
        raise RuntimeError("R549 candidate definition aggregate hash changed")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R549 input changed: {name}")
    if r547._ext_timeseries_hash() != prereg["input_hashes"].get("alt_timeseries"):
        raise RuntimeError("R549 extension timeseries changed")


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    r547 = _load_module("research_r547_incremental_factor_screen")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    membership = r547._load_membership(root)
    candidate_panel = r547._load_price_panel(root, membership)
    definitions = r547.load_candidate_definitions(root)
    candidate_panel = r547._add_candidate_sources(candidate_panel, definitions)
    panels = {
        variant_id: build_variant_panel(runtime, candidate_panel, spec["addons"])
        for variant_id, spec in VARIANTS.items()
    }
    discovery: dict[str, dict[str, Any]] = {}
    for variant_id, panel in panels.items():
        train = _run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"{variant_id}_train_2x",
            start=START,
            end=TRAIN_END,
            cost_label="2x",
        )
        validation = _run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"{variant_id}_validation_2x",
            start=VALIDATION_START,
            end=VALIDATION_END,
            cost_label="2x",
        )
        recent = _run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"{variant_id}_recent_2x",
            start=RECENT_START,
            end=END,
            cost_label="2x",
        )
        discovery[variant_id] = {
            "train": train,
            "validation": validation,
            "recent": recent,
        }
    selected = select_variant(discovery)
    if selected is None:
        raise RuntimeError("R549 no variant passed training+validation selection")
    selected_panel = panels[selected]
    full = {
        cost: _run_window(
            p5,
            service,
            runtime,
            selected_panel,
            label=f"selected_full_{cost}",
            start=START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    recent = {"2x": discovery[selected]["recent"]}
    for cost in ("3x", "5x"):
        recent[cost] = _run_window(
            p5,
            service,
            runtime,
            selected_panel,
            label=f"selected_recent_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
    validation = discovery[selected]["validation"]
    fresh = []
    for start in FRESH_STARTS:
        run = _run_window(
            p5,
            service,
            runtime,
            selected_panel,
            label=f"selected_fresh_{start.isoformat()}_2x",
            start=start,
            end=END,
            cost_label="2x",
        )
        fresh.append(run)
    all_runs = [
        run
        for row in discovery.values()
        for run in (row["train"], row["validation"], row["recent"])
    ] + [*full.values(), *recent.values(), validation, *fresh]
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in all_runs
    )
    full_2x = full["2x"]
    recent_2x = recent["2x"]
    full_2x["bootstrap"] = runtime["r533"].bootstrap_curve(
        full_2x["curve"], seed=54901
    )
    recent_2x["bootstrap"] = runtime["r533"].bootstrap_curve(
        recent_2x["curve"], seed=54902
    )
    gate = final_gate(
        full=full_2x["stats"],
        recent=recent_2x["stats"],
        validation=validation["stats"],
        fresh_values=[float(run["stats"]["total_return"]) for run in fresh],
        stress_3x={
            **full["3x"]["stats"],
            "recent_total_return": recent["3x"]["stats"]["total_return"],
        },
        warning_5x={
            "annual_return": full["5x"]["stats"]["annual_return"],
            "max_drawdown": full["5x"]["stats"]["max_drawdown"],
        },
    )
    if not evidence_ok:
        gate["failed_gates"].append("formal_evidence")
        gate["conclusion"] = "rejected"
    r546_source = json.loads(R546_RESULT.read_text(encoding="utf-8"))
    selected_addons = list(VARIANTS[selected]["addons"])
    comparison = {
        "r546_full_2x": r546_source["metrics"]["full"]["2x"],
        "r546_recent_2x": r546_source["metrics"]["recent"]["2x"],
        "selected_variant": selected,
        "selected_addons": selected_addons,
        "full_annual_delta_vs_r546": round(
            float(full_2x["stats"]["annual_return"])
            - float(r546_source["metrics"]["full"]["2x"]["stats"]["annual_return"]),
            8,
        ),
        "full_mdd_delta_vs_r546": round(
            float(full_2x["stats"]["max_drawdown"])
            - float(r546_source["metrics"]["full"]["2x"]["stats"]["max_drawdown"]),
            8,
        ),
        "recent_return_delta_vs_r546": round(
            float(recent_2x["stats"]["total_return"])
            - float(r546_source["metrics"]["recent"]["2x"]["stats"]["total_return"]),
            8,
        ),
    }
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "selected_variant": selected,
        "selected_addons": selected_addons,
        "discovery": {
            variant_id: {
                window: _public_run(run)
                for window, run in rows.items()
            }
            for variant_id, rows in discovery.items()
        },
        "full": {cost: _public_run(run) for cost, run in full.items()},
        "recent": {cost: _public_run(run) for cost, run in recent.items()},
        "validation": _public_run(validation),
        "fresh_starts": [_public_run(run) for run in fresh],
        "bootstrap": {
            "full_2x": full_2x["bootstrap"],
            "recent_2x": recent_2x["bootstrap"],
        },
        "comparison": comparison,
        "gate": gate,
        "formal_runs": [_public_run(run) for run in all_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "R547/R548 already observed recent-year outcomes; R549 recent result is not independent OOS.",
            "Variant selection uses training and validation only; recent is reported as a risk diagnostic.",
            "The add-on composites are fixed draft definitions with no weight tuning.",
            "Five-times cost is warning-only and no leverage is used.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": [] if gate["conclusion"] == "watch_only" else gate["failed_gates"],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R549B 质量复合正式加一组合",
        "",
        "> recent 已在 R547/R548 观察过，本轮最多 `watch_only`。",
        "",
        "## 选择",
        "",
        f"- 选中变体：`{payload['selected_variant']}`",
        f"- 加一因子：{', '.join(f'`{item}`' for item in payload['selected_addons']) if payload['selected_addons'] else '无'}",
        "",
        "## 发现窗口",
        "",
        "| 变体 | 训练年化 | 验证年化 | 近期总收益 | 选择 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for variant_id, row in payload["discovery"].items():
        lines.append(
            f"| {variant_id} | {row['train']['stats']['annual_return']:.2%} "
            f"| {row['validation']['stats']['annual_return']:.2%} "
            f"| {row['recent']['stats']['total_return']:.2%} "
            f"| {'selected' if variant_id == payload['selected_variant'] else ''} |"
        )
    lines += [
        "",
        "## 核心结果",
        "",
        "| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, run in (
        ("full 2x", payload["full"]["2x"]),
        ("full 3x", payload["full"]["3x"]),
        ("full 5x", payload["full"]["5x"]),
        ("recent 2x", payload["recent"]["2x"]),
        ("recent 3x", payload["recent"]["3x"]),
        ("recent 5x", payload["recent"]["5x"]),
        ("validation 2x", payload["validation"]),
    ):
        stats = run["stats"]
        lines.append(
            f"| {label} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} "
            f"| {stats['max_drawdown']:.2%} | {stats['total_return']:.2%} "
            f"| {int(stats['n_trades'])} |"
        )
    lines += [
        "",
        "## 与 R546 对比",
        "",
        f"- Full 年化差：{payload['comparison']['full_annual_delta_vs_r546']:+.2%}",
        f"- Full MDD 差：{payload['comparison']['full_mdd_delta_vs_r546']:+.2%}",
        f"- Recent 收益差：{payload['comparison']['recent_return_delta_vs_r546']:+.2%}",
        "",
        "## Fresh starts",
        "",
        "| 起始日 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for run in payload["fresh_starts"]:
        stats = run["stats"]
        lines.append(
            f"| {run['start']} | {stats['total_return']:.2%} | {stats['annual_return']:.2%} "
            f"| {stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- 核心通过：{payload['gate']['core_pass']}",
        f"- 3× 压力通过：{payload['gate']['stress_pass']}",
        f"- Fresh-start：{payload['gate']['fresh_start']}",
        f"- 5× warnings：{payload['gate']['five_x_warnings']}",
        f"- 失败项：{payload['gate']['failed_gates']}",
        f"- 结论：`{payload['conclusion']}`",
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
            "generated_at": payload["generated_at"],
            "conclusion": payload["conclusion"],
            "gate_results": payload["gate"],
            "metrics": {
                "design": payload["design"],
                "selected_variant": payload["selected_variant"],
                "selected_addons": payload["selected_addons"],
                "discovery": payload["discovery"],
                "full": payload["full"],
                "recent": payload["recent"],
                "validation": payload["validation"],
                "fresh_starts": payload["fresh_starts"],
                "bootstrap": payload["bootstrap"],
                "comparison": payload["comparison"],
                "formal_runs": payload["formal_runs"],
            },
            "source_result_hashes": [
                sha256(R548_RESULT),
                sha256(R547_RESULT),
                sha256(R546_RESULT),
            ],
            "formal_evidence": payload["full"]["2x"]["formal_evidence"],
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
        operation="r549b_quality_addon_portfolio_published",
    ).__dict__


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
        raise FileExistsError("R549 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(
        json.dumps(
            {
                "conclusion": payload["conclusion"],
                "selected_variant": payload["selected_variant"],
                "full_2x": payload["full"]["2x"]["stats"],
                "recent_2x": payload["recent"]["2x"]["stats"],
                "validation_2x": payload["validation"]["stats"],
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
