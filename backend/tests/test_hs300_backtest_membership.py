"""HS300 动态筛选回归 — 整段回测使用官方当前成分, 不再读取 PIT 归档。

筛选器只维护一份"当前沪深300"名单: 官方接口 -> 6h 缓存 -> fail-closed。
历史回测使用当前名单会有幸存者偏差, 但不会再出现"归档停更导致名单错误"或
"年份被固定"的问题。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.hs300.current import CurrentMembers, CurrentMembersError
from app.strategy.engine import StrategyDef

MEMBER_A = "600001.SH"
MEMBER_B = "000002.SZ"
START = date(2024, 1, 31)
DATES = (START, date(2024, 2, 1), date(2024, 2, 2))


def _current(symbols: list[str]) -> CurrentMembers:
    return CurrentMembers(
        as_of="2024-01-31",
        source="csindex",
        fetched_at="2024-01-31T12:00:00+08:00",
        members=tuple(
            {"symbol": symbol, "name": f"name-{index}"}
            for index, symbol in enumerate(symbols)
        ),
    )


@pytest.fixture()
def current_hs300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.hs300.current.load_current_members",
        lambda **_kwargs: _current([MEMBER_A]),
    )


def _panel() -> pl.DataFrame:
    rows = []
    for symbol, name in ((MEMBER_A, "A"), (MEMBER_B, "B")):
        for index, day in enumerate(DATES):
            price = 10.0 + index
            rows.append({
                "symbol": symbol,
                "name": name,
                "date": day,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000.0,
                "amount": 1000.0,
                "raw_close": price,
                "raw_high": price,
                "signal_limit_up": False,
                "signal_limit_down": False,
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _strategy(**kwargs) -> StrategyDef:
    defaults = dict(
        meta={"id": "test", "name": "test", "scoring": {}, "params": [], "limit": 100},
        basic_filter={"enabled": True, "amount_min": 100.0},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda df, params: pl.lit(True),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=None,
    )
    defaults.update(kwargs)
    return StrategyDef(**defaults)


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _RepoStub:
    def __init__(self, data_dir=None) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)

    def get_index_daily(self, *args, **kwargs) -> pl.DataFrame:
        return pl.DataFrame()


class _EngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = _RepoStub()
        self.sim_matrix = None
        self.loaded_symbols = None

    def load_panel(self, symbols, start: date, end: date, columns=None, asset_type: str = "stock") -> pl.DataFrame:
        self.loaded_symbols = list(symbols) if symbols is not None else None
        return self.panel

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock") -> pl.DataFrame:
        return self.load_panel(symbols, start, end, columns=sorted(feature_plan.base_columns), asset_type=asset_type)

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs,
    ):
        panel = self.load_panel_for_backtest(symbols, start, end, feature_plan, asset_type=asset_type)
        field_columns = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(panel, field_columns=field_columns)

    def simulate_portfolio(self, panel, entries, exits, config, progress_cb=None, cancel_event=None, entry_signal_ids=None, exit_signal_ids=None) -> SimResult:
        return SimResult(
            equity_curve=[], drawdown_curve=[], trades=[], per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )

    def simulate_independent_market_matrix(self, *args, **kwargs) -> SimResult:
        return SimResult(
            equity_curve=[], drawdown_curve=[], trades=[], per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )

    def simulate_market_matrix(self, matrix, config, progress_cb=None, cancel_event=None, options=None) -> SimResult:
        self.sim_matrix = matrix
        return SimResult(
            equity_curve=[], drawdown_curve=[], trades=[], per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def _entry_by_date(result, engine) -> dict[str, dict[str, int]]:
    matrix = engine.sim_matrix
    assert matrix is not None, result.error
    columns = {symbol: index for index, symbol in enumerate(matrix.symbols)}
    return {
        str(label)[:10]: {
            symbol: int(matrix.entry[time_id, columns[symbol]])
            for symbol in columns
        }
        for time_id, label in enumerate(matrix.timestamp_labels)
    }


_UNSET = object()


def _run(hs300: bool, strategy: StrategyDef | None = None, symbols=_UNSET):
    engine = _EngineStub(_panel())
    service = StrategyBacktestService(
        engine=engine,
        strategy_engine=_StrategyEngineStub(strategy or _strategy()),
    )
    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=[MEMBER_A, MEMBER_B] if symbols is _UNSET else symbols,
        start=START,
        end=DATES[-1],
        matching="close_t",
        mode="position",
        hs300=hs300,
    ))
    return result, engine


def test_without_hs300_both_symbols_enter_every_date() -> None:
    result, engine = _run(hs300=False)

    assert result.error is None
    assert _entry_by_date(result, engine) == {
        "2024-01-31": {MEMBER_A: 1, MEMBER_B: 1},
        "2024-02-01": {MEMBER_A: 1, MEMBER_B: 1},
        "2024-02-02": {MEMBER_A: 1, MEMBER_B: 1},
    }


def test_current_hs300_filter_applies_same_official_list_to_every_date(current_hs300: None) -> None:
    result, engine = _run(hs300=True)

    assert result.error is None
    assert _entry_by_date(result, engine) == {
        "2024-01-31": {MEMBER_A: 1, MEMBER_B: 0},
        "2024-02-01": {MEMBER_A: 1, MEMBER_B: 0},
        "2024-02-02": {MEMBER_A: 1, MEMBER_B: 0},
    }
    assert result.config["hs300"] is True
    assert result.config["hs300_scope"] == "current_official"
    assert result.config["symbols"] == [MEMBER_A, MEMBER_B]


def test_current_hs300_filter_limits_data_load_boundary_when_symbols_empty(
    current_hs300: None,
) -> None:
    result, engine = _run(hs300=True, symbols=None)

    assert result.error is None
    assert engine.loaded_symbols == [MEMBER_A]
    assert result.config["symbols"] == [MEMBER_A]


def test_current_hs300_filter_fails_closed_when_official_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs):
        raise CurrentMembersError("官方接口不可用")

    monkeypatch.setattr("app.hs300.current.load_current_members", _boom)
    result, _ = _run(hs300=True)

    assert result.error is not None
    assert "HS300" in result.error


def test_current_hs300_filter_runs_before_scoring_for_matrix_native(
    current_hs300: None,
) -> None:
    class NativeStrategy:
        def required_fields(self):
            return frozenset({"open", "high", "low", "close", "volume"})

        def required_warmup_bars(self, params):
            return 1

        def compute_signals(self, market, params):
            return make_signal_matrix(
                market.shape,
                entry=np.ones(market.shape, dtype=np.uint8),
                score=np.ones(market.shape, dtype=np.float32),
            )

    strategy = _strategy(
        meta={"id": "native", "name": "native", "scoring": {}, "params": [], "limit": 100},
        filter_fn=None,
        execution_backend="matrix_native",
        matrix_strategy=NativeStrategy(),
        required_features=frozenset({"amount"}),
    )
    result, engine = _run(hs300=True, strategy=strategy)

    assert result.error is None
    assert _entry_by_date(result, engine) == {
        "2024-01-31": {MEMBER_A: 1, MEMBER_B: 0},
        "2024-02-01": {MEMBER_A: 1, MEMBER_B: 0},
        "2024-02-02": {MEMBER_A: 1, MEMBER_B: 0},
    }


def test_hs300_flag_survives_worker_config_round_trip() -> None:
    """worker 用 asdict() JSON 传参, hs300 布尔位必须原样往返。"""
    import json

    from app.backtest.worker import _decode_backtest_config, encode_backtest_config

    config = StrategyBacktestConfig(
        strategy_id="test",
        symbols=[MEMBER_A],
        start=START,
        end=DATES[-1],
        hs300=True,
    )
    payload = json.loads(json.dumps(encode_backtest_config(config)))

    decoded = _decode_backtest_config(payload)

    assert decoded.hs300 is True


@pytest.mark.parametrize(
    ("hs300_value", "minute_fill_value"),
    [("true", "true"), ("1", "yes"), ("on", "1")],
)
def test_strategy_cancel_matches_hs300_stream_job_key(
    hs300_value: str,
    minute_fill_value: str,
) -> None:
    import asyncio
    from urllib.parse import urlencode

    from app.api import backtest as backtest_api

    regime_filter = '{"mode":"risk_on"}'
    key = backtest_api._make_job_key(
        "test",
        MEMBER_A,
        "2024-01-01",
        "2024-02-01",
        "open_t+1",
        None,
        None,
        0.0002,
        5.0,
        10,
        1.0,
        1_000_000.0,
        "equal",
        None,
        None,
        asset_type="stock",
        minute_fill=True,
        regime_filter=regime_filter,
        hs300=True,
    )
    query = urlencode({
        "strategy_id": "test",
        "symbols": MEMBER_A,
        "start": "2024-01-01",
        "end": "2024-02-01",
        "minute_fill": minute_fill_value,
        "regime_filter": regime_filter,
        "hs300": hs300_value,
    })

    class _Request:
        async def json(self):
            return {"qs": query}

    job = backtest_api._BacktestJob(key)
    backtest_api._running_jobs[key] = job
    try:
        response = asyncio.run(backtest_api.strategy_cancel(_Request()))
    finally:
        backtest_api._running_jobs.pop(key, None)

    assert response == {"ok": True}
    assert job.cancel_event.is_set()


def test_strategy_api_uses_current_membership(
    current_hs300: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过策略回测 API/worker 接线验证请求使用官方当前名单, 不退化成归档并集。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest as backtest_api
    from app.backtest import worker as worker_api

    monkeypatch.setattr(worker_api, "make_worker_task", lambda kind, data_dir, config: config)

    def _run_worker(config):
        engine = _EngineStub(_panel())
        service = StrategyBacktestService(
            engine=engine,
            strategy_engine=_StrategyEngineStub(_strategy()),
        )
        result = service.run(config)
        return {"error": result.error, "entries": _entry_by_date(result, engine)}

    monkeypatch.setattr(worker_api, "run_worker_task", _run_worker)
    app = FastAPI()
    app.include_router(backtest_api.router)

    response = TestClient(app).post(
        "/api/backtest/strategy/run",
        json={
            "strategy_id": "test",
            "symbols": [MEMBER_A, MEMBER_B],
            "start": START.isoformat(),
            "end": DATES[-1].isoformat(),
            "matching": "close_t",
            "mode": "position",
            "hs300": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "error": None,
        "entries": {
            "2024-01-31": {MEMBER_A: 1, MEMBER_B: 0},
            "2024-02-01": {MEMBER_A: 1, MEMBER_B: 0},
            "2024-02-02": {MEMBER_A: 1, MEMBER_B: 0},
        },
    }
