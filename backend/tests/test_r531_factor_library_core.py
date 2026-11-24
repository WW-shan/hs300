from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r531_factor_library_core.py"


def _module():
    spec = importlib.util.spec_from_file_location("r531_factor_library_core_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_contract_type_and_r530_source() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "statistical_screen"' in text
    assert "r531_factor_library_core_preregistration.json" in text
    assert "r530_unified_factor_library_mc_result.json" in text


def test_r531_uses_exactly_eleven_r530_admitted_candidates() -> None:
    module = _module()
    candidates = module.load_admitted_candidates(ROOT)
    assert len(candidates) == 11
    assert {candidate.family_id for candidate in candidates} == {
        "cs_spread", "idio_vol", "vol_of_vol", "amount_level", "annual_volatility",
        "atr_risk", "bollinger_width", "max_return", "turnover_level",
        "dividend_yield_absolute", "dividend_yield_quantile",
    }


def test_clustering_uses_union_find_at_frozen_threshold() -> None:
    module = _module()
    pairs = [
        {"left": "a", "right": "b", "rho": 0.91},
        {"left": "b", "right": "c", "rho": 0.72},
        {"left": "a", "right": "c", "rho": -0.88},
        {"left": "d", "right": "c", "rho": 0.10},
    ]
    clusters = module.cluster_pairs(pairs, threshold=0.70)
    assert clusters == [["a", "b", "c"], ["d"]]


def test_month_end_selection_uses_training_dates_only() -> None:
    module = _module()
    dates = module.month_end_dates(
        module.pl.DataFrame({"date": [
            module.date(2022, 5, 5), module.date(2022, 5, 31),
            module.date(2022, 6, 30), module.date(2024, 12, 31),
            module.date(2025, 1, 2),
        ]}),
        start=module.date(2022, 5, 5),
        end=module.date(2024, 12, 31),
    )
    assert dates == [module.date(2022, 5, 31), module.date(2022, 6, 30), module.date(2024, 12, 31)]


def test_committed_r531_result_is_contract_bound() -> None:
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    research = root / "data/factor_research/research"
    prereg = research / "r531_factor_library_core_preregistration.json"
    result = research / "r531_factor_library_core_result.json"
    markdown = research / "r531_factor_library_core_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "statistical_screen"
    assert payload["conclusion"] == "admitted_for_research"
    assert payload["preregistration_sha256"] == hashlib.sha256(prereg.read_bytes()).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert len(payload["metrics"]["clusters"]) == 6
    assert len(payload["metrics"]["representatives"]) == 6
    assert all(payload["metrics"]["core_gates"].values())
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
