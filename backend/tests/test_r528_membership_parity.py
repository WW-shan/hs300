from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r528_membership_parity_correction.py"


def _module():
    spec = importlib.util.spec_from_file_location("r528_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r528_pins_framework_membership_and_restores_it() -> None:
    module = _module()
    from app.backtest import strategy as strategy_module

    membership = module.bundle_membership()
    original = strategy_module._hs300_membership_frame
    seen = []

    class Service:
        def run(self, config, prepared=None):
            seen.append(strategy_module._hs300_membership_frame(config.start, config.end))
            return object()

    class Config:
        hs300 = True
        start = membership["effective_date"].min()
        end = membership["effective_date"].max()

    result = module.run_with_pinned_membership(Service(), Config(), membership)

    assert result is not None
    assert len(seen) == 1
    assert seen[0].equals(membership)
    assert strategy_module._hs300_membership_frame is original


def test_r528_engine_bundle_preserves_all_required_extension_columns() -> None:
    import polars as pl

    module = _module()
    source = pl.DataFrame(
        {
            "symbol": ["600001.SH"],
            "date": [module.date(2022, 5, 5)],
            "holder_top10_free_float_share": [0.1],
            "equity_risk_premium": [0.02],
            "credit_spread_5y_low": [0.01],
            "term_spread_10y_2y": [0.03],
            "industry_mom_20d": [0.04],
            "stock_rel_industry_20d": [0.01],
        }
    )
    selected = module.engine_extension_frame(source)

    assert selected.columns == [
        "symbol",
        "date",
        "holder_top10_free_float_share",
        "equity_risk_premium",
        "credit_spread_5y_low",
        "term_spread_10y_2y",
        "industry_mom_20d",
        "stock_rel_industry_20d",
    ]
    assert selected.equals(source)


def test_r528_loads_runtime_registry_for_extension_dependencies(monkeypatch) -> None:
    module = _module()
    calls: list[str] = []

    def fake_runtime_registry(data_dir):
        calls.append(str(data_dir))
        return ["cf_dependency"]

    monkeypatch.setattr(
        "app.factors.store.load_runtime_registry", fake_runtime_registry
    )
    result = module.load_runtime_factor_registry(module.DATA)

    assert result == ["cf_dependency"]
    assert calls == [str(module.DATA)]


def test_r528_output_records_corrected_parity_and_provenance() -> None:
    import hashlib
    import json

    module = _module()
    output = module.OUTPUT
    result = module.RESULT
    if not output.exists() or not result.exists():
        return
    for path in (module.PREREG, output, result):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text(encoding="ascii").split()[0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["round"] == "R528"
    assert payload["comparison"]["same_membership_for_both"] is True
    assert payload["comparison"]["r527_invalidated_claim"].endswith(
        "was_not_enforced_for_five_leg"
    )
    assert payload["families"]["hold30"]["run"]["membership_enforced_by_framework"] is True
    assert payload["families"]["five_leg"]["run"]["membership_enforced_by_framework"] is True
    assert payload["bundle"]["membership_symbols_per_session"] == 300
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False


def test_r528_runner_uses_runtime_registry_and_complete_extension_frame() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "load_runtime_factor_registry(engine_root)" in source
    assert "engine_extension_frame(" in source
    assert '"stock_rel_industry_20d"' in source
    assert 'result = service.run(config, prepared=prepared)\n        if result.error:' not in source
