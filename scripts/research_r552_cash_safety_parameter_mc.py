#!/usr/bin/env python3
"""R552C: standardized parameter Monte Carlo for the frozen R550 cash_safety score."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.standard_parameter_mc import (  # noqa: E402
    build_standard_parameter_artifacts,
    select_standard_parameter,
)
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
)

RESEARCH_TYPE = "statistical_screen"
RESEARCH_ID = "R552C"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r552c_cash_safety_parameter_mc_preregistration.json"
)
RUNNER = "scripts/research_r552_cash_safety_parameter_mc.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R550_RESULT = RESEARCH / "r550_cash_safety_confirmation_result.json"
R552_ATTEMPT1 = RESEARCH / "r552_attempt1_signature_mismatch_failure.json"
R552_ATTEMPT3 = RESEARCH / "r552b_attempt3_recent_mapping_failure.json"
R552_ATTEMPT4 = RESEARCH / "r552c_attempt4_validation_receipt_failure.json"
R551_RESULT = RESEARCH / "r551a_r550_gbm_stress_result.json"
R549_RESULT = RESEARCH / "r549b_quality_addon_portfolio_result.json"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
PREREG = RESEARCH / "r552c_cash_safety_parameter_mc_preregistration.json"
RESULT = RESEARCH / "r552c_cash_safety_parameter_mc_result.json"
MARKDOWN = RESEARCH / "r552c_cash_safety_parameter_mc_result.md"
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
POSITIONS = (8, 10, 12)
HOLD_DAYS = (40, 60, 80)
VOL_TARGETS = (0.10, 0.12, 0.14)
STOP = -0.12
FIXED_ADDONS = ("cf_r547_cash_quality", "cf_r547_value_quality_safety")


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


def parameter_grid() -> list[dict[str, Any]]:
    return [
        {
            "parameter": f"p{positions}_h{hold_days}_v{round(vol_target * 100)}",
            "positions": positions,
            "hold_days": hold_days,
            "vol_target": vol_target,
            "stop_loss": STOP,
        }
        for positions in POSITIONS
        for hold_days in HOLD_DAYS
        for vol_target in VOL_TARGETS
    ]


def _vol_variant(target: float) -> dict[str, Any]:
    return {
        "id": f"vol_{round(target * 100)}",
        "kind": "vol_target",
        "target": target,
        "lookback": 60,
        "floor": 0.50,
        "order": round(target * 100),
    }


def _variant_id(target: float) -> str:
    return _vol_variant(target)["id"]


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "curve": run["curve"],
        "formal_evidence": run["formal_evidence"],
    }


def _run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    positions: int,
    hold_days: int,
    vol_target: float,
    start: date,
    end: date,
    cost_label: str,
    label: str,
) -> dict[str, Any]:
    r545 = _load_module("research_r545_stable7_risk_budget")
    r533 = runtime["r533"]
    seed = p5.portfolio._backtest_config(
        {"score": 1.0},
        {"score": "high"},
        start=start,
        end=end,
        max_positions=positions,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=hold_days,
    )
    seed.symbols = list(runtime["symbols"])
    seed.hs300 = False
    seed.overrides = {**(seed.overrides or {}), "stop_loss": STOP, "trailing_stop": None}
    prepared_base = service.prepare_matrix_optimization([seed])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, ["score", "core_score"])
    config = r533.build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=positions,
        max_hold_days=hold_days,
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {**(config.overrides or {}), "stop_loss": STOP, "trailing_stop": None}
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": 3,
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "policy"
    config.position_policy = _variant_id(vol_target)
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R552C:{label}: prepared signature mismatch")
    with r545.policy_injection(_vol_variant(vol_target)):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R552C:{label}")
        if result.error:
            raise RuntimeError(f"R552C:{label}: {result.error}")
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
        "formal_evidence": r533._evidence_summary(evidence),
    }


def _plateau_flags(points: list[dict[str, Any]]) -> None:
    by_key = {
        (row["params"]["positions"], row["params"]["hold_days"], row["params"]["vol_target"]): row
        for row in points
    }
    for row in points:
        p = row["params"]
        neighbours = []
        for axis, values in (("positions", POSITIONS), ("hold_days", HOLD_DAYS), ("vol_target", VOL_TARGETS)):
            index = values.index(p[axis])
            for neighbour_index in (index - 1, index + 1):
                if not 0 <= neighbour_index < len(values):
                    continue
                other = dict(p)
                other[axis] = values[neighbour_index]
                neighbour = by_key.get((other["positions"], other["hold_days"], other["vol_target"]))
                if neighbour is not None:
                    neighbours.append(neighbour)
        annual = float(row["train"]["stats"]["annual_return"])
        row["plateau_ok"] = True if not neighbours else all(
            float(neighbour["train"]["stats"]["annual_return"]) >= max(0.0, annual * 0.70)
            for neighbour in neighbours
        )


def scientific_design() -> dict[str, Any]:
    return {
        "fixed_variant": "cash_safety",
        "grid": {
            "positions": list(POSITIONS),
            "hold_days": list(HOLD_DAYS),
            "vol_targets": list(VOL_TARGETS),
        },
        "grid_size": len(parameter_grid()),
        "selection_policy": "train_select_validation_confirm",
        "recent_is_diagnostic_only": True,
        "center": {"positions": 10, "hold_days": 60, "vol_target": 0.12},
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {
                "required": False,
                "status": "not_applicable",
                "reason": "R551A already owns strategy-level GBM stress for the frozen R550 score",
            },
            "parameter_mc": {
                "required": True,
                "status": "complete",
                "selection_policy": "train_select_validation_confirm",
            },
        },
        "gates": {
            "min_train_annual": 0.08,
            "max_mdd": -0.20,
            "min_trades": 20,
            "validation_positive": True,
            "plateau_required": True,
            "cost_monotonic_required": True,
        },
        "execution": {
            "matching": "open_t+1",
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


def _input_paths(root: Path) -> dict[str, Path]:
    return {
        "r550_result": root / R550_RESULT.relative_to(ROOT),
        "r551_result": root / R551_RESULT.relative_to(ROOT),
        "r549_result": root / R549_RESULT.relative_to(ROOT),
        "r547_result": root / R547_RESULT.relative_to(ROOT),
        "r552_attempt1": root / R552_ATTEMPT1.relative_to(ROOT),
        "r552_attempt3": root / R552_ATTEMPT3.relative_to(ROOT),
        "r552_attempt4": root / R552_ATTEMPT4.relative_to(ROOT),
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R552C preregistration already exists: {PREREG}")
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Which R550 cash_safety parameter is robust under a standardized 27-point "
            "train/validation parameter Monte Carlo while preserving the frozen factor score?"
        ),
        "hypothesis": (
            "A plateau around the R550 parameters will outperform single-point overfit choices "
            "when selection uses training and validation only."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": {
            **scientific_design(),
            "parameter_grid": parameter_grid(),
        },
        "sample": {
            "train_start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "execution": scientific_design()["execution"],
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "grid_frozen_before_run",
            "selection_uses_train_and_validation_only",
            "recent_not_used_for_selection",
            "plateau_and_cost_constraints",
            "charts_sidecar_bound",
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
        raise RuntimeError("R552C runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R552C input changed: {name}")


def _score_panel(root: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    r542 = _load_module("research_r542_recent_failure_attribution")
    r547 = _load_module("research_r547_incremental_factor_screen")
    r549 = _load_module("research_r549_quality_addon_portfolio")
    runtime = r542.load_runtime(root)
    membership = r547._load_membership(root)
    candidate_panel = r547._load_price_panel(root, membership)
    candidate_panel = r547._add_candidate_sources(
        candidate_panel,
        r547.load_candidate_definitions(root),
    )
    return r549.build_variant_panel(runtime, candidate_panel, FIXED_ADDONS), runtime["industries"]


def build_payload(
    *,
    selection: dict[str, Any],
    points: list[dict[str, Any]],
    selected_full: dict[str, Any],
    selected_recent: dict[str, Any],
    selected_validation: dict[str, Any],
    fresh: list[dict[str, Any]],
    artifacts: list[dict[str, str]],
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    selected = selection["selected"]
    if selected is None:
        conclusion = "rejected"
        gate = {"pass": False, "failed": ["no_eligible_parameter"]}
    else:
        gate = _final_gate(selected_full, selected_recent["2x"], selected_validation, fresh)
        conclusion = "watch_only" if gate["pass"] else "rejected"
    parameter_receipt = {
        **selection,
        "status": "complete",
        "bootstrap": bootstrap,
        "recent_used_for_selection": False,
        "constraints": {
            **(selection.get("constraints") or {}),
            "validation_confirmed": selected is not None,
        },
    }
    standard = build_standard_validation_receipt(
        {
            "status": "not_applicable",
            "reason": "R551A owns the strategy-level GBM stress for the frozen R550 score",
        },
        parameter_receipt,
        artifacts=artifacts,
    )
    checked = check_standard_validation_constraints(standard, research_type=RESEARCH_TYPE)
    if not checked["pass"]:
        raise RuntimeError(f"R552C standard validation failed: {checked['errors']}")
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "selection": selection,
        "points": points,
        "selected_full": _public_run(selected_full),
        "selected_recent": {cost: _public_run(run) for cost, run in selected_recent.items()},
        "selected_validation": _public_run(selected_validation),
        "fresh_starts": [_public_run(run) for run in fresh],
        "bootstrap": bootstrap,
        "gate": gate,
        "standard_validation": standard,
        "conclusion": conclusion,
        "known_limits": [
            "R552C selects parameters only on training and validation; recent is diagnostic only.",
            "The R550 score panel is frozen, so this is parameter selection, not factor re-discovery.",
            "GBM is owned by R551A; this parameter screen does not claim a separate GBM result.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": [] if conclusion == "watch_only" else gate.get("failed", []),
        "output_policy": scientific_design()["output_policy"],
    }


def _final_gate(full: dict[str, Any], recent: dict[str, Any], validation: dict[str, Any], fresh: list[dict[str, Any]]) -> dict[str, Any]:
    full_stats = full["stats"]
    recent_stats = recent["stats"]
    val_stats = validation["stats"]
    values = [float(run["stats"]["total_return"]) for run in fresh]
    positive = sum(value > 0 for value in values)
    checks = {
        "full_annual_min": float(full_stats.get("annual_return") or 0.0) >= 0.10,
        "full_sharpe_min": float(full_stats.get("sharpe") or 0.0) >= 0.90,
        "full_mdd_min": float(full_stats.get("max_drawdown") or 0.0) >= -0.18,
        "recent_positive": float(recent_stats.get("total_return") or 0.0) > 0.0,
        "validation_positive": float(val_stats.get("annual_return") or 0.0) > 0.0,
        "fresh_positive": positive >= 4 and (not values or float(np.median(values)) > 0.0),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "failed": [key for key, value in checks.items() if not value],
        "fresh_positive_count": positive,
        "fresh_median": float(np.median(values)) if values else None,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    lines = [
        "# R552C cash_safety 参数 Monte Carlo",
        "",
        "> 训练选参、验证确认、近期只披露；不是独立样本，也不是生产授权。",
        "",
        "## 网格",
        "",
        f"- 位置：{payload['design']['grid']['positions']}",
        f"- 持有：{payload['design']['grid']['hold_days']}",
        f"- 波动目标：{payload['design']['grid']['vol_targets']}",
        f"- 点数：{payload['design']['grid_size']}",
        "",
        "## 选中参数",
        "",
        f"`{selected}`",
        "",
        "## 选中结果",
        "",
        f"- 全周期 2x：{payload['selected_full']['stats']}",
        f"- 近期 2x：{payload['selected_recent']['2x']['stats']}",
        f"- 验证 2x：{payload['selected_validation']['stats']}",
        f"- gate：{payload['gate']}",
        "",
        "## 图表",
        "",
        *[f"- {item['path']} (`{item['sha256'][:12]}...`)" for item in payload["standard_validation"]["artifacts"]],
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    panel, _industries = _score_panel(root)
    points: list[dict[str, Any]] = []
    for spec in parameter_grid():
        train = _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=spec["positions"],
            hold_days=spec["hold_days"],
            vol_target=spec["vol_target"],
            start=START,
            end=TRAIN_END,
            cost_label="2x",
            label=f"train_{spec['parameter']}",
        )
        train_3x = _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=spec["positions"],
            hold_days=spec["hold_days"],
            vol_target=spec["vol_target"],
            start=START,
            end=TRAIN_END,
            cost_label="3x",
            label=f"train3x_{spec['parameter']}",
        )
        validation = _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=spec["positions"],
            hold_days=spec["hold_days"],
            vol_target=spec["vol_target"],
            start=VALIDATION_START,
            end=VALIDATION_END,
            cost_label="2x",
            label=f"validation_{spec['parameter']}",
        )
        points.append(
            {
                "parameter": spec["parameter"],
                "params": spec,
                "train": train,
                "train_3x": train_3x,
                "validation": validation,
                "cost_monotonic": float(train["stats"]["annual_return"])
                >= float(train_3x["stats"]["annual_return"]),
            }
        )
    _plateau_flags(points)
    selection = select_standard_parameter(
        points,
        {
            "min_train_annual": 0.08,
            "max_mdd": -0.20,
            "min_trades": 20,
            "require_validation_positive": True,
            "require_plateau": True,
            "require_cost_monotonic": True,
            "center": {"positions": 10, "hold_days": 60, "vol_target": 0.12},
        },
    )
    selected = selection["selected"]
    if selected is None:
        raise RuntimeError("R552C no parameter passed standard train/validation constraints")
    params = selected["params"]
    selected_full = {}
    for cost in ("2x", "3x", "5x"):
        selected_full[cost] = _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=params["positions"],
            hold_days=params["hold_days"],
            vol_target=params["vol_target"],
            start=START,
            end=END,
            cost_label=cost,
            label=f"selected_full_{cost}",
        )
    selected_recent = {}
    for cost in ("2x", "3x", "5x"):
        selected_recent[cost] = _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=params["positions"],
            hold_days=params["hold_days"],
            vol_target=params["vol_target"],
            start=RECENT_START,
            end=END,
            cost_label=cost,
            label=f"selected_recent_{cost}",
        )
    fresh = [
        _run_window(
            p5,
            service,
            runtime,
            panel,
            positions=params["positions"],
            hold_days=params["hold_days"],
            vol_target=params["vol_target"],
            start=start,
            end=END,
            cost_label="2x",
            label=f"fresh_{start.isoformat()}",
        )
        for start in FRESH_STARTS
    ]
    bootstrap = {
        "full_2x": runtime["r533"].bootstrap_curve(selected_full["2x"]["curve"], seed=55201),
        "recent_2x": runtime["r533"].bootstrap_curve(selected_recent["2x"]["curve"], seed=55202),
    }
    with __import__("tempfile").TemporaryDirectory(prefix="r552_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        artifacts = build_standard_parameter_artifacts(
            selection,
            plot_dir,
            prefix="r552_r550_parameter",
        )
        attachments = [
            (item["path"], (plot_dir / item["path"]).read_bytes())
            for item in artifacts
        ]
        payload = build_payload(
            selection=selection,
            points=points,
            selected_full=selected_full["2x"],
            selected_recent=selected_recent,
            selected_validation=next(
                row["validation"]
                for row in points
                if row["parameter"] == selected["parameter"]
            ),
            fresh=fresh,
            artifacts=artifacts,
            bootstrap=bootstrap,
        )
        published = _publish(payload, attachments)
    return {**payload, "published": published}


def _publish(payload: dict[str, Any], attachments: list[tuple[str, bytes]]) -> dict[str, Any]:
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
                "selection": payload["selection"],
                "points": payload["points"],
                "selected_full": payload["selected_full"],
                "selected_recent": payload["selected_recent"],
                "selected_validation": payload["selected_validation"],
                "fresh_starts": payload["fresh_starts"],
                "bootstrap": payload["bootstrap"],
                "standard_validation": payload["standard_validation"],
            },
            "source_result_hashes": [sha256(R550_RESULT), sha256(R551_RESULT)],
            "formal_evidence": payload["selected_full"]["formal_evidence"],
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
        operation="r552c_cash_safety_parameter_mc_published",
        attachments=attachments,
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
        raise FileExistsError("R552C result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selected": payload["selection"]["selected"],
        "standard_validation": payload["standard_validation"],
        "published_result": str(payload["published"].get("result_path")),
        "ledger_record_id": payload["published"].get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
