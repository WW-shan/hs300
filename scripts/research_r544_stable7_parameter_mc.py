#!/usr/bin/env python3
"""R544: stable-seven factor execution-parameter plateau and Monte Carlo."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r544_stable7_parameter_mc_preregistration.json"
)
RESEARCH_ID = "R544"
RUNNER = "scripts/research_r544_stable7_parameter_mc.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R543_RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
R543_PREREG = RESEARCH / "r543_stable_ic_subset_preregistration.json"
R543_RUNNER = ROOT / "scripts/research_r543_stable_ic_subset.py"
R542_RESULT = RESEARCH / "r542_recent_failure_attribution_result.json"
R542_PREREG = RESEARCH / "r542_recent_failure_attribution_preregistration.json"
R542_RUNNER = ROOT / "scripts/research_r542_recent_failure_attribution.py"
R539_RESULT = RESEARCH / "r539_exit_risk_grid_result.json"
R539_PREREG = RESEARCH / "r539_exit_risk_grid_preregistration.json"
PREREG = RESEARCH / "r544_stable7_parameter_mc_preregistration.json"
RESULT = RESEARCH / "r544_stable7_parameter_mc_result.json"
MARKDOWN = RESEARCH / "r544_stable7_parameter_mc_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
DEFAULT_PARAMS = {
    "max_positions": 10,
    "max_hold_days": 60,
    "industry_cap": 3,
    "stop_variant": "stop_12",
    "stop_loss": -0.12,
}
POSITIONS = (10, 15, 20)
HOLD_DAYS = (40, 60, 80)
INDUSTRY_CAPS = (2, 3, 4)
STOPS = (
    ("stop_08", -0.08),
    ("stop_10", -0.10),
    ("stop_12", -0.12),
    ("stop_15", -0.15),
)
EXPECTED_MEMBERS = (
    "amount_level",
    "bollinger_width",
    "dividend_yield_quantile",
    "fund_holding_ratio_absolute",
    "max_return",
    "turnover_level",
    "vol_of_vol",
)
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 544
PLATFORM_FINALISTS = 5


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
    sys.modules[name] = module
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


def parameter_grid() -> list[dict[str, Any]]:
    return [
        {
            "max_positions": positions,
            "max_hold_days": hold,
            "industry_cap": cap,
            "stop_variant": stop_variant,
            "stop_loss": stop_loss,
        }
        for positions in POSITIONS
        for hold in HOLD_DAYS
        for cap in INDUSTRY_CAPS
        for stop_variant, stop_loss in STOPS
    ]


def parameter_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(row["max_positions"]),
        int(row["max_hold_days"]),
        int(row["industry_cap"]),
        str(row["stop_variant"]),
    )


def default_distance(row: dict[str, Any]) -> int:
    def index(values: tuple[Any, ...], value: Any) -> int:
        return values.index(value)

    return (
        abs(index(POSITIONS, int(row["max_positions"])) - index(POSITIONS, DEFAULT_PARAMS["max_positions"]))
        + abs(index(HOLD_DAYS, int(row["max_hold_days"])) - index(HOLD_DAYS, DEFAULT_PARAMS["max_hold_days"]))
        + abs(index(INDUSTRY_CAPS, int(row["industry_cap"])) - index(INDUSTRY_CAPS, DEFAULT_PARAMS["industry_cap"]))
        + abs(
            index([name for name, _ in STOPS], str(row["stop_variant"]))
            - index([name for name, _ in STOPS], DEFAULT_PARAMS["stop_variant"])
        )
    )


def training_eligible(row: dict[str, Any]) -> bool:
    stats = row.get("stats") or {}
    return (
        isinstance(stats.get("annual_return"), (int, float))
        and float(stats["annual_return"]) >= 0.10
        and isinstance(stats.get("sharpe"), (int, float))
        and float(stats["sharpe"]) >= 0.90
        and isinstance(stats.get("max_drawdown"), (int, float))
        and float(stats["max_drawdown"]) >= -0.20
        and int(row.get("trade_count") or 0) >= 20
    )


def plateau_neighbors(
    row: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    row_values = (
        POSITIONS.index(int(row["max_positions"])),
        HOLD_DAYS.index(int(row["max_hold_days"])),
        INDUSTRY_CAPS.index(int(row["industry_cap"])),
        [name for name, _ in STOPS].index(str(row["stop_variant"])),
    )
    output = []
    for candidate in rows:
        if parameter_key(candidate) == parameter_key(row):
            continue
        candidate_values = (
            POSITIONS.index(int(candidate["max_positions"])),
            HOLD_DAYS.index(int(candidate["max_hold_days"])),
            INDUSTRY_CAPS.index(int(candidate["industry_cap"])),
            [name for name, _ in STOPS].index(str(candidate["stop_variant"])),
        )
        if all(abs(left - right) <= 1 for left, right in zip(row_values, candidate_values, strict=True)):
            output.append(candidate)
    return output


def _neighbor_stats(row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    neighbors = plateau_neighbors(row, rows)
    if not neighbors:
        neighbors = [row]
    pass_count = sum(
        training_eligible(neighbor)
        and float(neighbor["stats"]["annual_return"]) >= 0.08
        and float(neighbor["stats"]["sharpe"]) >= 0.85
        and float(neighbor["stats"]["max_drawdown"]) >= -0.22
        for neighbor in neighbors
    )
    return {
        "neighbors": len(neighbors),
        "pass_rate": round(pass_count / len(neighbors), 6) if neighbors else 0.0,
        "median_annual": round(st.median(float(row["stats"]["annual_return"]) for row in neighbors), 8),
        "median_sharpe": round(st.median(float(row["stats"]["sharpe"]) for row in neighbors), 8),
        "median_mdd": round(st.median(float(row["stats"]["max_drawdown"]) for row in neighbors), 8),
    }


def select_plateau(
    rows: list[dict[str, Any]],
    *,
    limit: int = PLATFORM_FINALISTS,
) -> list[dict[str, Any]]:
    eligible = [row for row in rows if training_eligible(row)]
    if not eligible:
        return []
    ranked = []
    for row in eligible:
        stats = _neighbor_stats(row, rows)
        enriched = {
            **row,
            "neighbor_stats": stats,
            "default_distance": default_distance(row),
        }
        ranked.append(enriched)
    ranked.sort(
        key=lambda row: (
            -float(row["neighbor_stats"]["pass_rate"]),
            -float(row["neighbor_stats"]["median_sharpe"]),
            -float(row["neighbor_stats"]["median_annual"]),
            -float(row["stats"]["max_drawdown"]),
            int(row["default_distance"]),
            parameter_key(row),
        )
    )
    return ranked[:limit]


def select_bootstrap_finalist(finalists: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        finalists,
        key=lambda row: (
            -float(row["bootstrap"]["total_p05"]),
            -float(row["neighbor_stats"]["median_sharpe"]),
            -float(row["neighbor_stats"]["median_annual"]),
            int(row["default_distance"]),
            parameter_key(row),
        ),
    )


def scientific_design() -> dict[str, Any]:
    return {
        "members": list(EXPECTED_MEMBERS),
        "grid_points": len(parameter_grid()),
        "confirmation_runs": 13,
        "formal_runs": len(parameter_grid()) + 13,
        "bootstrap_runs": BOOTSTRAP_RUNS,
        "bootstrap_block": BOOTSTRAP_BLOCK,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "selection_window": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
        },
        "confirmation_windows": {
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "parameter_grid": {
            "max_positions": list(POSITIONS),
            "max_hold_days": list(HOLD_DAYS),
            "industry_cap": list(INDUSTRY_CAPS),
            "stops": [{"name": name, "value": value} for name, value in STOPS],
        },
        "selection_order": [
            "neighbor_pass_rate_desc",
            "neighbor_median_sharpe_desc",
            "neighbor_median_annual_desc",
            "own_mdd_desc",
            "default_distance_asc",
            "bootstrap_p05_desc",
        ],
        "recent_is_independent_oos": False,
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_policy": "warning_only",
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def freeze_preregistration(root: Path) -> Path:
    prereg = root / RESEARCH_PREREGISTRATION
    if prereg.exists() or prereg.with_name(prereg.name + ".sha256").exists():
        raise FileExistsError(f"R544 preregistration already exists: {prereg}")
    sources = {
        "r543_result": root / R543_RESULT.relative_to(ROOT),
        "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
        "r543_runner": root / R543_RUNNER.relative_to(ROOT),
        "r542_result": root / R542_RESULT.relative_to(ROOT),
        "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
        "r542_runner": root / R542_RUNNER.relative_to(ROOT),
        "r539_result": root / R539_RESULT.relative_to(ROOT),
        "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Which execution-parameter plateau is robust for the R543 stable-seven "
            "factor set under training-only selection?"
        ),
        "hypothesis": (
            "A neighborhood-stable parameter point will retain recent positive "
            "performance better than a single-point optimizer."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in sources.items()},
        "parameters": scientific_design(),
        "sample": {
            **scientific_design()["selection_window"],
            **scientific_design()["confirmation_windows"],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "grid_points": 108,
            "confirmation_runs": 13,
            "formal_runs": 121,
            "selection_uses_training_only": True,
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "training_only_parameter_selection",
            "neighborhood_plateau_not_isolated_spike",
            "5000_block_bootstrap_for_platform_finalists",
            "validation_and_recent_are_confirmation_only",
            "recent_not_independent_oos",
            "five_x_warning_only",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(prereg, raw)
    return prereg


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R544 runner hash no longer matches preregistration")
    for name, expected in prereg["input_hashes"].items():
        paths = {
            "r543_result": root / R543_RESULT.relative_to(ROOT),
            "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
            "r543_runner": root / R543_RUNNER.relative_to(ROOT),
            "r542_result": root / R542_RESULT.relative_to(ROOT),
            "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
            "r542_runner": root / R542_RUNNER.relative_to(ROOT),
            "r539_result": root / R539_RESULT.relative_to(ROOT),
            "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
        }
        if sha256(paths[name]) != expected:
            raise RuntimeError(f"R544 input changed: {name}")


def run_window(
    p5: Any,
    service: Any,
    prepared: Any,
    runtime: dict[str, Any],
    *,
    params: dict[str, Any],
    label: str,
    start: date,
    end: date,
    cost_label: str,
) -> dict[str, Any]:
    config = runtime["r533"].build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=int(params["max_positions"]),
        max_hold_days=int(params["max_hold_days"]),
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": float(params["stop_loss"]),
        "trailing_stop": None,
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": int(params["industry_cap"]),
    }
    config.max_exposure_pct = 1.0
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R544:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R544:{label}")
    if result.error:
        raise RuntimeError(f"R544:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "params": dict(params),
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def prepare_panel(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: Any,
    *,
    hold: int,
    params: dict[str, Any],
    start: date,
    end: date,
) -> Any:
    seed = p5.portfolio._backtest_config(
        {"score": 1.0},
        {"score": "high"},
        start=start,
        end=end,
        max_positions=20,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=hold,
    )
    seed.symbols = list(runtime["symbols"])
    seed.hs300 = False
    seed.overrides = {
        **(seed.overrides or {}),
        "stop_loss": float(params["stop_loss"]),
        "trailing_stop": None,
    }
    prepared_base = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared_base,
        panel,
        ["score", "core_score"],
    )


def public_grid_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": row["params"],
        "stats": row["stats"],
        "trade_count": row["trade_count"],
        "eligible": training_eligible(row),
        "formal_evidence": row["formal_evidence"],
        **({"neighbor_stats": row["neighbor_stats"]} if "neighbor_stats" in row else {}),
        **({"bootstrap": row["bootstrap"]} if "bootstrap" in row else {}),
    }


def run_confirmation(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: Any,
    selected: dict[str, Any],
) -> dict[str, Any]:
    full = {
        cost: run_window(
            p5,
            service,
            prepare_panel(
                p5, service, runtime, panel,
                hold=int(selected["params"]["max_hold_days"]),
                params=selected["params"], start=TRAIN_START, end=END,
            ),
            runtime,
            params=selected["params"],
            label=f"confirm_full_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    recent = {
        cost: run_window(
            p5,
            service,
            prepare_panel(
                p5, service, runtime, panel,
                hold=int(selected["params"]["max_hold_days"]),
                params=selected["params"], start=RECENT_START, end=END,
            ),
            runtime,
            params=selected["params"],
            label=f"confirm_recent_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    validation = run_window(
        p5,
        service,
        prepare_panel(
            p5, service, runtime, panel,
            hold=int(selected["params"]["max_hold_days"]),
            params=selected["params"], start=VALIDATION_START, end=VALIDATION_END,
        ),
        runtime,
        params=selected["params"],
        label="confirm_validation_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    fresh = []
    for start in FRESH_STARTS:
        run = run_window(
            p5,
            service,
            prepare_panel(
                p5, service, runtime, panel,
                hold=int(selected["params"]["max_hold_days"]),
                params=selected["params"], start=start, end=END,
            ),
            runtime,
            params=selected["params"],
            label=f"confirm_fresh_{start.isoformat()}_2x",
            start=start,
            end=END,
            cost_label="2x",
        )
        fresh.append(run)
    return {"full": full, "recent": recent, "validation": validation, "fresh": fresh}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "params": run["params"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def gate_confirmation(
    confirmation: dict[str, Any],
    r543: Any,
) -> dict[str, Any]:
    full = confirmation["full"]["2x"]
    recent = confirmation["recent"]["2x"]
    validation = confirmation["validation"]
    fresh_values = [row["stats"]["total_return"] for row in confirmation["fresh"]]
    stress_3x = {
        **confirmation["full"]["3x"]["stats"],
        "recent_total_return": confirmation["recent"]["3x"]["stats"]["total_return"],
    }
    warning_5x = {
        **confirmation["full"]["5x"]["stats"],
        "recent_total_return": confirmation["recent"]["5x"]["stats"]["total_return"],
    }
    gate = r543.candidate_gate(
        full=full["stats"],
        recent=recent["stats"],
        validation=validation["stats"],
        fresh_values=fresh_values,
        stress_3x=stress_3x,
        warning_5x=warning_5x,
    )
    if float(warning_5x["recent_total_return"]) < 0:
        gate["five_x_warnings"].append("negative_5x_recent")
    return gate


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    r543 = _load_module("research_r543_stable_ic_subset")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    source = json.loads(R543_RESULT.read_text(encoding="utf-8"))
    if source.get("conclusion") != "watch_only":
        raise RuntimeError("R543 source conclusion changed")
    if set(source["metrics"]["members"]) != set(EXPECTED_MEMBERS):
        raise RuntimeError("R543 member set changed")
    _representatives, panel = r543.load_candidate(runtime, r542)
    grid = parameter_grid()
    prepared = {}
    rows = []
    for hold in HOLD_DAYS:
        for stop_variant, stop_loss in STOPS:
            key = (hold, stop_variant)
            prepared[key] = prepare_panel(
                p5,
                service,
                runtime,
                panel,
                hold=hold,
                params={"stop_loss": stop_loss},
                start=TRAIN_START,
                end=TRAIN_END,
            )
    for params in grid:
        key = (int(params["max_hold_days"]), str(params["stop_variant"]))
        run = run_window(
            p5,
            service,
            prepared[key],
            runtime,
            params=params,
            label=f"grid_{params['max_positions']}_{params['max_hold_days']}_{params['industry_cap']}_{params['stop_variant']}",
            start=TRAIN_START,
            end=TRAIN_END,
            cost_label="2x",
        )
        row = {
            **run,
            **params,
            "params": params,
            "eligible": training_eligible(run),
        }
        rows.append(row)
        print("grid", parameter_key(params), run["stats"]["annual_return"], run["stats"]["sharpe"], flush=True)
    if len(rows) != 108:
        raise RuntimeError(f"R544 grid expected 108 runs, got {len(rows)}")
    platform = select_plateau(rows, limit=PLATFORM_FINALISTS)
    if not platform:
        raise RuntimeError("R544 no eligible plateau")
    finalists = []
    for index, row in enumerate(platform):
        row["bootstrap"] = runtime["r533"].bootstrap_curve(
            row["curve"], seed=BOOTSTRAP_SEED + index
        )
        finalists.append(row)
    selected = select_bootstrap_finalist(finalists)
    confirmation = run_confirmation(p5, service, runtime, panel, selected)
    formal_runs = [*rows, *confirmation["full"].values(), *confirmation["recent"].values(), confirmation["validation"], *confirmation["fresh"]]
    if len(formal_runs) != 121:
        raise RuntimeError(f"R544 expected 121 formal runs, got {len(formal_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in formal_runs
    )
    gate = gate_confirmation(confirmation, r543)
    gate["grid_complete"] = len(rows) == 108
    gate["platform_finalists"] = len(platform) == PLATFORM_FINALISTS
    gate["formal_evidence"] = evidence_ok
    if not evidence_ok or not gate["grid_complete"] or not gate["platform_finalists"]:
        gate["conclusion"] = "rejected"
        gate["failed_gates"].append("evidence_or_grid_completeness")
    if not evidence_ok:
        gate["failed_gates"].append("formal_evidence")
    if not gate["grid_complete"]:
        gate["failed_gates"].append("grid_complete")
    if not gate["platform_finalists"]:
        gate["failed_gates"].append("platform_finalists")
    source_r543 = {
        "conclusion": source["conclusion"],
        "members": source["metrics"]["members"],
        "full_2x": source["metrics"]["full"]["2x"],
        "recent_2x": source["metrics"]["recent"]["2x"],
    }
    selected_public = public_grid_row({**selected, "params": selected["params"]})
    comparison = {
        "r543_default_full_2x": source_r543["full_2x"],
        "r543_default_recent_2x": source_r543["recent_2x"],
        "selected_full_2x": confirmation["full"]["2x"]["stats"],
        "selected_recent_2x": confirmation["recent"]["2x"]["stats"],
        "full_annual_delta_vs_r543_default": round(
            float(confirmation["full"]["2x"]["stats"]["annual_return"])
            - float(source_r543["full_2x"]["stats"]["annual_return"]),
            8,
        ),
        "recent_return_delta_vs_r543_default": round(
            float(confirmation["recent"]["2x"]["stats"]["total_return"])
            - float(source_r543["recent_2x"]["stats"]["total_return"]),
            8,
        ),
    }
    benchmarks = json.loads(R542_RESULT.read_text(encoding="utf-8"))["metrics"]["benchmarks"]
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "members": list(EXPECTED_MEMBERS),
        "grid": [public_grid_row(row) for row in rows],
        "platform_finalists": [public_grid_row(row) for row in platform],
        "selected": selected_public,
        "confirmation": {
            "full": {cost: _public_run(run) for cost, run in confirmation["full"].items()},
            "recent": {cost: _public_run(run) for cost, run in confirmation["recent"].items()},
            "validation": _public_run(confirmation["validation"]),
            "fresh_starts": [_public_run(run) for run in confirmation["fresh"]],
        },
        "bootstrap": {
            "platform_finalists": [row["bootstrap"] for row in platform],
            "full_2x": runtime["r533"].bootstrap_curve(confirmation["full"]["2x"]["curve"], seed=54401),
            "recent_2x": runtime["r533"].bootstrap_curve(confirmation["recent"]["2x"]["curve"], seed=54402),
        },
        "comparison": comparison,
        "benchmarks": benchmarks,
        "gate": gate,
        "formal_runs": [public_grid_row(run) if "params" in run else _public_run(run) for run in formal_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "R543 recent-year results were already observed; R544 recent is not independent OOS.",
            "Parameter selection uses training-only 2x data; validation and recent are confirmation only.",
            "Neighborhood statistics describe local sensitivity, not causal parameter effects.",
            "The selected parameter point can still be affected by the earlier R542/R543 observation process.",
            "Five-times cost is warning-only and no leverage is used.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": [] if gate["conclusion"] == "watch_only" else gate["failed_gates"],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    confirmation = payload["confirmation"]
    full = confirmation["full"]["2x"]["stats"]
    recent = confirmation["recent"]["2x"]["stats"]
    validation = confirmation["validation"]["stats"]
    lines = [
        "# R544 稳定 7 因子参数平台与 Monte Carlo",
        "",
        "> recent 已观察，本轮最多 `watch_only`；参数选择只使用训练期。",
        "",
        "## 选中参数",
        "",
        f"- 参数：`{selected['parameters']}`",
        f"- 邻域统计：`{selected['neighbor_stats']}`",
        f"- 训练 bootstrap：`{selected['bootstrap']}`",
        "",
        "## 确认结果",
        "",
        "| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| full 2x | {full['annual_return']:.2%} | {full['sharpe']:.2f} | {full['max_drawdown']:.2%} | {full['total_return']:.2%} | {int(full['n_trades'])} |",
        f"| recent 2x | {recent['annual_return']:.2%} | {recent['sharpe']:.2f} | {recent['max_drawdown']:.2%} | {recent['total_return']:.2%} | {int(recent['n_trades'])} |",
        f"| validation 2x | {validation['annual_return']:.2%} | {validation['sharpe']:.2f} | {validation['max_drawdown']:.2%} | {validation['total_return']:.2%} | {int(validation['n_trades'])} |",
        "",
        "## 与 R543 默认参数取舍",
        "",
        f"- Full 年化差：{payload['comparison']['full_annual_delta_vs_r543_default']:+.2%}",
        f"- Recent 收益差：{payload['comparison']['recent_return_delta_vs_r543_default']:+.2%}",
        "",
        "## 成本压力",
        "",
        "| 成本 | Full 年化 | Full Sharpe | Full MDD | Recent 总收益 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for cost in ("2x", "3x", "5x"):
        s = confirmation["full"][cost]["stats"]
        r = confirmation["recent"][cost]["stats"]
        lines.append(
            f"| {cost} | {s['annual_return']:.2%} | {s['sharpe']:.2f} | "
            f"{s['max_drawdown']:.2%} | {r['total_return']:.2%} |"
        )
    lines += [
        "",
        "## 平台 Finalists",
        "",
        "| # | 参数 | 邻居通过率 | 邻居 Sharpe 中位数 | Bootstrap p05 |",
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(payload["platform_finalists"], start=1):
        lines.append(
            f"| {index} | `{row['parameters']}` | {row['neighbor_stats']['pass_rate']:.2%} | "
            f"{row['neighbor_stats']['median_sharpe']:.2f} | {row['bootstrap']['total_p05']:.2%} |"
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
    result = ResearchResultEnvelope.model_validate({
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
            "members": payload["members"],
            "grid": payload["grid"],
            "platform_finalists": payload["platform_finalists"],
            "selected": payload["selected"],
            "confirmation": payload["confirmation"],
            "bootstrap": payload["bootstrap"],
            "comparison": payload["comparison"],
            "benchmarks": payload["benchmarks"],
            "formal_runs": payload["formal_runs"],
        },
        "source_result_hashes": [
            sha256(R543_RESULT),
            sha256(R542_RESULT),
            sha256(R539_RESULT),
        ],
        "formal_evidence": payload["confirmation"]["full"]["2x"]["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r544_stable7_parameter_mc_published",
    )
    return published.__dict__


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
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _verify_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R544 results already exist; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selected": payload["selected"]["parameters"],
        "full_2x": payload["confirmation"]["full"]["2x"]["stats"],
        "recent_2x": payload["confirmation"]["recent"]["2x"]["stats"],
        "validation_2x": payload["confirmation"]["validation"]["stats"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
