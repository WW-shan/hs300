"""A background refresh must not change the universe midway through a run."""
from unittest.mock import Mock

import pytest

from app.backtest.factor import FactorBacktestService, FactorBatchConfig
from tests import test_hs300_factor_membership as sample


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("explicit_symbols", [False, True])
def test_factor_uses_one_current_membership(monkeypatch, batch, explicit_symbols):
    members = [sample.MEMBER_A, sample.MEMBER_C]
    loader = Mock(side_effect=[
        sample._current(members),
        sample._current([sample.MEMBER_B, sample.MEMBER_C]),
    ])
    monkeypatch.setattr("app.hs300.current.load_current_members", loader)
    symbols = sample._UNION if explicit_symbols else None
    engine = sample._EngineStub(sample._panel())
    service = FactorBacktestService(engine)
    if batch:
        result = service.run_batch(FactorBatchConfig(
            factor_names=["turnover_rate"], symbols=symbols,
            start=sample.START, end=sample.END, n_groups=2,
            rebalance="daily", fees_pct=0.0, slippage_bps=0.0, hs300=True,
        ))
        metrics = result.results[0]
    else:
        result = service.run(sample._config(hs300=True, symbols=symbols))
        metrics = result

    assert result.error is None
    assert metrics.n_symbols == 2
    assert metrics.ic_mean == pytest.approx(-0.3333)
    assert loader.call_count == 1
    assert engine.loaded_symbols == (sample._UNION if explicit_symbols else members)
    assert result.config["hs300_scope"] == "current_official"
