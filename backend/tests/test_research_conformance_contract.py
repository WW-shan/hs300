from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO = ROOT / "scripts/research_factor_set_portfolio.py"


def _portfolio():
    spec = importlib.util.spec_from_file_location(
        "research_factor_set_portfolio_contract", PORTFOLIO
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_backtest_config_propagates_industry_cap_to_project_strategy() -> None:
    module = _portfolio()
    industry = {"600000.SH": "银行", "000001.SZ": "银行"}
    config = module._backtest_config(
        {"factor_a": 1.0},
        {"factor_a": "high"},
        start=module.FULL_START,
        end=module.END,
        max_positions=10,
        cost=module._cost_parameters("2x"),
        industry_by_symbol=industry,
        max_industry_positions=3,
    )
    assert config.hs300 is True
    assert config.params["industry_by_symbol"] == industry
    assert config.params["max_industry_positions"] == 3


def test_industry_cap_uses_project_industry_mapping_contract() -> None:
    module = _portfolio()
    import polars as pl

    # The helper imports lazily; patch the project loader at its source module.
    from app.services import industry_map

    original = industry_map.load_industry_l1
    industry_map.load_industry_l1 = lambda _data: pl.DataFrame(
        {"symbol": ["600000.SH", "000001.SZ"], "industry_l1": ["银行", "银行"]}
    )
    try:
        mapping, cap = module._industry_cap_from_project_data(max_industry_positions=2)
    finally:
        industry_map.load_industry_l1 = original
    assert mapping == {"600000.SH": "银行", "000001.SZ": "银行"}
    assert cap == 2


def test_formal_evidence_requires_saved_project_manifest() -> None:
    module = _portfolio()
    assert module._formal_evidence_status({"evidence": None}) == "missing"
    assert module._formal_evidence_status(
        {"evidence": {"persistence": {"status": "error"}}}
    ) == "error"
    assert module._formal_evidence_status(
        {"evidence": {"persistence": {"status": "saved"}}}
    ) == "saved"


def test_neutralization_config_reaches_strategy_params_and_evidence_override() -> None:
    module = _portfolio()
    config = module._backtest_config(
        {"factor_a": 1.0},
        {"factor_a": "high"},
        start=module.FULL_START,
        end=module.END,
        max_positions=20,
        cost=module._cost_parameters("2x"),
    )
    neutralize = {
        "industry": "industry_l1",
        "styles": ["log_float_mv"],
        "shrink": 1.0,
    }
    config.overrides = {**(config.overrides or {}), "neutralize": neutralize}
    assert config.params["scoring"] == {"factor_a": 1.0}
    assert config.overrides["neutralize"] == neutralize


def test_research_standard_rules_require_formal_evidence_for_return_claims() -> None:
    import hashlib
    import json

    path = ROOT / "data/factor_research/research/research_standard_rules_2026-11-24.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["classification"]["project_formal"].startswith("Produces portfolio return")
    assert "persistence=saved" in payload["prohibitions"][0]
    assert "active_version" in payload["prohibitions"][2]
    assert "require_formal_evidence" in payload["classification"]["project_formal"]
    assert "custom simulation" in payload["classification"]["diagnostic_simulation"]
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_conformance_classifier_rejects_handwritten_return_simulation_as_derived() -> None:
    import importlib.util
    import sys

    path = ROOT / "scripts/audit_research_conformance.py"
    spec = importlib.util.spec_from_file_location("research_conformance_audit_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    result = module.classify(
        ROOT / "scripts/research_r446_defensive_etf_rotation.py",
        {
            "classification": "hand-written ETF simulation",
            "research_type": "diagnostic_custom_simulation",
        },
    )
    assert result["return_metric_claim"] is True
    assert result["project_engine"] is False
    assert result["status"] == "diagnostic_custom_simulation"


def test_formal_evidence_guard_rejects_missing_or_failed_audits() -> None:
    from app.factor_research.evidence_guard import require_formal_evidence

    class Result:
        run_id = "run-contract"

    result = Result()
    result.evidence = {
        "persistence": {"status": "saved"},
        "lookahead": {"status": "passed"},
        "execution_timing": {"status": "passed"},
    }
    guarded = require_formal_evidence(result, label="contract")
    assert guarded["formal_status"] == {
        "run_id": "run-contract",
        "persistence": "saved",
        "lookahead": "passed",
        "execution_timing": "passed",
    }

    result.evidence["lookahead"] = {"status": "failed"}
    try:
        require_formal_evidence(result, label="contract")
    except RuntimeError as exc:
        assert "lookahead" in str(exc)
    else:
        raise AssertionError("failed lookahead must fail closed")


def test_conformance_classifier_preserves_source_summary_as_derived() -> None:
    import importlib.util
    import sys

    path = ROOT / "scripts/audit_research_conformance.py"
    spec = importlib.util.spec_from_file_location(
        "research_conformance_audit_source_summary_contract", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    result = module.classify(ROOT / "scripts/research_r404_bootstrap.py", None)
    assert result["return_metric_claim"] is True
    assert result["derived_analysis_only"] is True
    assert result["status"] == "derived_analysis_only"


def test_every_authoritative_runner_declares_a_research_type_mapping() -> None:
    import json

    path = ROOT / "data/factor_research/research/research_conformance_audit_2026-11-23.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "project_chain_with_formal_evidence": "project_formal",
        "project_chain_without_formal_evidence": "project_formal_missing_evidence",
        "non_project_backtest_or_custom_simulation": "diagnostic_custom_simulation",
        "statistical_or_feasibility_only": "statistical_screen_or_feasibility",
        "non_return_research": "non_return_research",
        "derived_analysis_only": "derived_analysis_only",
        "diagnostic_derived_analysis_only": "derived_analysis_only",
        "diagnostic_attribution": "attribution",
        "diagnostic_external_data_feasibility": "external_data_feasibility",
        "diagnostic_external_data_diagnostic_simulation": "external_data_diagnostic_simulation",
        "diagnostic_statistical_screen": "statistical_screen",
        "diagnostic_non_project_backtest_or_custom_simulation": "diagnostic_custom_simulation",
        "diagnostic_custom_simulation": "diagnostic_custom_simulation",
    }
    statuses = {row["status"] for row in payload["runners"]}
    assert statuses <= set(allowed)
    assert payload["gates"] == {
        "project_chain_without_formal_evidence": 0,
        "non_project_backtest_or_custom_simulation": 0,
        "unguarded_formal_calls": 0,
    }
    assert "project_chain_without_formal_evidence" not in statuses
    assert "non_project_backtest_or_custom_simulation" not in statuses
    assert not any(
        row["status"] == "diagnostic_non_project_backtest_or_custom_simulation"
        and row.get("research_type") != "diagnostic_custom_simulation"
        for row in payload["runners"]
    )


def test_research_standard_rules_amendment_matches_runner_hashes() -> None:
    import hashlib
    import json

    path = ROOT / "data/factor_research/research/diagnostic_simulation_amendment_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        runner = ROOT / row["runner"]
        assert hashlib.sha256(runner.read_bytes()).hexdigest() == row["runner_sha256"]


def test_asset_ledger_has_standard_type_for_every_runner() -> None:
    import json

    path = ROOT / "data/factor_research/research/research_asset_ledger_2026-11-24_v21.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    runners = [row for row in payload["rows"] if row.get("kind") == "runner"]
    assert runners
    assert all(row.get("research_type") for row in runners)
    retained = [row for row in payload["rows"] if row.get("kind") == "runner" and row.get("status") == "project_chain_with_formal_evidence"]
    assert retained
    assert {row["status"] for row in runners} <= {
        "project_chain_with_formal_evidence",
        "statistical_or_feasibility_only",
        "non_return_research",
        "derived_analysis_only",
        "diagnostic_derived_analysis_only",
        "diagnostic_attribution",
        "diagnostic_external_data_feasibility",
        "diagnostic_external_data_diagnostic_simulation",
        "diagnostic_statistical_screen",
        "diagnostic_custom_simulation",
        "diagnostic_non_project_backtest_or_custom_simulation",
    }


def test_conformance_classifier_rejects_unguarded_formal_call() -> None:
    import importlib.util
    import sys
    import tempfile

    path = ROOT / "scripts/audit_research_conformance.py"
    spec = importlib.util.spec_from_file_location(
        "research_conformance_audit_unguarded_contract", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as tmp:
        runner = Path(tmp) / "research_bad.py"
        runner.write_text(
            "service.run_with_evidence(config)\n"
            "total_return = 1.0\n",
            encoding="utf-8",
        )
        try:
            module.classify(runner, None)
        except RuntimeError as exc:
            assert "unguarded run_with_evidence" in str(exc)
        else:
            raise AssertionError("unguarded formal call must fail closed")


def test_all_direct_formal_calls_are_guarded() -> None:
    offenders = []
    paths = [
        *sorted((ROOT / "scripts").glob("research_*.py")),
        *sorted((ROOT / "scripts").glob("backtest_*.py")),
        *sorted((ROOT / "data/strategies/ai/.iterations").rglob("*.py")),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "run_with_evidence" in text and "require_formal_evidence" not in text and "formal_run" not in text:
            offenders.append(path.as_posix())
    assert not offenders, offenders


def test_all_retained_legacy_backtest_helpers_use_formal_evidence() -> None:
    import json

    path = ROOT / "data/factor_research/research/legacy_backtest_keep_closure_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    retained = [row["path"] for row in payload["rows"] if row["keep_closure"]]
    unguarded = []
    for relative in retained:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if "service.run(" in text or (
            "run_with_evidence" in text and "require_formal_evidence" not in text
        ):
            unguarded.append(relative)
    assert not unguarded, unguarded


def test_formal_run_wrapper_uses_guarded_evidence() -> None:
    from app.factor_research.evidence_guard import formal_run

    class Result:
        run_id = "wrapper-contract"
        evidence = {
            "persistence": {"status": "saved"},
            "lookahead": {"status": "passed"},
            "execution_timing": {"status": "passed"},
        }

    class Service:
        def run_with_evidence(self, config):
            assert config == "config"
            return Result()

    result = formal_run(Service(), "config")
    assert result.run_id == "wrapper-contract"
