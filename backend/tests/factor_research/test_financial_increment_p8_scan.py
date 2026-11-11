from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factor_research.financial_pit import (
    BUILTIN_FINANCIAL_DIRECTIONS,
    BUILTIN_FINANCIAL_FACTOR_IDS,
)
from app.factor_research.fingerprints import sha256_json

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
SCRIPT = ROOT / "scripts" / "research_financial_increment_p8.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "research_financial_increment_p8", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(factor_id: str) -> dict:
    return {
        "candidate_key": f"p8_{factor_id}",
        "economic_key": factor_id,
        "label": factor_id,
        "factor_id": factor_id,
        "direction": BUILTIN_FINANCIAL_DIRECTIONS[factor_id],
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    }


def test_financial_candidates_cover_all_p7_verified_builtin_factors() -> None:
    module = _module()

    audit = module.load_verified_audit(DATA)
    candidates = module.financial_candidates(audit)

    assert len(candidates) == 11
    assert {item["factor_id"] for item in candidates} == set(
        BUILTIN_FINANCIAL_DIRECTIONS
    )
    assert all(
        item["direction"] == BUILTIN_FINANCIAL_DIRECTIONS[item["factor_id"]]
        for item in candidates
    )


def test_financial_candidates_reject_any_unverified_factor() -> None:
    module = _module()
    verified = list(BUILTIN_FINANCIAL_DIRECTIONS)
    audit = {
        "verified_factor_ids": verified[:-1],
        "factors": {
            factor_id: {"status": "verified"} for factor_id in verified[:-1]
        },
    }

    with pytest.raises(ValueError, match="not P7-verified"):
        module.financial_candidates(audit)


def test_statistical_precheck_marks_only_passing_candidates_for_portfolio() -> None:
    module = _module()
    passing = _candidate("pb_latest")
    failing = _candidate("roe_latest")
    marginal = {
        passing["candidate_key"]: {
            "directional_delta_mean": 0.01,
            "delta_p_value": 0.01,
            "delta_q_value": 0.02,
            "max_abs_correlation_for_gate": 0.20,
            "n_dates": 500,
            "recent_directional_delta_mean": 0.005,
        },
        failing["candidate_key"]: {
            "directional_delta_mean": -0.01,
            "delta_p_value": 0.01,
            "delta_q_value": 0.02,
            "max_abs_correlation_for_gate": 0.20,
            "n_dates": 500,
            "recent_directional_delta_mean": -0.005,
        },
    }

    records = module.gate_financial_candidates([passing, failing], marginal)
    by_id = {item["factor_id"]: item for item in records}

    assert by_id["pb_latest"]["status"] == "statistical_pass"
    assert by_id["pb_latest"]["statistical_gate"]["pass"] is True
    assert by_id["roe_latest"]["status"] == "statistical_rejected"
    assert by_id["roe_latest"]["statistical_gate"]["pass"] is False
    assert module.portfolio_candidates(records) == [by_id["pb_latest"]]


def test_financial_candidates_fail_closed_on_p6_admission(monkeypatch) -> None:
    module = _module()
    candidates = [_candidate("pb_latest")]
    checked: list[list[str]] = []

    def reject(_data_dir, factor_ids):
        checked.append(list(factor_ids))
        raise ValueError("portfolio admission rejected: financial_pit_not_verified")

    monkeypatch.setattr(module, "assert_portfolio_candidates_admitted", reject)

    with pytest.raises(ValueError, match="portfolio admission rejected"):
        module.admit_financial_candidates(DATA, candidates)

    assert checked == [["pb_latest"]]


def test_select_final_set_never_exceeds_eight_members_and_prefers_ic_order() -> None:
    module = _module()
    current_ids = [f"f{index}" for index in range(8)]
    directions = {factor_id: "high" for factor_id in current_ids}
    passing = [
        {**_candidate("pb_latest"), "marginal_ic": {"directional_delta_mean": 0.03}},
        {**_candidate("roe_latest"), "marginal_ic": {"directional_delta_mean": 0.02}},
        {**_candidate("ocf_ni"), "marginal_ic": {"directional_delta_mean": 0.01}},
    ]

    selected_ids, selected_directions = module.select_final_set(
        passing,
        current_ids,
        directions,
        weakest_factors=["f7", "f6", "f5"],
    )

    assert len(selected_ids) == 8
    assert selected_ids[-3:] == ["pb_latest", "roe_latest", "ocf_ni"]
    assert {"pb_latest", "roe_latest", "ocf_ni"}.issubset(selected_ids)
    assert selected_directions["pb_latest"] == "low"


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_financial_panel_attaches_all_verified_factors() -> None:
    module = _module()
    from app.hs300.current import current_symbols

    panel, coverage = module.load_financial_panel(
        DATA,
        current_symbols(),
        list(BUILTIN_FINANCIAL_FACTOR_IDS),
    )

    assert panel.height > 0
    assert set(coverage) == set(BUILTIN_FINANCIAL_FACTOR_IDS)
    for factor_id in BUILTIN_FINANCIAL_FACTOR_IDS:
        assert factor_id in panel.columns
        assert coverage[factor_id]["non_null_rows"] > 0
        assert coverage[factor_id]["symbol_count"] > 0


P8_RESULT = DATA / "factor_research" / "research" / "p8_financial_increment_2026-11-12.json"


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_published_p8_result_is_pit_bound_and_watch_only() -> None:
    payload = json.loads(P8_RESULT.read_text(encoding="utf-8"))
    audit = _module().load_verified_audit(DATA)
    core = {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }

    assert payload["research_id"] == sha256_json(core)
    assert payload["financial_pit_research_id"] == audit["research_id"]
    assert payload["candidate_count"] == 11
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["paper_target_generated"] is False
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert payload["decision"]["status"] in {
        "no_statistical_candidate",
        "no_portfolio_candidate",
        "no_new_watch_version",
        "watch_version_published",
    }
    assert all(
        candidate["statistical_gate"]["pass"] is False
        for candidate in payload["candidates"]
    ) or payload["decision"]["status"] != "no_statistical_candidate"


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_p8_rerun_is_idempotent() -> None:
    paths = [
        P8_RESULT,
        P8_RESULT.with_suffix(".md"),
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
