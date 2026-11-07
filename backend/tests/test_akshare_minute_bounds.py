"""Minute bounds accept whole dates without discarding the final trading day."""
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from app.plugins.akshare import provider


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ("2026-09-30", ["09:31", "15:00"]),
        ("20260930", ["09:31", "15:00"]),
        (date(2026, 9, 30), ["09:31", "15:00"]),
        ("2026-09-30T09:31:00", ["09:31"]),
        ("2026-09-30T01:31:00+00:00", ["09:31"]),
        (datetime(2026, 9, 30), []),
    ],
)
def test_minute_end_date_includes_day_but_explicit_times_stay_exact(monkeypatch, end, expected):
    stamps = [
        "2026-09-29 15:00:00", "2026-09-30 09:31:00",
        "2026-09-30 15:00:00", "2026-10-01 09:31:00",
    ]
    raw = pd.DataFrame([
        {"day": stamp, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100}
        for stamp in stamps
    ])
    fake = SimpleNamespace(stock_zh_a_minute=lambda **_kwargs: raw)
    monkeypatch.setattr(provider, "_akshare", lambda: fake)
    monkeypatch.setattr(provider, "_call_sina_symbol", lambda function, **kwargs: function(**kwargs))

    frame = provider.AkShareProvider().get_minute(["600519.SH"], "2026-09-30", end)

    assert [stamp.strftime("%H:%M") for stamp in frame["datetime"]] == expected
