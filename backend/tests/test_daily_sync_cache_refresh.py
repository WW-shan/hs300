from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.services import extend_history, preferences, repair_daily


class _CacheSpy:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)
        self.calls: list[str] = []

    def earliest_daily_date(self):
        return date(2025, 9, 17)

    def clear_cache(self):
        self.calls.append("clear")

    def refresh_cache(self):
        self.calls.append("refresh")


def test_extend_history_rebuilds_repository_cache_after_enriched_write(monkeypatch, tmp_path):
    repo = _CacheSpy(tmp_path / "data")
    monkeypatch.setattr(extend_history, "_resolve_universe", lambda _capset: ["000001.SZ"])
    monkeypatch.setattr(extend_history.kline_sync, "sync_and_persist_daily_batch", lambda *a, **k: 5)
    monkeypatch.setattr(preferences, "get_adj_factor_provider", lambda: "tickflow")
    monkeypatch.setattr(extend_history, "_refresh_single_view", lambda *_args: None)
    monkeypatch.setattr(extend_history, "_invalidate", lambda *_args: None)
    monkeypatch.setattr("app.indicators.pipeline.run_pipeline", lambda: 5)

    result = extend_history.run_extend_history(
        repo,
        SimpleNamespace(has=lambda _cap: False),
        value=1,
        unit="year",
    )

    assert result["daily_rows"] == 5
    assert repo.calls == ["clear", "refresh"]


def test_repair_daily_rebuilds_repository_cache_after_pipeline(monkeypatch, tmp_path):
    repo = _CacheSpy(tmp_path / "data")
    expected = {"universe_size": 1}
    monkeypatch.setattr("app.jobs.daily_pipeline.run_now", lambda *_a, **_k: expected)

    result = repair_daily.run_repair_daily(
        repo,
        SimpleNamespace(has=lambda _cap: True),
        start_date=date(2025, 1, 1),
    )

    assert result is expected
    assert repo.calls == ["clear", "refresh"]
