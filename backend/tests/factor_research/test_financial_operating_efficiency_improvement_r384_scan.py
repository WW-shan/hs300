from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factor_research.fingerprints import sha256_json

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
RESULT = (
    DATA
    / "factor_research"
    / "research"
    / "r384_financial_operating_efficiency_improvement_2026-11-21.json"
)
SCRIPT = (
    ROOT
    / "scripts"
    / "research_financial_operating_efficiency_improvement_r384.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "research_financial_operating_efficiency_improvement_r384",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _variants(*, first_ic: float = 0.02, first_p: float = 0.04) -> dict:
    return {
        "industry": {"directional_ic": first_ic, "p_value": first_p},
        "size": {"directional_ic": 0.01, "p_value": 0.08},
        "industry_size": {"directional_ic": -0.01, "p_value": 0.02},
    }


def test_r384_pre_registers_only_four_improvement_factors() -> None:
    module = _module()

    assert module.FACTOR_DIRECTIONS == {
        "inventory_turnover_improvement_yoy": "high",
        "receivable_turnover_improvement_yoy": "high",
        "receivable_growth_minus_revenue_growth": "low",
        "inventory_growth_minus_cogs_growth": "low",
    }
    assert {item["factor_id"] for item in module.CANDIDATES} == set(
        module.FACTOR_DIRECTIONS
    )
    assert module.DEFAULT_OUTPUT.name == (
        "r384_financial_operating_efficiency_improvement_2026-11-21.json"
    )
    assert module.METHOD_VERSION == "financial_operating_efficiency_improvement_r384_v1"
    assert module.OPERATION == (
        "financial_operating_efficiency_improvement_scan_published"
    )


def test_horizon_gate_requires_two_neutralized_confirmations() -> None:
    module = _module()

    passing = module.factor_horizon_gate(_variants(), direction="high")
    failing = module.factor_horizon_gate(
        {
            "industry": {"directional_ic": 0.02, "p_value": 0.20},
            "size": {"directional_ic": 0.01, "p_value": 0.08},
            "industry_size": {"directional_ic": -0.01, "p_value": 0.02},
        },
        direction="high",
    )

    assert passing["pass"] is True
    assert passing["confirmations"] == ["industry", "size"]
    assert failing["pass"] is False
    assert "fewer_than_two_neutralized_confirmations" in failing["reasons"]


def test_candidate_status_requires_both_horizon_and_incremental_evidence() -> None:
    module = _module()
    horizon = {"pass": True, "reasons": []}
    incremental = {"pass": False, "reasons": ["marginal_ic_not_positive"]}

    assert module.candidate_status(horizon, incremental) == "incremental_rejected"
    assert (
        module.candidate_status(
            {
                "pass": False,
                "reasons": ["fewer_than_two_neutralized_confirmations"],
            },
            {"pass": True, "reasons": []},
        )
        == "horizon_rejected"
    )
    assert (
        module.candidate_status(horizon, {"pass": True, "reasons": []})
        == "incremental_pass"
    )


def test_non_financial_universe_excludes_banks_and_non_bank_finance() -> None:
    import polars as pl

    module = _module()
    panel = pl.DataFrame(
        {
            "symbol": ["BANK", "BROKER", "INDUSTRIAL"],
            "industry_l1": ["银行", "非银金融", "机械设备"],
        }
    )

    filtered = module.filter_non_financial_universe(panel)

    assert filtered["symbol"].to_list() == ["INDUSTRIAL"]


def test_published_r384_result_is_canonical_and_portfolio_gated() -> None:
    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    core = {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }
    module = _module()

    assert sha256_json(core) == payload["research_id"]
    assert payload["method_version"] == module.METHOD_VERSION
    assert payload["financial_pit_research_id"] == module.P7_RESEARCH_ID
    assert payload["p6_admission_research_id"] == module.P6_ADMISSION_RESEARCH_ID
    assert payload["candidate_count"] == 4
    assert payload["statistical_pass_count"] == 2
    assert payload["incremental_pass_count"] == 2
    assert payload["portfolio_pass_count"] == 0
    assert payload["decision"] == {
        "status": "no_portfolio_candidate",
        "factor_ids": [],
        "incremental_pass_factor_ids": [
            "inventory_turnover_improvement_yoy",
            "inventory_growth_minus_cogs_growth",
        ],
        "active_version": None,
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
    }
    assert payload["portfolio"]["run"] is True
    assert payload["portfolio"]["passing_candidates"] == []
    assert payload["portfolio"]["final"] is None
    assert payload["portfolio"]["cost_warning_5x"] is None
    assert set(payload["verified_factor_ids"]) >= set(module.FACTOR_DIRECTIONS)

    candidates = {item["factor_id"]: item for item in payload["candidates"]}
    assert candidates["inventory_turnover_improvement_yoy"]["horizon_gate"][
        "pass"
    ] is True
    assert candidates["inventory_turnover_improvement_yoy"]["incremental_gate"][
        "pass"
    ] is True
    assert candidates["inventory_turnover_improvement_yoy"]["portfolio_result"][
        "status"
    ] == "portfolio_rejected"
    assert candidates["inventory_growth_minus_cogs_growth"]["horizon_gate"][
        "pass"
    ] is True
    assert candidates["inventory_growth_minus_cogs_growth"]["incremental_gate"][
        "pass"
    ] is True
    assert candidates["inventory_growth_minus_cogs_growth"]["portfolio_result"][
        "status"
    ] == "portfolio_rejected"
    assert candidates["receivable_turnover_improvement_yoy"]["status"] == (
        "horizon_rejected"
    )
    assert candidates["receivable_growth_minus_revenue_growth"]["status"] == (
        "horizon_rejected"
    )

    negative = {
        item["factor_id"]: item for item in payload["negative_knowledge"]
    }
    assert "portfolio_rejected" in negative[
        "inventory_turnover_improvement_yoy"
    ]["reasons"]
    assert "portfolio_rejected" in negative[
        "inventory_growth_minus_cogs_growth"
    ]["reasons"]
    assert "horizon_rejected" in negative[
        "receivable_turnover_improvement_yoy"
    ]["reasons"]
    assert "horizon_rejected" in negative[
        "receivable_growth_minus_revenue_growth"
    ]["reasons"]


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_r384_rerun_is_idempotent() -> None:
    paths = [
        RESULT,
        RESULT.with_suffix(".md"),
        DATA / "factor_research" / "ledger" / "head.json",
        DATA / "factor_research" / "ledger" / "records.jsonl",
    ]
    before = _hash(paths)

    subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT / "backend",
        check=True,
        capture_output=True,
        text=True,
    )

    assert _hash(paths) == before
