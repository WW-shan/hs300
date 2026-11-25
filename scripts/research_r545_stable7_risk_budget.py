#!/usr/bin/env python3
"""R545: causal risk-budget variants for the stable-seven factor portfolio."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.position_sizing import PositionSizingResult  # noqa: E402
from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r545_stable7_risk_budget_preregistration.json"
)
RESEARCH_ID = "R545"
RUNNER = "scripts/research_r545_stable7_risk_budget.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R543_RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
R543_PREREG = RESEARCH / "r543_stable_ic_subset_preregistration.json"
R543_RUNNER = ROOT / "scripts/research_r543_stable_ic_subset.py"
R544_RESULT = RESEARCH / "r544_stable7_parameter_mc_result.json"
R544_PREREG = RESEARCH / "r544_stable7_parameter_mc_preregistration.json"
R544_RUNNER = ROOT / "scripts/research_r544_stable7_parameter_mc.py"
R542_RESULT = RESEARCH / "r542_recent_failure_attribution_result.json"
R542_PREREG = RESEARCH / "r542_recent_failure_attribution_preregistration.json"
PREREG = RESEARCH / "r545_stable7_risk_budget_preregistration.json"
RESULT = RESEARCH / "r545_stable7_risk_budget_result.json"
MARKDOWN = RESEARCH / "r545_stable7_risk_budget_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
STOP_LOSS = -0.12
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
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
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 545


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


def risk_variants() -> list[dict[str, Any]]:
    return [
        {"id": "control", "kind": "control", "order": 0},
        {"id": "cash_80", "kind": "cash", "scale": 0.80, "order": 1},
        {"id": "cash_60", "kind": "cash", "scale": 0.60, "order": 2},
        {"id": "cash_40", "kind": "cash", "scale": 0.40, "order": 3},
        {"id": "dd_10", "kind": "drawdown", "threshold": -0.10, "defensive": 0.50, "order": 4},
        {"id": "dd_15", "kind": "drawdown", "threshold": -0.15, "defensive": 0.50, "order": 5},
        {"id": "vol_10", "kind": "vol_target", "target": 0.10, "lookback": 60, "floor": 0.50, "order": 6},
        {"id": "vol_12", "kind": "vol_target", "target": 0.12, "lookback": 60, "floor": 0.50, "order": 7},
        {"id": "vol_12_dd_10", "kind": "combined", "target": 0.12, "lookback": 60, "floor": 0.50, "threshold": -0.10, "defensive": 0.50, "order": 8},
    ]


def _portfolio_recent_return(ctx: Any, lookback: int) -> list[float]:
    series = [list(values) for values in ctx.recent_returns.values()]
    if not series:
        return []
    length = min(len(values) for values in series)
    if length == 0:
        return []
    length = min(length, int(lookback))
    return [
        st.mean(float(values[-length + index]) for values in series)
        for index in range(length)
    ]


def _vol_scale(ctx: Any, spec: dict[str, Any]) -> float:
    values = _portfolio_recent_return(ctx, int(spec["lookback"]))
    if len(values) < 2:
        return 1.0
    mean = st.mean(values)
    vol = math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    annual_vol = vol * math.sqrt(252.0)
    if annual_vol <= 1e-12:
        return 1.0
    return min(1.0, max(float(spec["floor"]), float(spec["target"]) / annual_vol))


def make_policy(spec: dict[str, Any]):
    kind = str(spec["kind"])

    def compute(ctx: Any) -> PositionSizingResult:
        n = len(ctx.selected)
        if n == 0:
            raise ValueError("risk policy requires at least one selected candidate")
        weights = [1.0 / n] * n
        if kind == "control":
            scale = 1.0
        elif kind == "cash":
            scale = float(spec["scale"])
        elif kind == "drawdown":
            scale = (
                float(spec["defensive"])
                if float(ctx.portfolio.drawdown) <= float(spec["threshold"])
                else 1.0
            )
        elif kind == "vol_target":
            scale = _vol_scale(ctx, spec)
        elif kind == "combined":
            scale = min(
                _vol_scale(ctx, spec),
                float(spec["defensive"])
                if float(ctx.portfolio.drawdown) <= float(spec["threshold"])
                else 1.0,
            )
        else:
            raise ValueError(f"unsupported risk variant: {kind}")
        return PositionSizingResult(weights=weights, budget_scale=min(1.0, max(0.01, scale)))

    return compute


@contextmanager
def policy_injection(variant: dict[str, Any]):
    """Bind one research policy id to the project's normal resolver."""
    from app.backtest import strategy as strategy_module

    original = strategy_module._resolve_position_policy

    def resolver(config: Any):
        if (
            getattr(config, "position_sizing", None) == "policy"
            and getattr(config, "position_policy", None) == variant["id"]
        ):
            return make_policy(variant), dict(getattr(config, "position_policy_params", {}) or {})
        return original(config)

    strategy_module._resolve_position_policy = resolver
    try:
        yield
    finally:
        strategy_module._resolve_position_policy = original


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


def select_training_variant(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if training_eligible(row)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -float(row["stats"]["max_drawdown"]),
            -float(row["stats"]["annual_return"]),
            -float(row["stats"]["sharpe"]),
            int(row["variant"]["order"]),
        ),
    )


def scientific_design() -> dict[str, Any]:
    return {
        "members": list(EXPECTED_MEMBERS),
        "variants": len(risk_variants()),
        "confirmation_runs": 13,
        "formal_runs": len(risk_variants()) + 13,
        "selection_window": {
            "start": str(TRAIN_START),
            "end": str(TRAIN_END),
        },
        "confirmation_windows": {
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "variants_spec": risk_variants(),
        "selection_order": [
            "mdd_desc",
            "annual_desc",
            "sharpe_desc",
            "variant_order_asc",
        ],
        "bootstrap_runs": BOOTSTRAP_RUNS,
        "bootstrap_block": BOOTSTRAP_BLOCK,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "recent_is_independent_oos": False,
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "position_sizing": "policy_budget_scale",
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
        raise FileExistsError(f"R545 preregistration already exists: {prereg}")
    sources = {
        "r543_result": root / R543_RESULT.relative_to(ROOT),
        "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
        "r543_runner": root / R543_RUNNER.relative_to(ROOT),
        "r544_result": root / R544_RESULT.relative_to(ROOT),
        "r544_preregistration": root / R544_PREREG.relative_to(ROOT),
        "r544_runner": root / R544_RUNNER.relative_to(ROOT),
        "r542_result": root / R542_RESULT.relative_to(ROOT),
        "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Can a causal, no-leverage budget scale improve the drawdown and "
            "validation profile of the R543 stable-seven portfolio?"
        ),
        "hypothesis": (
            "A drawdown or volatility budget will reduce tail risk while retaining "
            "positive recent performance and stress-cost viability."
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
            "training_variants": 9,
            "confirmation_runs": 13,
            "formal_runs": 22,
            "training_only_selection": True,
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "position_context_contains_only_causal_inputs",
            "budget_scale_never_exceeds_one",
            "training_only_risk_variant_selection",
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
        raise RuntimeError("R545 runner hash no longer matches preregistration")
    for name, expected in prereg["input_hashes"].items():
        path = {
            "r543_result": root / R543_RESULT.relative_to(ROOT),
            "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
            "r543_runner": root / R543_RUNNER.relative_to(ROOT),
            "r544_result": root / R544_RESULT.relative_to(ROOT),
            "r544_preregistration": root / R544_PREREG.relative_to(ROOT),
            "r544_runner": root / R544_RUNNER.relative_to(ROOT),
            "r542_result": root / R542_RESULT.relative_to(ROOT),
            "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
        }[name]
        if sha256(path) != expected:
            raise RuntimeError(f"R545 input changed: {name}")


def prepare_panel(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: Any,
    *,
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
        max_hold_days=MAX_HOLD_DAYS,
    )
    seed.symbols = list(runtime["symbols"])
    seed.hs300 = False
    seed.overrides = {
        **(seed.overrides or {}),
        "stop_loss": STOP_LOSS,
        "trailing_stop": None,
    }
    prepared_base = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared_base,
        panel,
        ["score", "core_score"],
    )


def run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    prepared: Any,
    *,
    variant: dict[str, Any],
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
    if variant["kind"] == "control":
        config.position_sizing = "equal"
        config.position_policy = None
        config.position_policy_params = None
    else:
        config.position_sizing = "policy"
        config.position_policy = str(variant["id"])
        config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R545:{label}: prepared signature mismatch")
    with policy_injection(variant):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R545:{label}")
    if result.error:
        raise RuntimeError(f"R545:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "variant": variant,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "variant": run["variant"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def run_confirmation(
    runtime: dict[str, Any],
    p5: Any,
    service: Any,
    panel: Any,
    selected: dict[str, Any],
) -> dict[str, Any]:
    def execute(label: str, start: date, end: date, cost: str) -> dict[str, Any]:
        prepared = prepare_panel(p5, service, runtime, panel, start=start, end=end)
        return run_window(
            p5,
            service,
            runtime,
            prepared,
            variant=selected["variant"],
            label=label,
            start=start,
            end=end,
            cost_label=cost,
        )

    full = {
        cost: execute(f"full_{selected['variant']['id']}_{cost}", TRAIN_START, END, cost)
        for cost in ("2x", "3x", "5x")
    }
    recent = {
        cost: execute(f"recent_{selected['variant']['id']}_{cost}", RECENT_START, END, cost)
        for cost in ("2x", "3x", "5x")
    }
    validation = execute(
        f"validation_{selected['variant']['id']}_2x",
        VALIDATION_START,
        VALIDATION_END,
        "2x",
    )
    fresh = [
        execute(f"fresh_{start.isoformat()}_2x", start, END, "2x")
        for start in FRESH_STARTS
    ]
    return {"full": full, "recent": recent, "validation": validation, "fresh": fresh}


def gate_candidate(confirmation: dict[str, Any], r543: Any) -> dict[str, Any]:
    full = confirmation["full"]["2x"]["stats"]
    recent = confirmation["recent"]["2x"]["stats"]
    validation = confirmation["validation"]["stats"]
    fresh_values = [run["stats"]["total_return"] for run in confirmation["fresh"]]
    stress_3x = {
        **confirmation["full"]["3x"]["stats"],
        "recent_total_return": confirmation["recent"]["3x"]["stats"]["total_return"],
    }
    warning_5x = {
        **confirmation["full"]["5x"]["stats"],
        "recent_total_return": confirmation["recent"]["5x"]["stats"]["total_return"],
    }
    gate = r543.candidate_gate(
        full=full,
        recent=recent,
        validation=validation,
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
    training = []
    prepared_train = prepare_panel(
        p5,
        service,
        runtime,
        panel,
        start=TRAIN_START,
        end=TRAIN_END,
    )
    for variant in risk_variants():
        run = run_window(
            p5,
            service,
            runtime,
            prepared_train,
            variant=variant,
            label=f"train_{variant['id']}",
            start=TRAIN_START,
            end=TRAIN_END,
            cost_label="2x",
        )
        run["eligible"] = training_eligible(run)
        training.append(run)
        print("train", variant["id"], run["stats"]["annual_return"], run["stats"]["sharpe"], flush=True)
    if len(training) != 9:
        raise RuntimeError(f"R545 expected 9 training variants, got {len(training)}")
    selected = select_training_variant(training)
    if selected is None:
        raise RuntimeError("R545 no risk variant passed training eligibility")
    confirmation = run_confirmation(runtime, p5, service, panel, selected)
    formal_runs = [
        *training,
        *confirmation["full"].values(),
        *confirmation["recent"].values(),
        confirmation["validation"],
        *confirmation["fresh"],
    ]
    if len(formal_runs) != 22:
        raise RuntimeError(f"R545 expected 22 formal runs, got {len(formal_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in formal_runs
    )
    gate = gate_candidate(confirmation, r543)
    gate["training_variants_complete"] = len(training) == 9
    gate["formal_evidence"] = evidence_ok
    if not evidence_ok or len(training) != 9:
        gate["conclusion"] = "rejected"
        gate["failed_gates"].append("evidence_or_training_completeness")
    full_2x = confirmation["full"]["2x"]
    recent_2x = confirmation["recent"]["2x"]
    full_2x["bootstrap"] = runtime["r533"].bootstrap_curve(full_2x["curve"], seed=54501)
    recent_2x["bootstrap"] = runtime["r533"].bootstrap_curve(recent_2x["curve"], seed=54502)
    comparison = {
        "r543_full_2x": source["metrics"]["full"]["2x"],
        "r543_recent_2x": source["metrics"]["recent"]["2x"],
        "selected_full_2x": full_2x["stats"],
        "selected_recent_2x": recent_2x["stats"],
        "full_annual_delta_vs_r543": round(
            float(full_2x["stats"]["annual_return"])
            - float(source["metrics"]["full"]["2x"]["stats"]["annual_return"]),
            8,
        ),
        "recent_return_delta_vs_r543": round(
            float(recent_2x["stats"]["total_return"])
            - float(source["metrics"]["recent"]["2x"]["stats"]["total_return"]),
            8,
        ),
        "full_mdd_delta_vs_r543": round(
            float(full_2x["stats"]["max_drawdown"])
            - float(source["metrics"]["full"]["2x"]["stats"]["max_drawdown"]),
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
        "training": [public_run(run) | {"eligible": run["eligible"]} for run in training],
        "selected": public_run(selected),
        "confirmation": {
            "full": {cost: public_run(run) for cost, run in confirmation["full"].items()},
            "recent": {cost: public_run(run) for cost, run in confirmation["recent"].items()},
            "validation": public_run(confirmation["validation"]),
            "fresh_starts": [public_run(run) for run in confirmation["fresh"]],
        },
        "bootstrap": {
            "full_2x": full_2x["bootstrap"],
            "recent_2x": recent_2x["bootstrap"],
        },
        "comparison": comparison,
        "benchmarks": benchmarks,
        "gate": gate,
        "formal_runs": [public_run(run) | ({"eligible": run["eligible"]} if "eligible" in run else {}) for run in formal_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "R543 recent-year results were already observed; R545 recent is not independent OOS.",
            "Risk-budget variants are selected only on training-period metrics.",
            "Volatility targeting uses only the causal selected-candidate recent-return window.",
            "The PIT equal-weight benchmark is a costless diagnostic approximation.",
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
        "# R545 稳定 7 因子风险预算与动态敞口",
        "",
        "> 固定因子与执行参数，只改变无杠杆 budget_scale；recent 已观察。",
        "",
        "## 选中风险政策",
        "",
        f"- 变体：`{selected['variant']}`",
        f"- Full 2×：年化 {full['annual_return']:.2%}，Sharpe {full['sharpe']:.2f}，MDD {full['max_drawdown']:.2%}，交易 {int(full['n_trades'])}",
        f"- Recent 2×：年化 {recent['annual_return']:.2%}，Sharpe {recent['sharpe']:.2f}，MDD {recent['max_drawdown']:.2%}，交易 {int(recent['n_trades'])}",
        f"- Validation 2×：年化 {validation['annual_return']:.2%}，Sharpe {validation['sharpe']:.2f}，MDD {validation['max_drawdown']:.2%}",
        "",
        "## 与 R543 对照",
        "",
        f"- Full 年化差：{payload['comparison']['full_annual_delta_vs_r543']:+.2%}",
        f"- Recent 收益差：{payload['comparison']['recent_return_delta_vs_r543']:+.2%}",
        f"- Full MDD 差：{payload['comparison']['full_mdd_delta_vs_r543']:+.2%}",
        "",
        "## 训练期变体",
        "",
        "| 变体 | 年化 | Sharpe | MDD | 交易数 | 资格 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["training"]:
        stats = row["stats"]
        lines.append(
            f"| {row['variant']['id']} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} | "
            f"{stats['max_drawdown']:.2%} | {int(stats['n_trades'])} | {row['eligible']} |"
        )
    lines += [
        "",
        "## 确认成本",
        "",
        "| 成本 | Full 年化 | Full Sharpe | Full MDD | Recent 总收益 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for cost in ("2x", "3x", "5x"):
        stats = confirmation["full"][cost]["stats"]
        recent_stats = confirmation["recent"][cost]["stats"]
        lines.append(
            f"| {cost} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} | "
            f"{stats['max_drawdown']:.2%} | {recent_stats['total_return']:.2%} |"
        )
    lines += [
        "",
        "## Fresh starts",
        "",
        "| 起始日 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for run in confirmation["fresh_starts"]:
        stats = run["stats"]
        lines.append(
            f"| {run['start']} | {stats['total_return']:.2%} | {stats['annual_return']:.2%} | "
            f"{stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- Core：{payload['gate']['core_pass']}",
        f"- 3× stress：{payload['gate']['stress_pass']}",
        f"- Fresh-start：{payload['gate']['fresh_start']}",
        f"- 5× warnings：{payload['gate']['five_x_warnings']}",
        f"- 失败项：{payload['gate']['failed_gates']}",
        f"- 结论：`{payload['conclusion']}`",
        "",
        "## Bootstrap",
        "",
        f"- Full 2×：{payload['bootstrap']['full_2x']}",
        f"- Recent 2×：{payload['bootstrap']['recent_2x']}",
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
            "training": payload["training"],
            "selected": payload["selected"],
            "confirmation": payload["confirmation"],
            "bootstrap": payload["bootstrap"],
            "comparison": payload["comparison"],
            "benchmarks": payload["benchmarks"],
            "formal_runs": payload["formal_runs"],
        },
        "source_result_hashes": [sha256(R543_RESULT), sha256(R544_RESULT), sha256(R542_RESULT)],
        "formal_evidence": payload["selected"]["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r545_stable7_risk_budget_published",
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
        raise FileExistsError("R545 results already exist; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selected": payload["selected"]["variant"],
        "full_2x": payload["confirmation"]["full"]["2x"]["stats"],
        "recent_2x": payload["confirmation"]["recent"]["2x"]["stats"],
        "validation_2x": payload["confirmation"]["validation"]["stats"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
