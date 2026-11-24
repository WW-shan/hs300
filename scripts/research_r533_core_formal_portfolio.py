#!/usr/bin/env python3
"""R533: formal T+1 portfolio validation of the frozen R531 factor core."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence
from app.research.gate_policy_v2 import evaluate_gate_policy_v2
from app.services.industry_map import load_industry_l1

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r533a_core_formal_portfolio_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"
R530_PREREG = RESEARCH / "r530_unified_factor_library_mc_preregistration.json"
R531_RESULT = RESEARCH / "r531_factor_library_core_result.json"
R531_PREREG = RESEARCH / "r531_factor_library_core_preregistration.json"
R532_RESULT = RESEARCH / "r532_factor_library_core_stability_result.json"
R532_PREREG = RESEARCH / "r532_factor_library_core_stability_preregistration.json"
R533_ATTEMPT1 = RESEARCH / "r533_attempt1_signature_mismatch_failure.json"
GATE_POLICY = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/gate_policy_v2.json"
PREREG = RESEARCH / "r533a_core_formal_portfolio_preregistration.json"
RESULT = RESEARCH / "r533a_core_formal_portfolio_result.json"
MARKDOWN = RESEARCH / "r533a_core_formal_portfolio_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
MAX_POSITIONS = (10, 20, 30)
HOLD_DAYS = (20, 30, 60)
TRAIN_ANNUAL_MIN = 0.03
TRAIN_SHARPE_MIN = 0.70
TRAIN_MDD_MIN = -0.30
TRAIN_TRADES_MIN = 20
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 533
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
CORE_FACTORS = frozenset({
    "amount_level",
    "atr_risk",
    "dividend_yield_absolute",
    "max_return",
    "turnover_level",
    "vol_of_vol",
})
CONTROL_FACTORS = ("dividend_yield_absolute", "atr_risk")


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


def _load_script_module(name: str) -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_frozen_inputs(root: Path) -> dict[str, Any]:
    root = Path(root)
    for relative in (
        R530_PREREG,
        R530_RESULT,
        R531_PREREG,
        R531_RESULT,
        R532_PREREG,
        R532_RESULT,
        R533_ATTEMPT1,
    ):
        verify_sidecar(root / relative.relative_to(ROOT))
    r532 = _load_script_module("research_r532_factor_library_core_stability")
    representatives, r531_payload = r532.load_frozen_core(root)
    if {row["family_id"] for row in representatives} != CORE_FACTORS:
        raise RuntimeError("R532 frozen representatives changed")
    return {
        "r532": r532,
        "representatives": representatives,
        "r531_payload": r531_payload,
        "r532_payload": json.loads(
            (root / R532_RESULT.relative_to(ROOT)).read_text(encoding="utf-8")
        ),
    }


def scaled_score_panel(core: pl.DataFrame) -> pl.DataFrame:
    return core.select(
        "symbol",
        "date",
        (pl.col("factor_value") * 100.0).alias("score"),
        "n_factors",
    )


def training_candidates() -> list[dict[str, Any]]:
    return [
        {
            "max_positions": positions,
            "max_hold_days": hold,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
        }
        for positions in MAX_POSITIONS
        for hold in HOLD_DAYS
    ]


def training_eligible(row: dict[str, Any]) -> bool:
    stats = row["stats"]
    bootstrap = row.get("bootstrap") or {}
    return (
        isinstance(stats.get("annual_return"), (int, float))
        and stats["annual_return"] >= TRAIN_ANNUAL_MIN
        and isinstance(stats.get("sharpe"), (int, float))
        and stats["sharpe"] >= TRAIN_SHARPE_MIN
        and isinstance(stats.get("max_drawdown"), (int, float))
        and stats["max_drawdown"] >= TRAIN_MDD_MIN
        and int(row.get("trade_count") or 0) >= TRAIN_TRADES_MIN
        and isinstance(bootstrap.get("total_p05"), (int, float))
        and bootstrap["total_p05"] > 0
    )


def select_training_candidate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if training_eligible(row)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -float(row["bootstrap"]["total_p05"]),
            -float(row["stats"]["sharpe"]),
            -float(row["stats"]["annual_return"]),
            int(row["max_positions"]),
            int(row["max_hold_days"]),
        ),
    )


def concentration_metrics(
    trades: list[dict[str, Any]],
    industries: dict[str, str],
    *,
    max_positions: int,
) -> dict[str, Any]:
    by_symbol: dict[str, float] = defaultdict(float)
    positive_by_symbol: dict[str, float] = defaultdict(float)
    loss_by_symbol: dict[str, float] = defaultdict(float)
    for trade in trades:
        symbol = str(trade.get("symbol") or "")
        pnl = float(trade.get("pnl_amount") or 0.0)
        by_symbol[symbol] += pnl
        if pnl > 0:
            positive_by_symbol[symbol] += pnl
        elif pnl < 0:
            loss_by_symbol[symbol] += pnl
    positives = sorted(positive_by_symbol.values(), reverse=True)
    gross_profit = sum(positives)
    losses = list(loss_by_symbol.values())
    by_industry: dict[str, float] = defaultdict(float)
    for symbol, pnl in positive_by_symbol.items():
        by_industry[industries.get(symbol, "unknown")] += pnl
    minimum_symbols = max(40, max_positions * 4)

    def share(values: list[float], count: int) -> float:
        return round(sum(values[:count]) / gross_profit, 6) if gross_profit else 0.0

    return {
        "distinct_symbols": len(by_symbol),
        "minimum_distinct_symbols": minimum_symbols,
        "gross_profit": round(gross_profit, 6),
        "top1_gross_share": share(positives, 1),
        "top3_gross_share": share(positives, 3),
        "top5_gross_share": share(positives, 5),
        "top_industry_gross_share": (
            round(max(by_industry.values()) / gross_profit, 6)
            if gross_profit and by_industry
            else 0.0
        ),
        "max_symbol_loss_to_gross": (
            round(abs(min(losses)) / gross_profit, 6)
            if gross_profit and losses
            else 0.0
        ),
    }


def curve_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [
        (
            str(curve[index]["date"])[:10],
            float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0,
        )
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def yearly_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    buckets: dict[str, float] = defaultdict(lambda: 1.0)
    for day, value in curve_returns(curve):
        buckets[day[:4]] *= 1.0 + value
    return {
        year: round(product - 1.0, 6)
        for year, product in sorted(buckets.items())
    }


def load_runtime_inputs(root: Path) -> dict[str, Any]:
    frozen = load_frozen_inputs(root)
    r532 = frozen["r532"]
    study = r532.load_study_inputs(root)
    main_core, _, _ = r532.build_core(study, study["representatives"])
    control_representatives = [
        row for row in study["representatives"] if row["family_id"] in CONTROL_FACTORS
    ]
    if len(control_representatives) != 2:
        raise RuntimeError("R533 control must contain exactly two frozen factors")
    control_core, _, _ = r532.build_core(study, control_representatives)
    main_panel = scaled_score_panel(main_core)
    control_panel = scaled_score_panel(control_core).rename({"score": "control_score"})
    panel = main_panel.join(
        control_panel.select("symbol", "date", "control_score"),
        on=["symbol", "date"],
        how="full",
        coalesce=True,
    ).sort(["date", "symbol"])
    membership = study["membership"].select("symbol", "date")
    outside = panel.join(membership, on=["symbol", "date"], how="anti")
    if not outside.is_empty():
        raise RuntimeError(f"score panel contains non-members: {outside.height}")
    latest_catalog = json.loads(
        (DATA / "factor_research/catalog/latest.json").read_text(encoding="utf-8")
    )
    snapshot_id = str(latest_catalog["snapshot_id"]).removeprefix("sha256:")
    snapshot = json.loads(
        (
            DATA
            / "factor_research/catalog/snapshots"
            / f"{snapshot_id}.json"
        ).read_text(encoding="utf-8")
    )
    financial_pit_verified = any(
        row["factor_id"] == "ext_alt_hs300_dividend_yield"
        and row.get("pit_status") == "verified"
        for row in snapshot["factors"]
    )
    if not financial_pit_verified:
        raise RuntimeError("dividend factor financial PIT status is not verified")
    industries = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in load_industry_l1(DATA).iter_rows(named=True)
    }
    return {
        **frozen,
        "study": study,
        "panel": panel,
        "symbols": sorted(membership["symbol"].unique().to_list()),
        "industries": industries,
        "membership_days": membership.select("date").unique().height,
        "score_rows": panel.height,
        "score_rows_within_membership": outside.is_empty(),
        "financial_pit_verified": financial_pit_verified,
    }


def prepare_score_window(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    *,
    start: date,
    end: date,
    max_hold_days: int,
) -> Any:
    seed = p5.portfolio._backtest_config(
        {"score": 1.0},
        {"score": "high"},
        start=start,
        end=end,
        max_positions=20,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=max_hold_days,
    )
    seed.symbols = list(symbols)
    seed.hs300 = False
    prepared = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared,
        panel,
        ["score", "control_score"],
    )


def cost_parameters(p5: Any, label: str) -> dict[str, float]:
    if label == "5x":
        return {
            "fees_pct": 0.001,
            "commission_pct": 0.001,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 25.0,
        }
    return p5.portfolio._cost_parameters(label)


def config_symbols_for_prepared(prepared: Any) -> list[str]:
    requested = prepared.signature[1]
    if requested is None:
        raise RuntimeError("prepared matrix has no explicit requested universe")
    return list(requested)


def build_formal_config(
    p5: Any,
    prepared: Any,
    *,
    start: date,
    end: date,
    max_positions: int,
    max_hold_days: int,
    cost_label: str,
    score_field: str = "score",
) -> Any:
    config = p5.portfolio._backtest_config(
        {score_field: 1.0},
        {score_field: "high"},
        start=start,
        end=end,
        max_positions=max_positions,
        cost=cost_parameters(p5, cost_label),
        max_hold_days=max_hold_days,
    )
    config.params = {
        **(config.params or {}),
        "entry_score": ENTRY_SCORE,
        "exit_score": EXIT_SCORE,
        "top_rank": max(20, max_positions),
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "equal"
    config.symbols = config_symbols_for_prepared(prepared)
    config.hs300 = False
    return config


def _evidence_summary(evidence: dict[str, Any]) -> dict[str, str]:
    formal = evidence.get("formal_status") or {}
    return {
        "run_id": str(formal.get("run_id")),
        "persistence": str(formal.get("persistence")),
        "lookahead": str(formal.get("lookahead")),
        "execution_timing": str(formal.get("execution_timing")),
    }


def run_formal_window(
    p5: Any,
    service: Any,
    prepared: Any,
    *,
    label: str,
    start: date,
    end: date,
    max_positions: int,
    max_hold_days: int,
    cost_label: str,
    score_field: str = "score",
) -> dict[str, Any]:
    config = build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=max_positions,
        max_hold_days=max_hold_days,
        cost_label=cost_label,
        score_field=score_field,
    )
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R533:{label}")
    if result.error:
        raise RuntimeError(f"R533:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "max_positions": max_positions,
        "max_hold_days": max_hold_days,
        "score_field": score_field,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": _evidence_summary(evidence),
    }


def bootstrap_curve(curve: list[dict[str, Any]], *, seed: int) -> dict[str, float]:
    r453 = _load_script_module("research_r453_value_quality_portfolio")
    return r453.bootstrap(
        curve,
        block=BOOTSTRAP_BLOCK,
        paths=BOOTSTRAP_RUNS,
        seed=seed,
    )


def run_training_grid(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    prepared_by_hold = {
        hold: prepare_score_window(
            p5,
            service,
            runtime["panel"],
            runtime["symbols"],
            start=TRAIN_START,
            end=TRAIN_END,
            max_hold_days=hold,
        )
        for hold in HOLD_DAYS
    }
    rows = []
    for candidate in training_candidates():
        positions = int(candidate["max_positions"])
        hold = int(candidate["max_hold_days"])
        run = run_formal_window(
            p5,
            service,
            prepared_by_hold[hold],
            label=f"train_p{positions}_h{hold}_2x",
            start=TRAIN_START,
            end=TRAIN_END,
            max_positions=positions,
            max_hold_days=hold,
            cost_label="2x",
        )
        run["bootstrap"] = bootstrap_curve(
            run["curve"],
            seed=BOOTSTRAP_SEED + positions * 100 + hold,
        )
        run["eligible"] = training_eligible(run)
        rows.append(run)
    return rows


def run_confirmation(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    positions = int(selected["max_positions"])
    hold = int(selected["max_hold_days"])
    full_prepared = prepare_score_window(
        p5,
        service,
        runtime["panel"],
        runtime["symbols"],
        start=TRAIN_START,
        end=END,
        max_hold_days=hold,
    )
    full = {
        cost: run_formal_window(
            p5,
            service,
            full_prepared,
            label=f"full_p{positions}_h{hold}_{cost}",
            start=TRAIN_START,
            end=END,
            max_positions=positions,
            max_hold_days=hold,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_full = run_formal_window(
        p5,
        service,
        full_prepared,
        label=f"control_full_p{positions}_h{hold}_2x",
        start=TRAIN_START,
        end=END,
        max_positions=positions,
        max_hold_days=hold,
        cost_label="2x",
        score_field="control_score",
    )

    validation_prepared = prepare_score_window(
        p5,
        service,
        runtime["panel"],
        runtime["symbols"],
        start=VALIDATION_START,
        end=VALIDATION_END,
        max_hold_days=hold,
    )
    validation = run_formal_window(
        p5,
        service,
        validation_prepared,
        label=f"validation_p{positions}_h{hold}_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        max_positions=positions,
        max_hold_days=hold,
        cost_label="2x",
    )

    recent_prepared = prepare_score_window(
        p5,
        service,
        runtime["panel"],
        runtime["symbols"],
        start=RECENT_START,
        end=END,
        max_hold_days=hold,
    )
    recent = {
        cost: run_formal_window(
            p5,
            service,
            recent_prepared,
            label=f"recent_p{positions}_h{hold}_{cost}",
            start=RECENT_START,
            end=END,
            max_positions=positions,
            max_hold_days=hold,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_recent = run_formal_window(
        p5,
        service,
        recent_prepared,
        label=f"control_recent_p{positions}_h{hold}_2x",
        start=RECENT_START,
        end=END,
        max_positions=positions,
        max_hold_days=hold,
        cost_label="2x",
        score_field="control_score",
    )

    fresh = []
    for start in FRESH_STARTS:
        prepared = prepare_score_window(
            p5,
            service,
            runtime["panel"],
            runtime["symbols"],
            start=start,
            end=END,
            max_hold_days=hold,
        )
        run = run_formal_window(
            p5,
            service,
            prepared,
            label=f"fresh_{start.isoformat()}_p{positions}_h{hold}_2x",
            start=start,
            end=END,
            max_positions=positions,
            max_hold_days=hold,
            cost_label="2x",
        )
        fresh.append({
            "start": start.isoformat(),
            "total_return": run["stats"]["total_return"],
            "annual_return": run["stats"]["annual_return"],
            "sharpe": run["stats"]["sharpe"],
            "max_drawdown": run["stats"]["max_drawdown"],
            "formal_evidence": run["formal_evidence"],
        })
    return {
        "full": full,
        "validation": validation,
        "recent": recent,
        "control_full": control_full,
        "control_recent": control_recent,
        "fresh_starts": fresh,
        "yearly": yearly_returns(full["2x"]["curve"]),
    }


def _net_to_gross(trades: list[dict[str, Any]]) -> float:
    pnls = [float(trade.get("pnl_amount") or 0.0) for trade in trades]
    gross = sum(value for value in pnls if value > 0)
    net = sum(pnls)
    return round(net / gross, 6) if gross else 0.0


def formal_evidence_pass(runs: list[dict[str, Any]]) -> bool:
    return bool(runs) and all(
        row["formal_evidence"]["persistence"] == "saved"
        and row["formal_evidence"]["lookahead"] == "passed"
        and row["formal_evidence"]["execution_timing"] == "passed"
        for row in runs
    )


def build_gate_payload(
    runtime: dict[str, Any],
    training: list[dict[str, Any]],
    confirmation: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    full_2x = confirmation["full"]["2x"]
    full_3x = confirmation["full"]["3x"]
    full_5x = confirmation["full"]["5x"]
    recent_2x = confirmation["recent"]["2x"]
    recent_3x = confirmation["recent"]["3x"]
    recent_5x = confirmation["recent"]["5x"]
    control_full = confirmation["control_full"]
    control_recent = confirmation["control_recent"]
    concentration = concentration_metrics(
        full_2x["trades"],
        runtime["industries"],
        max_positions=int(selected["max_positions"]),
    )
    concentration["net_to_gross"] = _net_to_gross(full_2x["trades"])
    all_runs = [
        *training,
        full_2x,
        full_3x,
        full_5x,
        recent_2x,
        recent_3x,
        recent_5x,
        control_full,
        control_recent,
    ]
    evidence_ok = formal_evidence_pass(all_runs)
    checks = {
        "input_sidecars_valid": True,
        "r526_daily_membership": True,
        "financial_factor_pit_verified": runtime["financial_pit_verified"],
        "score_rows_within_membership": runtime["score_rows_within_membership"],
        "formal_evidence_saved_lookahead_t1": evidence_ok,
        "no_leverage": True,
        "open_t_plus_one": True,
        "limit_and_suspension_constraints": True,
    }
    main_2x = {
        **full_2x["stats"],
        "recent": recent_2x["stats"]["total_return"],
        "year_2022": confirmation["yearly"].get("2022"),
        "year_2023": confirmation["yearly"].get("2023"),
        "fresh_starts": [row["total_return"] for row in confirmation["fresh_starts"]],
    }
    candidate = {
        "id": "r533_core_six_factor_formal",
        "max_positions": int(selected["max_positions"]),
        "is_multi_asset": False,
        "data_integrity": {"pass": all(checks.values()), "checks": checks},
        "no_leverage": True,
        "main_2x": main_2x,
        "control_2x": {
            "annual": control_full["stats"]["annual_return"],
            "sharpe": control_full["stats"]["sharpe"],
            "recent": control_recent["stats"]["total_return"],
        },
        "stress_3x": {
            **full_3x["stats"],
            "cost_factor": 3,
            "recent": recent_3x["stats"]["total_return"],
        },
        "extreme_5x": {
            **full_5x["stats"],
            "cost_factor": 5,
            "recent": recent_5x["stats"]["total_return"],
            "net_to_gross": concentration["net_to_gross"],
        },
        "concentration": concentration,
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    evaluation = evaluate_gate_policy_v2(candidate)
    validation_positive = (
        confirmation["validation"]["stats"]["annual_return"] is not None
        and confirmation["validation"]["stats"]["annual_return"] > 0
    )
    hard_pass = (
        evaluation["status"] == "qualified_for_paper_review"
        and validation_positive
    )
    conclusion = "watch_only" if hard_pass else "rejected"
    failed = _failed_leaf_paths(evaluation["gates"])
    if not validation_positive:
        failed.append("validation_annual_positive")
    return {
        "candidate": candidate,
        "evaluation": evaluation,
        "validation_positive": validation_positive,
        "conclusion": conclusion,
        "failed_gate_paths": failed,
        "formal_evidence": full_2x["formal_evidence"],
    }


def _failed_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.extend(_failed_leaf_paths(item, path))
    elif isinstance(value, bool) and not value:
        paths.append(prefix)
    return paths


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"curve", "trades"}
    }


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_runtime_inputs(root)
    p5 = _load_script_module("research_factor_increment_p5")
    service = p5.portfolio._service()
    training = run_training_grid(p5, service, runtime)
    selected = select_training_candidate(training)
    base_payload = {
        "schema_version": 1,
        "research_id": "R533A",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r530": R530_RESULT.relative_to(ROOT).as_posix(),
        "source_r531": R531_RESULT.relative_to(ROOT).as_posix(),
        "source_r532": R532_RESULT.relative_to(ROOT).as_posix(),
        "runtime": {
            "membership_days": runtime["membership_days"],
            "symbols": len(runtime["symbols"]),
            "score_rows": runtime["score_rows"],
            "score_rows_within_membership": runtime["score_rows_within_membership"],
            "financial_pit_verified": runtime["financial_pit_verified"],
            "control_factors": list(CONTROL_FACTORS),
        },
        "training_grid": [_public_run(row) for row in training],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    if selected is None:
        first_evidence = training[0]["formal_evidence"]
        base_payload.update({
            "selected": None,
            "selection_status": "no_training_candidate",
            "confirmation": None,
            "gate": {
                "conclusion": "rejected",
                "failed_gate_paths": ["training_candidate_selected"],
                "evaluation": None,
            },
            "conclusion": "rejected",
            "formal_evidence": first_evidence,
            "negative_knowledge": ["training_candidate_selected"],
            "known_limits": [
                "No training candidate passed the frozen economic, risk, trade-count and bootstrap gates.",
                "No validation, recent or pressure result was used to change parameters.",
                "No leverage, orders, paper targets or active version are produced.",
            ],
        })
        return base_payload, runtime

    confirmation = run_confirmation(p5, service, runtime, selected)
    gate = build_gate_payload(runtime, training, confirmation, selected)
    base_payload.update({
        "selected": _public_run(selected),
        "selection_status": "selected_on_training_only",
        "confirmation": {
            "full": {
                cost: _public_run(row)
                for cost, row in confirmation["full"].items()
            },
            "validation": _public_run(confirmation["validation"]),
            "recent": {
                cost: _public_run(row)
                for cost, row in confirmation["recent"].items()
            },
            "control_full": _public_run(confirmation["control_full"]),
            "control_recent": _public_run(confirmation["control_recent"]),
            "fresh_starts": confirmation["fresh_starts"],
            "yearly": confirmation["yearly"],
        },
        "gate": {
            "conclusion": gate["conclusion"],
            "candidate": gate["candidate"],
            "evaluation": gate["evaluation"],
            "validation_annual_positive": gate["validation_positive"],
            "failed_gate_paths": gate["failed_gate_paths"],
        },
        "conclusion": gate["conclusion"],
        "formal_evidence": gate["formal_evidence"],
        "negative_knowledge": gate["failed_gate_paths"],
        "known_limits": [
            "R533 attempt 1 failed before result publication because the prepared requested universe was replaced by filtered market axes; R533A is the explicit amendment.",
            "Factor parameters and the six-member core are frozen from R530/R531; R532 retained a conservative statistical-screen rejection.",
            "The R526 membership and industry vintages are reconstructed research inputs, not provider publication snapshots.",
            "Execution parameters are selected only on the training window, then validation and recent windows are confirmation-only.",
            "The project v2 gate policy treats 5x cost as warning-only, never as leverage or a hard rejection.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
    })
    return base_payload, runtime


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R533 因子库核心正式组合验证",
        "",
        "## 训练期执行参数",
        "",
        "| 持仓数 | 持有期 | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["training_grid"]:
        stats = row["stats"]
        lines.append(
            f"| {row['max_positions']} | {row['max_hold_days']} | "
            f"{_fmt(stats['annual_return'])} | {_fmt(stats['sharpe'])} | "
            f"{_fmt(stats['max_drawdown'])} | {_fmt(row['bootstrap']['total_p05'])} | "
            f"{row['trade_count']} | {row['eligible']} |"
        )
    lines += ["", "## 选中参数", ""]
    selected = payload.get("selected")
    if selected is None:
        lines.append("- 没有训练候选通过预注册资格。")
    else:
        lines += [
            f"- 持仓数：{selected['max_positions']}",
            f"- 最大持有期：{selected['max_hold_days']}",
            f"- 训练年化：{_fmt(selected['stats']['annual_return'])}",
            f"- 训练 Sharpe：{_fmt(selected['stats']['sharpe'])}",
            f"- 训练 bootstrap p05：{_fmt(selected['bootstrap']['total_p05'])}",
        ]
    confirmation = payload.get("confirmation")
    if confirmation:
        lines += [
            "",
            "## 确认窗口",
            "",
            "| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        rows = [
            ("full 2x", confirmation["full"]["2x"]),
            ("full 3x", confirmation["full"]["3x"]),
            ("full 5x", confirmation["full"]["5x"]),
            ("validation 2x", confirmation["validation"]),
            ("recent 2x", confirmation["recent"]["2x"]),
            ("recent 3x", confirmation["recent"]["3x"]),
            ("recent 5x", confirmation["recent"]["5x"]),
            ("control full 2x", confirmation["control_full"]),
            ("control recent 2x", confirmation["control_recent"]),
        ]
        for label, row in rows:
            stats = row["stats"]
            lines.append(
                f"| {label} | {_fmt(stats['annual_return'])} | {_fmt(stats['sharpe'])} | "
                f"{_fmt(stats['max_drawdown'])} | {_fmt(stats['total_return'])} | {row['trade_count']} |"
            )
        lines += ["", "## 年度和起始相位", "", "### 年度", ""]
        lines += [f"- {year}: {_fmt(value)}" for year, value in confirmation["yearly"].items()]
        lines += ["", "### Fresh starts", ""]
        lines += [
            f"- {row['start']}: {_fmt(row['total_return'])}"
            for row in confirmation["fresh_starts"]
        ]
    lines += [
        "",
        "## 门禁",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 失败路径：{payload['gate']['failed_gate_paths']}",
    ]
    evaluation = payload["gate"].get("evaluation")
    if evaluation is not None:
        lines += [
            f"- v2 状态：{evaluation['status']}",
            f"- 5× warnings：{evaluation['diagnostics']['5x_warnings']}",
            f"- 5× 硬否决：{evaluation['diagnostics']['5x_hard_rejection']}",
        ]
    lines += ["", "## 边界", ""]
    lines += [f"- {item}" for item in payload["known_limits"]]
    if payload["negative_knowledge"]:
        lines += ["", "## 负知识", ""]
        lines += [f"- {item}" for item in payload["negative_knowledge"]]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R533 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R533 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R533 preregistration already exists: {PREREG}")
    runtime = load_runtime_inputs(root)
    r530 = runtime["study"]["r530"]
    bundle = Path(root) / r530.PIT_BUNDLE.relative_to(ROOT)
    payload = {
        "schema_version": 1,
        "research_id": "R533A",
        "research_type": RESEARCH_TYPE,
        "question": "Does the frozen six-factor R531 core produce an investable long-only HS300 portfolio under the project formal T+1 path after training-only execution-parameter MC?",
        "hypothesis": "A diversified six-factor rank core survives realistic costs, the recent year and a simple two-factor control without leverage.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {
            "r530_result": sha256(root / R530_RESULT.relative_to(ROOT)),
            "r530_preregistration": sha256(root / R530_PREREG.relative_to(ROOT)),
            "r531_result": sha256(root / R531_RESULT.relative_to(ROOT)),
            "r531_preregistration": sha256(root / R531_PREREG.relative_to(ROOT)),
            "r532_result": sha256(root / R532_RESULT.relative_to(ROOT)),
            "r532_preregistration": sha256(root / R532_PREREG.relative_to(ROOT)),
            "r526_membership": sha256(bundle / "daily_snapshots.parquet"),
            "r526_industry": sha256(bundle / "industry_history.parquet"),
            "gate_policy_v2": sha256(GATE_POLICY),
            "r533_attempt1_failure": sha256(root / R533_ATTEMPT1.relative_to(ROOT)),
        },
        "parameters": {
            "representatives": runtime["representatives"],
            "control_factors": list(CONTROL_FACTORS),
            "training_grid": training_candidates(),
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "cost_baseline": "2x",
            "cost_stress": "3x",
            "cost_diagnostic": "5x",
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "bootstrap_block": BOOTSTRAP_BLOCK,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "selection_order": [
                "bootstrap_total_p05_desc",
                "sharpe_desc",
                "annual_desc",
                "max_positions_asc",
                "max_hold_days_asc",
            ],
            "fresh_starts": [str(day) for day in FRESH_STARTS],
            "amends_failed_attempt": R533_ATTEMPT1.relative_to(ROOT).as_posix(),
        },
        "sample": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "membership_days": runtime["membership_days"],
            "symbols": len(runtime["symbols"]),
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "training_annual_min": TRAIN_ANNUAL_MIN,
            "training_sharpe_min": TRAIN_SHARPE_MIN,
            "training_mdd_min": TRAIN_MDD_MIN,
            "training_trades_min": TRAIN_TRADES_MIN,
            "training_bootstrap_p05_positive": True,
            "validation_annual_positive": True,
            "gate_policy_v2": GATE_POLICY.relative_to(ROOT).as_posix(),
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "r526_daily_membership_only_score_rows",
            "training_only_parameter_selection",
            "fixed_seed_block_bootstrap_20_5000",
            "formal_evidence_saved_lookahead_t1",
            "gate_policy_v2_layered_thresholds",
            "five_x_warning_only",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PREREG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
        raise FileExistsError(f"R533 result already exists: {RESULT}")
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
        "gate_results": payload["gate"],
        "metrics": {
            "runtime": payload["runtime"],
            "training_grid": payload["training_grid"],
            "selected": payload["selected"],
            "selection_status": payload["selection_status"],
            "confirmation": payload["confirmation"],
        },
        "source_result_hashes": [
            sha256(root / R530_RESULT.relative_to(ROOT)),
            sha256(root / R531_RESULT.relative_to(ROOT)),
            sha256(root / R532_RESULT.relative_to(ROOT)),
            sha256(root / R533_ATTEMPT1.relative_to(ROOT)),
        ],
        "formal_evidence": payload["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r533a_core_formal_portfolio_published",
    ).__dict__


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
        runtime = load_runtime_inputs(ROOT)
        print(json.dumps({
            "representatives": [
                {
                    "family_id": row["family_id"],
                    "parameter": runtime["study"]["candidates"][row["family_id"]].parameter,
                }
                for row in runtime["representatives"]
            ],
            "control_factors": list(CONTROL_FACTORS),
            "membership_days": runtime["membership_days"],
            "symbols": len(runtime["symbols"]),
            "score_rows": runtime["score_rows"],
            "training_grid": training_candidates(),
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R533 results already exist; refusing overwrite")
    payload, _runtime = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selection_status": payload["selection_status"],
        "selected": payload["selected"],
        "gate_failed_paths": payload["gate"]["failed_gate_paths"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
