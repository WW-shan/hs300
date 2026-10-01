"""pool 过滤的单跑不得污染全局策略缓存。

strategy_cache.json 的键只有 as_of + strategy_id, 没有 pool 维度。HS300 预设用
pool 收窄候选集后若照常写缓存, /cached 与策略卡片会把池内结果当成全市场结果展示。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.api import screener as screener_api
from app.services import strategy_cache


class _Svc:
    def latest_date(self):
        return date(2026, 9, 10)

    def coverage_warnings(self, as_of, *, required_bars=None):
        return []

    def build_strategy_context(self, engine, as_of, strategy_ids, **kwargs):
        return SimpleNamespace(as_of=as_of)


class _Engine:
    def has(self, strategy_id):
        return strategy_id == "s1"

    def get(self, strategy_id):
        return SimpleNamespace(meta={"id": strategy_id})

    def run(self, strategy_id, context, **kwargs):
        from app.services.screener import ScreenerResult
        return ScreenerResult(as_of=context.as_of, strategy=strategy_id)


def _request(tmp_path):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        strategy_engine=_Engine(),
    )))


def _prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(screener_api, "ScreenerService", lambda *a, **k: _Svc())
    monkeypatch.setattr(screener_api, "_load_ext_value_maps", lambda *a, **k: {})
    monkeypatch.setattr(screener_api.strategy_config, "load_override", lambda *a: {})
    strategy_cache.write_cache(
        tmp_path, "2026-09-10",
        {"s1": {"total": 300, "as_of": "2026-09-10", "rows": [{"symbol": "600519.SH"}]}},
    )


def test_pool_filtered_run_preset_keeps_canonical_cache(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)

    resp = screener_api.run_preset(
        screener_api.PresetRequest(
            strategy_id="s1", as_of=date(2026, 9, 10), pool=["600519.SH"],
        ),
        _request(tmp_path),
    )

    assert resp["total"] == 0  # 请求本身按 pool 计算
    cached = strategy_cache.read_cache(tmp_path)
    assert cached["results"]["s1"]["total"] == 300  # 全局缓存未被池内结果覆盖


def test_unfiltered_run_preset_still_updates_cache(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)

    screener_api.run_preset(
        screener_api.PresetRequest(strategy_id="s1", as_of=date(2026, 9, 10)),
        _request(tmp_path),
    )

    cached = strategy_cache.read_cache(tmp_path)
    assert cached["results"]["s1"]["total"] == 0
