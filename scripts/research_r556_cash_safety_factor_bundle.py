#!/usr/bin/env python3
"""R556: reconcile the reusable R550 cash-safety factor bundle without return claims."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.extended_parameter_data import load_existing_ext_factors  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.signal_scan import calculate_rank_ic  # noqa: E402
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
)
from app.factors import store  # noqa: E402
from app.factors.registry import get_factor  # noqa: E402
from app.strategy import scoring  # noqa: E402
from app.strategy.scoring import neutralize_scoring_columns  # noqa: E402

RESEARCH_TYPE = "statistical_screen"
RESEARCH_ID = "R556B"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r556b_cash_safety_factor_bundle_preregistration.json"
)
RUNNER = "scripts/research_r556_cash_safety_factor_bundle.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = RESEARCH / "r556b_cash_safety_factor_bundle_preregistration.json"
RESULT = RESEARCH / "r556b_cash_safety_factor_bundle_result.json"
MARKDOWN = RESEARCH / "r556b_cash_safety_factor_bundle_result.md"
R556_ATTEMPT_PREREG = RESEARCH / "r556_cash_safety_factor_bundle_preregistration.json"
R556_ATTEMPT_FAILURE = RESEARCH / "r556_attempt1_prereg_execution_contract_failure.json"
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
FACTOR_ID = "uf_r550_cash_safety_rank"
FACTOR_IDS = (
    "uf_r550_cash_safety_rank",
    "cf_r550_cash_safety",
    "cf_r550_stable7",
    "cf_r550_cash_quality",
    "cf_r550_value_quality_safety",
    "uf_r550_amount_120",
    "uf_r550_turnover_120",
    "uf_r550_max_return_60",
    "uf_r550_boll_width_10",
    "uf_r550_dividend_q70",
    "uf_r550_fund_holding_n005",
)
SOURCE_RESULTS = {
    "r550": RESEARCH / "r550_cash_safety_confirmation_result.json",
    "r552c": RESEARCH / "r552c_cash_safety_parameter_mc_result.json",
    "r553": RESEARCH / "r553_r550_policy_gbm_stress_result.json",
    "r554": RESEARCH / "r554_industry_neutral_confirmation_result.json",
    "r555b": RESEARCH / "r555b_neutralization_shrink_mc_result.json",
}
EXT_FIELDS = {
    "ext_alt_hs300_dividend_yield": "dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
    "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol_20d_low",
}
MINIMUM_CROSS_SECTION = 30


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


def scientific_design() -> dict[str, Any]:
    return {
        "question": (
            "Can the R537/R550 cash-safety research score be reproduced by reusable "
            "registered factors without losing its cross-sectional IC?"
        ),
        "factor_id": FACTOR_ID,
        "factor_ids": list(FACTOR_IDS),
        "input_sources": list(SOURCE_RESULTS),
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
        },
        "metrics": {
            "return_col": "forward_return_5d",
            "minimum_cross_section": MINIMUM_CROSS_SECTION,
            "neutralization": {
                "industry": "industry_l1",
                "styles": ["log_float_mv"],
                "exclude": [],
                "shrink": 1.0,
            },
        },
        "gates": {
            "all_definitions_loaded": True,
            "coverage_min": 0.90,
            "full_ic_min": 0.01,
            "train_ic_min": 0.01,
            "validation_ic_positive": True,
            "recent_ic_positive": True,
            "neutral_full_ic_min": 0.01,
        },
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {
                "required": False,
                "status": "not_applicable",
                "reason": "R556 reconciles factor definitions only and produces no portfolio returns",
            },
            "parameter_mc": {
                "required": False,
                "status": "not_applicable",
                "reason": "The factor formulas are frozen from prior research; no parameter search is run",
            },
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _definition_paths() -> dict[str, Path]:
    return {
        factor_id: DATA / "user_data" / "custom_factors" / f"{factor_id}.json"
        for factor_id in FACTOR_IDS
    }


def _input_paths(root: Path) -> dict[str, Path]:
    inputs = {name: root / path.relative_to(ROOT) for name, path in SOURCE_RESULTS.items()}
    inputs.update({name: root / path.relative_to(ROOT) for name, path in _definition_paths().items()})
    inputs["r556_attempt_preregistration"] = root / R556_ATTEMPT_PREREG.relative_to(ROOT)
    inputs["r556_attempt_failure"] = root / R556_ATTEMPT_FAILURE.relative_to(ROOT)
    return inputs


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R556 preregistration already exists: {PREREG}")
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": design["question"],
        "hypothesis": (
            "A reusable registered cash-safety factor will preserve positive full/training IC, "
            "but it will not be promoted if validation or recent IC is non-positive."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": design,
        "sample": design["windows"],
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": design["gates"],
        "falsification_checks": [
            "all_registered_definitions_compile",
            "rank_composite_uses_project_registered_factors",
            "coverage_checked",
            "train_validation_recent_ic_reported",
            "industry_size_neutral_ic_reported",
            "no_portfolio_return_claim",
            "no_orders_or_active_version",
        ],
        "output_policy": design["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R556 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R556 input changed: {name}")


def _period_stats(ic_frame: pl.DataFrame, start: date, end: date) -> dict[str, Any]:
    values = ic_frame.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    )["ic"].to_numpy()
    if values.size == 0:
        return {"n_dates": 0, "ic_mean": None, "win_rate": None}
    return {
        "n_dates": int(values.size),
        "ic_mean": round(float(values.mean()), 6),
        "ic_std": round(float(values.std()), 6),
        "ir": round(float(values.mean() / values.std()), 6)
        if float(values.std()) > 1e-12
        else None,
        "win_rate": round(float((values > 0).mean()), 6),
    }


def _coverage(panel: pl.DataFrame) -> dict[str, Any]:
    scoped = panel.filter(
        (pl.col("date") >= START)
        & (pl.col("date") <= END)
        & pl.col("forward_return_5d").is_not_null()
        & pl.col("forward_return_5d").is_finite()
    )
    total = scoped.height
    valid = scoped.filter(
        pl.col(FACTOR_ID).is_not_null() & pl.col(FACTOR_ID).is_finite()
    ).height
    return {
        "rows": total,
        "valid_rows": valid,
        "ratio": round(valid / total, 6) if total else 0.0,
    }


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    loaded = set(store.load_runtime_registry(DATA))
    missing = sorted(set(FACTOR_IDS) - loaded)
    if missing:
        raise RuntimeError(f"R556 definitions did not register: {missing}")
    specs = {factor_id: get_factor(factor_id) for factor_id in FACTOR_IDS}
    if any(spec is None for spec in specs.values()):
        raise RuntimeError("R556 registered factor lookup failed")

    base = _load_module("research_r554_industry_neutral_confirmation")
    _, _runtime, candidate_panel = base._score_panel(root)
    ext = load_existing_ext_factors(DATA, EXT_FIELDS)
    panel = candidate_panel.join(ext, on=["symbol", "date"], how="left").sort(["date", "symbol"])
    panel = scoring.materialize_scoring_columns(panel, {FACTOR_ID: 1.0}).sort(["date", "symbol"])
    neutral = neutralize_scoring_columns(
        panel,
        [FACTOR_ID],
        {
            "industry": "industry_l1",
            "styles": ["log_float_mv"],
            "exclude": [],
            "shrink": 1.0,
        },
    )
    raw_ic = calculate_rank_ic(
        panel,
        FACTOR_ID,
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )
    neutral_ic = calculate_rank_ic(
        neutral,
        FACTOR_ID,
        "forward_return_5d",
        minimum=MINIMUM_CROSS_SECTION,
    )
    coverage = _coverage(panel)
    raw_periods = {
        "full": _period_stats(raw_ic, START, END),
        "train": _period_stats(raw_ic, START, TRAIN_END),
        "validation": _period_stats(raw_ic, VALIDATION_START, VALIDATION_END),
        "recent": _period_stats(raw_ic, RECENT_START, END),
    }
    neutral_periods = {
        "full": _period_stats(neutral_ic, START, END),
        "train": _period_stats(neutral_ic, START, TRAIN_END),
        "validation": _period_stats(neutral_ic, VALIDATION_START, VALIDATION_END),
        "recent": _period_stats(neutral_ic, RECENT_START, END),
    }
    checks = {
        "all_definitions_loaded": not missing,
        "coverage_min": coverage["ratio"] >= 0.90,
        "full_ic_min": float(raw_periods["full"]["ic_mean"] or -1.0) >= 0.01,
        "train_ic_min": float(raw_periods["train"]["ic_mean"] or -1.0) >= 0.01,
        "validation_ic_positive": float(raw_periods["validation"]["ic_mean"] or -1.0) > 0.0,
        "recent_ic_positive": float(raw_periods["recent"]["ic_mean"] or -1.0) > 0.0,
        "neutral_full_ic_min": float(neutral_periods["full"]["ic_mean"] or -1.0) >= 0.01,
    }
    gate = {
        "pass": all(checks.values()),
        "checks": checks,
        "failed": [name for name, value in checks.items() if not value],
    }
    receipt = build_standard_validation_receipt(
        {
            "status": "not_applicable",
            "reason": "Factor-only reconciliation has no synthetic market strategy path",
        },
        {
            "status": "not_applicable",
            "reason": "Definitions are frozen from prior research and no parameter search is run",
        },
        artifacts=[],
    )
    checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
    if not checked["pass"]:
        raise RuntimeError(f"R556 standard validation failed: {checked['errors']}")
    conclusion = "watch_only" if gate["pass"] else "rejected"
    known_limits = [
        "R556 is a factor-only statistical reconciliation and makes no portfolio-return claim.",
        "The reusable rank composite preserves the source factor signs but is not numerically identical to R531's internal composite.",
        "A negative validation IC blocks factor-set promotion even if full and recent IC are positive.",
        "No active version, orders, paper targets or production enablement are produced.",
    ]
    payload = {
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "definitions": {
            factor_id: {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256(path),
                "kind": specs[factor_id].kind,
                "dependencies": sorted(specs[factor_id].dependencies),
                "warmup_bars": specs[factor_id].warmup_bars,
            }
            for factor_id, path in _definition_paths().items()
        },
        "coverage": coverage,
        "raw_ic": raw_periods,
        "neutral_ic": neutral_periods,
        "gate": gate,
        "standard_validation": receipt,
        "factor_set_promoted": False,
        "conclusion": conclusion,
        "known_limits": known_limits,
        "negative_knowledge": [] if gate["pass"] else gate["failed"],
        "output_policy": scientific_design()["output_policy"],
    }
    envelope = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "preregistration_sha256": verify_sidecar(PREREG),
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": gate,
        "metrics": {
            "design": payload["design"],
            "definitions": payload["definitions"],
            "coverage": coverage,
            "raw_ic": raw_periods,
            "neutral_ic": neutral_periods,
            "standard_validation": receipt,
        },
        "source_result_hashes": [sha256(path) for path in SOURCE_RESULTS.values()],
        "formal_evidence": None,
        "known_limits": known_limits,
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    markdown = _render_markdown(payload)
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=envelope,
        markdown=markdown,
        operation="r556b_cash_safety_factor_bundle_published",
    ).__dict__
    return {**payload, "published": published}


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R556 R550 因子库对账",
        "",
        "> `statistical_screen`：只核对可复用因子与 IC，不声明组合收益或生产可用。",
        "",
        "## 因子",
        "",
        f"- 主因子：`{FACTOR_ID}`",
        f"- 覆盖率：{payload['coverage']['ratio']:.2%}",
        "",
        "## IC",
        "",
        "| 口径 | 全周期 | 训练 | 验证 | 近期 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, block in (("原始", payload["raw_ic"]), ("行业+规模中性", payload["neutral_ic"])):
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(
                "—" if block[name]["ic_mean"] is None else f"{block[name]['ic_mean']:.4f}"
                for name in ("full", "train", "validation", "recent")
            )
            + " |"
        )
    lines += ["", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in payload["gate"]["checks"].items()]
    lines += ["", "## 结论", "", payload["conclusion"], "", "## 边界", ""]
    lines += [f"- {item}" for item in payload["known_limits"]]
    lines.append("")
    return "\n".join(lines)


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
        raise FileExistsError("R556 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "coverage": payload["coverage"],
        "raw_ic": payload["raw_ic"],
        "neutral_ic": payload["neutral_ic"],
        "gate": payload["gate"],
        "published": payload["published"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
