"""HS300 因子回测 PIT 成分过滤回归 — 因子截面只含当日生效成分。

因子预设若只把选定快照的成分当静态 symbols 传进来, 已调出成分会在非成员日继续
参与 IC/分层/多空, 属于幸存者偏差的对偶错误, 与策略回测的逐日口径不一致。

夹具: 2024-01 快照 = {A, C}, 2024-02 快照 = {B, C}; 4 个交易日:
- 未启用 hs300: 静态并集不变, 三个标的每个截面都参与 (coverage=1);
- 启用 hs300: 每日只放行当日成分, A 在 1 月最后一日、B 在 2 月首日进入截面;
- A/B 在非成员日的收盘价走势与因子排序相反: 过滤前 IC=-0.5, 过滤后 IC=+1,
  据此锁定"按日过滤"而非"A 在 2 月在不在";
- A 在 2024-01-31 的下一期收益必须取 02-01 收盘价 (先算收益、后按日过滤),
  否则首日截面只剩 C 一只、IC 丢失。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.factor import (
    FactorBacktestService,
    FactorBatchConfig,
    FactorConfig,
)

MEMBER_A = "600001.SH"
MEMBER_B = "000002.SZ"
MEMBER_C = "600003.SH"
DATES = (
    date(2024, 1, 31),  # 1 月快照最后一天
    date(2024, 2, 1),   # 2 月快照生效
    date(2024, 2, 2),
    date(2024, 2, 5),   # 无下期收益, 只作为 02-02 的目标日
)
START, END = DATES[0], DATES[-1]

_CLOSES = {
    MEMBER_A: (100.0, 101.0, 1.01, 0.0101),        # 01-31→02-01 +1%, 之后 -99%
    MEMBER_B: (1000.0, 10.0, 10.1, 10.201),        # 01-31→02-01 -99%, 之后 +1%
    MEMBER_C: (100.0, 102.0, 104.04, 106.1208),    # 每日 +2%
}
_FACTORS = {
    date(2024, 1, 31): {MEMBER_A: 1.0, MEMBER_B: 3.0, MEMBER_C: 2.0},
    date(2024, 2, 1): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
    date(2024, 2, 2): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
    date(2024, 2, 5): {MEMBER_A: 3.0, MEMBER_B: 1.0, MEMBER_C: 2.0},
}
_UNION = [MEMBER_A, MEMBER_B, MEMBER_C]


def _snapshot(root: Path, year: int, month: int, symbols: list[str]) -> None:
    path = root / f"{year:04d}" / f"{month:02d}" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"Symbol": symbols, "Name": [f"name-{i}" for i in range(len(symbols))]}
    ).write_csv(path)


@pytest.fixture()
def hs300_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "docs"
    _snapshot(root, 2024, 1, ["600001.SS", "600003.SS"])  # 1 月: A, C
    _snapshot(root, 2024, 2, ["000002.SZ", "600003.SS"])  # 2 月: B, C
    monkeypatch.setattr("app.config.settings.hs300_snapshot_dir", root)
    return root


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

    def load_panel(
        self,
        symbols,
        start: date,
        end: date,
        columns=None,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        frame = self.panel.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if symbols:
            frame = frame.filter(pl.col("symbol").is_in(symbols))
        if columns is not None:
            frame = frame.select([column for column in columns if column in frame.columns])
        return frame


def _config(**overrides) -> FactorConfig:
    values = {
        "factor_name": "turnover_rate",  # 面板物理列, 直接作为受控因子值
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


def _run(hs300: bool):
    return FactorBacktestService(_EngineStub(_panel())).run(_config(hs300=hs300))


def test_static_union_keeps_pre_pit_behaviour_when_disabled(hs300_root: Path) -> None:
    result = _run(hs300=False)

    assert result.error is None
    assert result.config["hs300"] is False
    assert result.coverage == pytest.approx(1.0)
    # 静态并集: 非成员日的反向走势把 IC 拉到 -0.5
    assert [(row["date"], row["ic"]) for row in result.ic_series] == [
        ("2024-01-31", -0.5),
        ("2024-02-01", -0.5),
        ("2024-02-02", -0.5),
    ]


def test_pit_filter_only_allows_snapshot_member_per_date(hs300_root: Path) -> None:
    result = _run(hs300=True)

    assert result.error is None
    assert result.config["hs300"] is True
    assert result.config["hs300_membership_range"] == {
        "start": START.isoformat(),
        "end": END.isoformat(),
    }
    assert [(row["date"], row["ic"]) for row in result.ic_series] == [
        ("2024-01-31", 1.0),
        ("2024-02-01", 1.0),
        ("2024-02-02", 1.0),
    ]
    # 行级口径: 每日只保留当日生效快照成分
    filtered = FactorBacktestService._apply_hs300_membership(_panel(), _config(hs300=True))
    assert {(str(d), s) for d, s in zip(filtered["date"], filtered["symbol"], strict=True)} == {
        ("2024-01-31", MEMBER_A),
        ("2024-01-31", MEMBER_C),
        ("2024-02-01", MEMBER_B),
        ("2024-02-01", MEMBER_C),
        ("2024-02-02", MEMBER_B),
        ("2024-02-02", MEMBER_C),
        ("2024-02-05", MEMBER_B),
        ("2024-02-05", MEMBER_C),
    }


def test_pit_filter_keeps_forward_return_of_last_membership_day(hs300_root: Path) -> None:
    """A 在 02-01 已不是成分, 但 01-31 截面的下期收益仍必须用 A 的 02-01 收盘价。"""
    result = _run(hs300=True)

    assert result.error is None
    first = next(row for row in result.ic_series if row["date"] == "2024-01-31")
    # 若先过滤再算收益, 01-31 截面只剩 C 一只 → 该日 IC 缺失
    assert first["ic"] == 1.0


def test_pit_filter_fails_closed_before_first_snapshot(hs300_root: Path) -> None:
    result = FactorBacktestService(_EngineStub(_panel())).run(
        _config(start=date(2023, 6, 1), hs300=True)
    )

    assert result.error is not None
    assert "HS300" in result.error


def test_batch_pit_filter_matches_single_run(hs300_root: Path) -> None:
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
    assert batch.config["hs300_membership_range"] == {
        "start": START.isoformat(),
        "end": END.isoformat(),
    }
    assert batch.results[0].error is None
    assert batch.results[0].ic_mean == single.ic_mean == pytest.approx(1.0)
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


def test_factor_api_endpoint_returns_pit_factor_results_from_fixture(
    hs300_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """因子 HTTP endpoint 按快照逐日评分, 不让快照外成分进入 IC 截面。"""
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
    assert body["config"]["start"] == START.isoformat()
    assert body["config"]["end"] == END.isoformat()
    assert [(row["date"], row["ic"]) for row in body["ic_series"]] == [
        ("2024-01-31", 1.0),
        ("2024-02-01", 1.0),
        ("2024-02-02", 1.0),
    ]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/backtest/factor/run",
            {"factor_name": "turnover_rate", "start": "2023-06-30", "end": "2023-07-01"},
        ),
        (
            "/api/backtest/factor/batch",
            {"factor_names": ["turnover_rate"], "start": "2023-06-30", "end": "2023-07-01"},
        ),
    ],
)
def test_factor_api_rejects_hs300_range_before_first_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest as backtest_api

    monkeypatch.setattr(
        backtest_api,
        "_get_engine",
        lambda request: pytest.fail("engine must not load for dates before the first snapshot"),
    )
    app = FastAPI()
    app.include_router(backtest_api.router)

    response = TestClient(app).post(path, json={**payload, "hs300": True})

    assert response.status_code == 422
    assert "2023-07-01" in response.json()["detail"]
