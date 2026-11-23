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
