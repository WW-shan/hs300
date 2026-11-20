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
    / "r383_financial_operating_efficiency_2026-11-21.json"
)
SCRIPT = ROOT / "scripts" / "research_financial_operating_efficiency_r383.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "research_financial_operating_efficiency_r383", SCRIPT
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


def test_r383_pre_registers_only_the_two_operating_efficiency_factors() -> None:
    module = _module()

    assert module.FACTOR_DIRECTIONS == {
        "inventory_turnover_ttm": "high",
        "receivable_turnover_ttm": "high",
    }
    assert {item["factor_id"] for item in module.CANDIDATES} == {
        "inventory_turnover_ttm",
        "receivable_turnover_ttm",
    }


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
    assert module.candidate_status(
        {"pass": False, "reasons": ["fewer_than_two_neutralized_confirmations"]},
        {"pass": True, "reasons": []},
    ) == "horizon_rejected"
    assert module.candidate_status(horizon, {"pass": True, "reasons": []}) == "incremental_pass"


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


def test_published_r383_result_is_canonical_and_incremental_gated() -> None:
    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    core = {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }

    assert sha256_json(core) == payload["research_id"]
    assert payload["candidate_count"] == 2
    assert payload["statistical_pass_count"] == 1
    assert payload["incremental_pass_count"] == 0
    assert payload["portfolio_pass_count"] == 0
    assert payload["decision"]["status"] == "no_incremental_candidate"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["paper_target_generated"] is False
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert set(payload["verified_factor_ids"]) >= {
        "inventory_turnover_ttm",
        "receivable_turnover_ttm",
    }


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_r383_rerun_is_idempotent() -> None:
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
