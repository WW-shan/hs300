"""HS300 因子动态筛选回归 — 整段因子回测使用官方当前成分。

不再有月度快照 PIT: 当前名单每次由官方接口解析, 整段回测使用同一份名单。
本文件锁定两件事:
- hs300=False 时行为完全不变;
- hs300=True 时只保留当前名单内的股票, 且 symbols 留空时以当前名单为数据加载边界。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.factor import (
    FactorBacktestService,
    FactorBatchConfig,
    FactorConfig,
)
from app.hs300.current import CurrentMembers, CurrentMembersError

MEMBER_A = "600001.SH"
MEMBER_B = "000002.SZ"
MEMBER_C = "600003.SH"
DATES = (
    date(2024, 1, 31),
    date(2024, 2, 1),
    date(2024, 2, 2),
    date(2024, 2, 5),
)
START, END = DATES[0], DATES[-1]

_CLOSES = {
    MEMBER_A: (100.0, 101.0, 1.01, 0.0101),
    MEMBER_B: (1000.0, 10.0, 10.1, 10.201),
    MEMBER_C: (100.0, 102.0, 104.04, 106.1208),
}
_FACTORS = {
    date(2024, 1, 31): {MEMBER_A: 1.0, MEMBER_B: 3.0, MEMBER_C: 2.0},
    date(2024, 2, 1): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
    date(2024, 2, 2): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
    date(2024, 2, 5): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
}
_UNION = [MEMBER_A, MEMBER_B, MEMBER_C]


def _current(symbols: list[str]) -> CurrentMembers:
    return CurrentMembers(
        as_of="2026-09-30",
        source="csindex",
        fetched_at="2026-10-08T12:00:00+08:00",
        members=tuple(
            {"symbol": symbol, "name": f"name-{index}"}
            for index, symbol in enumerate(symbols)
        ),
    )


@pytest.fixture()
def current_hs300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.hs300.current.load_current_members",
        lambda **_kwargs: _current([MEMBER_A, MEMBER_C]),
    )


def _panel() -> pl.DataFrame:
    rows = []
    for symbol in _UNION:
        for index, day in enumerate(DATES):
            close = _CLOSES[symbol][index]
            rows.append({
                "symbol": symbol,
                "date": day,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
                "amount": 10_000.0,
                "turnover_rate": _FACTORS[day][symbol],
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


class _EngineStub:
    """按 symbols/日期/列裁剪合成面板, 不触真实存储。"""

    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.loaded_symbols = None

    def load_panel(
        self,
        symbols,
        start: date,
        end: date,
        columns=None,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        self.loaded_symbols = list(symbols) if symbols is not None else None
        frame = self.panel.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if symbols:
            frame = frame.filter(pl.col("symbol").is_in(symbols))
        if columns is not None:
            frame = frame.select([column for column in columns if column in frame.columns])
        return frame


def _config(**overrides) -> FactorConfig:
    values = {
        "factor_name": "turnover_rate",
        "symbols": _UNION,
        "start": START,
        "end": END,
        "n_groups": 2,
        "rebalance": "daily",
        "fees_pct": 0.0,
        "slippage_bps": 0.0,
    }
    values.update(overrides)
    return FactorConfig(**values)


_UNSET = object()


def _run(hs300: bool, symbols=_UNSET):
    engine = _EngineStub(_panel())
    config = _config(hs300=hs300) if symbols is _UNSET else _config(hs300=hs300, symbols=symbols)
    return FactorBacktestService(engine).run(config), engine


def test_without_hs300_union_behaviour_is_unchanged() -> None:
    result, _ = _run(hs300=False)

    assert result.error is None
    assert result.config["hs300"] is False
    assert result.n_symbols == 3
    assert [(row["date"], row["ic"]) for row in result.ic_series] == [
        ("2024-01-31", -0.5),
        ("2024-02-01", -0.5),
        ("2024-02-02", -0.5),
    ]


def test_current_hs300_filter_keeps_only_official_members(current_hs300: None) -> None:
    result, _ = _run(hs300=True)

    assert result.error is None
    assert result.config["hs300"] is True
    assert result.config["hs300_scope"] == "current_official"
    assert result.n_symbols == 2

    filtered = FactorBacktestService._apply_hs300_membership(_panel(), _config(hs300=True))
    assert set(filtered["symbol"].to_list()) == {MEMBER_A, MEMBER_C}


def test_current_hs300_filter_limits_data_load_boundary_when_symbols_empty(
    current_hs300: None,
) -> None:
    result, engine = _run(hs300=True, symbols=None)

    assert result.error is None
    assert engine.loaded_symbols == [MEMBER_A, MEMBER_C]
    assert result.config["symbols"] == [MEMBER_A, MEMBER_C]


def test_current_hs300_filter_fails_closed_when_official_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs):
        raise CurrentMembersError("官方接口不可用")

    monkeypatch.setattr("app.hs300.current.load_current_members", _boom)
    result, _ = _run(hs300=True)

    assert result.error is not None
    assert "HS300" in result.error


def test_batch_current_filter_matches_single_run(current_hs300: None) -> None:
    service = FactorBacktestService(_EngineStub(_panel()))
    single = service.run(_config(hs300=True))
    batch = service.run_batch(FactorBatchConfig(
        factor_names=["turnover_rate"],
        symbols=_UNION,
        start=START,
        end=END,
        n_groups=2,
        rebalance="daily",
        fees_pct=0.0,
        slippage_bps=0.0,
        hs300=True,
    ))

    assert batch.error is None
    assert batch.config["hs300"] is True
    assert batch.config["hs300_scope"] == "current_official"
    assert batch.results[0].error is None
    assert batch.results[0].ic_mean == single.ic_mean
    assert batch.results[0].yearly_ic == single.yearly_ic


def test_factor_api_passes_hs300_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求模型必须把 hs300 布尔位透传到 FactorConfig/FactorBatchConfig (默认 False)。"""
    from app.api import backtest as bt

    captured: list[object] = []

    @dataclass
    class _DummyResult:
        ok: bool = True

    class _CapturingService:
        def __init__(self, engine) -> None:
            pass

        def run(self, cfg):
            captured.append(cfg)
            return _DummyResult()

        def run_batch(self, cfg):
            captured.append(cfg)
            return _DummyResult()

    monkeypatch.setattr(bt, "_get_engine", lambda request: object())
    monkeypatch.setattr(bt, "_guard_server_backtest_range", lambda start, end: None)
    import app.backtest.factor as factor_mod

    monkeypatch.setattr(factor_mod, "FactorBacktestService", _CapturingService)

    class _Req:
        pass

    bt.factor_run(
        bt.FactorBacktestRequest(factor_name="momentum_5d", hs300=True),
        _Req(),
    )
    bt.factor_batch(
        bt.FactorBatchRequest(factor_names=["momentum_5d"]),
        _Req(),
    )

    assert captured[0].hs300 is True
    assert captured[1].hs300 is False


def test_factor_api_endpoint_uses_current_membership(
    current_hs300: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """因子 HTTP endpoint 使用官方当前名单, 不让名单外成分进入 IC 截面。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest as backtest_api

    monkeypatch.setattr(backtest_api, "_get_engine", lambda request: _EngineStub(_panel()))
    app = FastAPI()
    app.include_router(backtest_api.router)

    response = TestClient(app).post(
        "/api/backtest/factor/run",
        json={
            "factor_name": "turnover_rate",
            "symbols": _UNION,
            "start": START.isoformat(),
            "end": END.isoformat(),
            "n_groups": 2,
            "rebalance": "daily",
            "fees_pct": 0.0,
            "slippage_bps": 0.0,
            "hs300": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["config"]["hs300"] is True
    assert body["config"]["hs300_scope"] == "current_official"
    assert body["n_symbols"] == 2
