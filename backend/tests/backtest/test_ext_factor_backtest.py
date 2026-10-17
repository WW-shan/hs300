"""扩展因子必须真正进入 polars_expr 策略回测的面板。

回归点：ext_* 因子不在 enriched parquet 中，若依赖解析只把它们当普通
base 列，回测会在评分时静默缺列或产生零候选。这里同时断言依赖计划保留
ext_columns，以及 BacktestEngine 在加载后把时序扩展列注入面板。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import ResolvedFeaturePlan, StrategyDependencyResolver
from app.factors import ext_factors
from app.services.ext_data import (
    ExtConfig,
    ExtConfigStore,
    ExtField,
    _load_all_cache,
    write_ext_parquet,
)
from app.strategy.engine import StrategyDef

COL = "ext_alt_test_hot"


@pytest.fixture(autouse=True)
def _signal_defs(monkeypatch):
    from tests.signal_seeds import install_pipeline_caches

    install_pipeline_caches(monkeypatch)


@pytest.fixture(autouse=True)
def _clean_ext_caches():
    _load_all_cache.clear()
    ext_factors._frame_cache.clear()
    ext_factors._sync_state = None
    yield
    _load_all_cache.clear()
    ext_factors._frame_cache.clear()
    ext_factors._sync_state = None


@pytest.fixture
def ext_data_dir(tmp_path, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    cfg = ExtConfig(
        id="alt_test",
        label="测试扩展因子",
        mode="timeseries",
        fields=[ExtField("hot", "float", "热度")],
    )
    ExtConfigStore(tmp_path).upsert(cfg)
    write_ext_parquet(
        pl.DataFrame({"symbol": ["A"], "hot": [0.8]}),
        cfg,
        tmp_path,
        snapshot_date=date(2024, 1, 2),
    )
    ext_factors.invalidate_ext_caches(tmp_path)
    ext_factors.ensure_synced(tmp_path)
    return tmp_path


def _strategy() -> StrategyDef:
    return StrategyDef(
        meta={"id": "ext_test", "name": "ext test", "scoring": {COL: 1.0}, "params": [], "limit": 10},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda df, params: pl.col(COL).is_not_null(),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=None,
    )


def test_resolver_preserves_ext_columns(ext_data_dir):
    plan = StrategyDependencyResolver().resolve(
        _strategy(),
        params={},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
    )
    assert plan.ext_columns == frozenset({COL})


def test_engine_attaches_ext_columns_to_backtest_panel(ext_data_dir, monkeypatch):
    panel = pl.DataFrame({
        "symbol": ["A"],
        "date": [date(2024, 1, 2)],
        "open": [10.0],
        "high": [10.2],
        "low": [9.8],
        "close": [10.0],
        "volume": [1000.0],
        "amount": [10000.0],
    })
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=ext_data_dir),
        get_instruments_asset=lambda asset_type: pl.DataFrame(),
    )
    engine = BacktestEngine(repo)
    monkeypatch.setattr(engine, "load_panel", lambda *args, **kwargs: panel)
    plan = ResolvedFeaturePlan(
        base_columns=frozenset({"symbol", "date", "open", "high", "low", "close", "volume", "amount"}),
        intermediate_columns=frozenset(),
        indicator_columns=frozenset(),
        signal_columns=frozenset(),
        matrix_columns=frozenset(),
        instrument_columns=frozenset(),
        warmup_bars=1,
        ext_columns=frozenset({COL}),
    )
    out = engine.load_panel_for_backtest(
        ["A"], date(2024, 1, 2), date(2024, 1, 2), plan,
    )
    assert COL in out.columns
    assert out[COL].to_list() == [0.8]
