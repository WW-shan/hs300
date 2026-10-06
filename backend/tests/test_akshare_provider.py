"""AkShare plugin contract tests; upstream responses are injected, never synthetic runtime data."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import polars as pl
import pytest
import yaml


def _module():
    from app.plugins.akshare import provider

    return provider


def test_manifest_declares_only_implemented_datasets():
    provider_mod = _module()
    manifest_path = Path(provider_mod.__file__).with_name("plugin.yaml")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "akshare"
    assert set(manifest["datasets"]) == {
        "daily", "adj_factor", "financial", "minute", "realtime",
    }
    assert set(provider_mod._AkShareConfig().datasets) == set(manifest["datasets"])


def test_trading_days_normalizes_akshare_calendar(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def tool_trade_date_hist_sina():
            return pd.DataFrame({
                "trade_date": ["2026-09-30", "2026-10-08"],
            })

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod, "_call_akshare", lambda function, **kwargs: function(**kwargs),
    )

    days = provider_mod.AkShareProvider().trading_days()

    assert days == {date(2026, 9, 30), date(2026, 10, 8)}


def test_trading_days_rejects_empty_or_invalid_calendar(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def tool_trade_date_hist_sina():
            return pl.DataFrame({"trade_date": [None, "not-a-date"]})

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod, "_call_akshare", lambda function, **kwargs: function(**kwargs),
    )

    with pytest.raises(provider_mod.AkShareProviderError, match="交易日历为空"):
        provider_mod.AkShareProvider().trading_days()


def test_qfq_cumulative_factors_become_single_event_factors(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_daily(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame(
                {
                    "date": ["2023-07-01", "2023-07-03", "2023-08-01", "2023-09-01"],
                    "qfq_factor": [4.0, 4.0, 2.0, 1.0],
                }
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600000.SH"},
    )
    progress = []

    frame = provider_mod.AkShareProvider().get_adj_factors(
        ["600000.SH", "000001.SZ"],
        datetime(2023, 7, 2),
        datetime(2023, 8, 31),
        on_chunk_done=lambda current, total: progress.append((current, total)),
    )

    assert calls == [
        {
            "symbol": "sh600000",
            "start_date": "20230702",
            "end_date": "20230831",
            "adjust": "qfq-factor",
        }
    ]
    assert frame.to_dicts() == [
        {"symbol": "600000.SH", "trade_date": date(2023, 8, 1), "ex_factor": 2.0},
    ]
    assert progress == [(1, 1)]


def test_factor_conversion_matches_forward_adjustment_contract(monkeypatch):
    provider_mod = _module()
    import app.indicators.pipeline as pipeline

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_daily(**kwargs):
            return pd.DataFrame(
                {
                    "date": ["2023-07-01", "2023-08-01", "2023-09-01", "2023-10-01"],
                    "qfq_factor": [4.0, 2.0, 1.0, 1.0],
                }
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600000.SH"},
    )
    factors = provider_mod.AkShareProvider().get_adj_factors(
        ["600000.SH"], date(2023, 7, 1), date(2023, 10, 1)
    )
    raw = pl.DataFrame(
        {
            "symbol": ["600000.SH"] * 5,
            "date": [
                date(2023, 7, 31),
                date(2023, 8, 1),
                date(2023, 8, 31),
                date(2023, 9, 1),
                date(2023, 9, 2),
            ],
            "close": [40.0, 20.0, 22.0, 10.0, 11.0],
        }
    ).sort(["symbol", "date"])

    adjusted = pipeline._apply_adj_factor(raw, factors)

    qfq_factor = {
        date(2023, 7, 31): 4.0,
        date(2023, 8, 1): 2.0,
        date(2023, 8, 31): 2.0,
        date(2023, 9, 1): 1.0,
        date(2023, 9, 2): 1.0,
    }
    expected = [
        close / qfq_factor[day]
        for day, close in zip(raw["date"].to_list(), raw["close"].to_list(), strict=True)
    ]
    assert adjusted["close"].to_list() == expected


def test_adj_factor_empty_or_invalid_payload_fails_closed(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_daily(**kwargs):
            return pd.DataFrame({"unexpected": [1]})

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600000.SH"},
    )

    with pytest.raises(provider_mod.AkShareProviderError, match="factor"):
        provider_mod.AkShareProvider().get_adj_factors(
            ["600000.SH"], datetime(2023, 7, 1), datetime(2023, 8, 1)
        )


def test_adj_factor_empty_payload_does_not_look_like_success(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_daily(**kwargs):
            return pd.DataFrame()

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600000.SH"},
    )

    with pytest.raises(provider_mod.AkShareProviderError, match="为空"):
        provider_mod.AkShareProvider().get_adj_factors(
            ["600000.SH"], datetime(2023, 7, 1), datetime(2023, 8, 1)
        )


def test_financial_metrics_use_report_and_announcement_dates(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_yjbb_em(date):
            calls.append(("metrics", date))
            return pd.DataFrame(
                [
                    {
                        "股票代码": "600000",
                        "每股收益": 0.35,
                        "营业总收入-营业总收入": 1000.0,
                        "营业总收入-同比增长": 8.5,
                        "净利润-净利润": 120.0,
                        "净利润-同比增长": 12.0,
                        "每股净资产": 10.0,
                        "净资产收益率": 9.5,
                        "销售毛利率": 35.0,
                        "最新公告日期": "2026-08-25",
                    }
                ]
            )

        @staticmethod
        def stock_zcfz_em(date):
            calls.append(("balance", date))
            return pd.DataFrame(
                [
                    {
                        "股票代码": "600000",
                        "资产-总资产": 5000.0,
                        "负债-总负债": 3000.0,
                        "公告日期": "2026-08-24",
                    }
                ]
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(provider_mod, "_today_cn", lambda: date(2026, 10, 1))
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600519.SH"},
    )

    frame = provider_mod.AkShareProvider().get_financials(
        "metrics", ["600000.SH", "000001.SZ"], latest_only=True
    )

    assert calls == [("metrics", "20260630"), ("balance", "20260630")]
    assert frame.select(
        "symbol",
        "period_end",
        "announce_date",
        "eps_basic",
        "bps",
        "roe",
        "gross_margin",
        "revenue_yoy",
        "net_income_yoy",
        "debt_to_asset_ratio",
    ).to_dicts() == [
        {
            "symbol": "600000.SH",
            "period_end": "2026-06-30",
            "announce_date": "2026-08-25",
            "eps_basic": 0.35,
            "bps": 10.0,
            "roe": 9.5,
            "gross_margin": 35.0,
            "revenue_yoy": 8.5,
            "net_income_yoy": 12.0,
            "debt_to_asset_ratio": 60.0,
        }
    ]


def test_financial_history_covers_2021_q1_through_safe_current_report(monkeypatch):
    provider_mod = _module()
    periods = provider_mod._financial_report_periods(date(2026, 10, 1))

    assert periods[0] == date(2021, 3, 31)
    assert periods[-1] == date(2026, 6, 30)
    assert len(periods) == 22
    assert provider_mod._latest_report_period(date(2026, 10, 1)) == date(2026, 6, 30)


def test_financials_use_notice_date_not_report_period_as_pit_date(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_lrb_em(date):
            return pd.DataFrame(
                [
                    {
                        "股票代码": "000001",
                        "净利润": 200.0,
                        "净利润同比": 7.0,
                        "营业总收入": 1500.0,
                        "营业总收入同比": 5.0,
                        "营业利润": 240.0,
                        "利润总额": 250.0,
                        "公告日期": "2026-08-30",
                    }
                ]
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_financial_report_periods",
        lambda as_of: [date(2026, 6, 30)],
    )

    frame = provider_mod.AkShareProvider().get_financials(
        "income", ["000001.SZ"], latest_only=False
    )

    assert frame.select("symbol", "period_end", "announce_date", "net_income_yoy").to_dicts() == [
        {
            "symbol": "000001.SZ",
            "period_end": "2026-06-30",
            "announce_date": "2026-08-30",
            "net_income_yoy": 7.0,
        }
    ]


def test_financials_do_not_invent_missing_announcement_date(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_lrb_em(date):
            return pd.DataFrame(
                [{
                    "股票代码": "000001",
                    "净利润": 200.0,
                    "净利润同比": 7.0,
                    "营业总收入": 1500.0,
                    "营业总收入同比": 5.0,
                    "公告日期": None,
                }]
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_financial_report_periods",
        lambda as_of: [date(2026, 6, 30)],
    )

    frame = provider_mod.AkShareProvider().get_financials(
        "income", ["000001.SZ"], latest_only=False
    )

    assert frame["period_end"].to_list() == ["2026-06-30"]
    assert frame["announce_date"].to_list() == [None]


def test_financials_reject_schema_without_an_announcement_date_field(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_lrb_em(date):
            return pd.DataFrame([{"股票代码": "000001", "净利润": 200.0}])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_financial_report_periods",
        lambda as_of: [date(2026, 6, 30)],
    )

    with pytest.raises(provider_mod.AkShareProviderError, match="公告日期"):
        provider_mod.AkShareProvider().get_financials(
            "income", ["000001.SZ"], latest_only=False
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (600000.0, "600000.SH"),
        ("000001", "000001.SZ"),
        ("430047.BJ", "430047.BJ"),
        ("600000.SZ", None),
    ],
)
def test_symbol_normalization_handles_numeric_codes_and_rejects_bad_suffix(raw, expected):
    assert _module()._normalize_symbol(raw) == expected


def test_daily_uses_raw_sina_bars_and_converts_share_volume_to_hands(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_daily(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame(
                [{
                    "date": "2026-09-30",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 12345,
                    "amount": 1265000.0,
                }]
            )

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    frame = provider_mod.AkShareProvider().get_daily(
        ["600519.SH"], date(2026, 9, 30), date(2026, 9, 30)
    )

    assert calls == [{
        "symbol": "sh600519",
        "start_date": "20260930",
        "end_date": "20260930",
        "adjust": "",
    }]
    assert frame.to_dicts() == [{
        "symbol": "600519.SH",
        "date": date(2026, 9, 30),
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "volume": 123,
        "amount": 1265000.0,
    }]


def test_index_and_etf_daily_use_raw_asset_endpoints(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_zh_index_daily(**kwargs):
            calls.append(("index", kwargs))
            return pd.DataFrame([{
                "date": "2026-09-30", "open": 4500, "high": 4510,
                "low": 4490, "close": 4505, "volume": 12000,
            }])

        @staticmethod
        def fund_etf_hist_sina(**kwargs):
            calls.append(("etf", kwargs))
            return pd.DataFrame([{
                "date": "2026-09-30", "open": 4.4, "high": 4.5,
                "low": 4.3, "close": 4.45, "volume": 12345,
                "amount": 55000,
            }])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    provider = provider_mod.AkShareProvider()
    index_frame = provider.get_daily(
        ["000300.SH"], date(2026, 9, 30), date(2026, 9, 30), asset_type="index"
    )
    etf_frame = provider.get_daily(
        ["510300.SH"], date(2026, 9, 30), date(2026, 9, 30), asset_type="etf"
    )

    assert calls == [
        ("index", {"symbol": "sh000300"}),
        ("etf", {"symbol": "sh510300"}),
    ]
    assert index_frame["symbol"].to_list() == ["000300.SH"]
    assert index_frame["volume"].to_list() == [120]
    assert etf_frame["symbol"].to_list() == ["510300.SH"]
    assert etf_frame["volume"].to_list() == [123]


def test_instrument_lists_normalize_stock_index_and_etf_codes(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_info_a_code_name():
            return pd.DataFrame([{"code": "600519", "name": "贵州茅台"}])

        @staticmethod
        def stock_zh_index_spot_sina():
            return pd.DataFrame([{"代码": "sh000300", "名称": "沪深300"}])

        @staticmethod
        def fund_etf_spot_ths():
            return pd.DataFrame([{"基金代码": "510300", "基金名称": "沪深300ETF"}])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    provider = provider_mod.AkShareProvider()

    assert provider.get_instruments("stock")[0]["symbol"] == "600519.SH"
    assert provider.get_instruments("index")[0]["symbol"] == "000300.SH"
    assert provider.get_instruments("etf")[0]["symbol"] == "510300.SH"


def test_realtime_snapshots_convert_percent_and_volume_units(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_spot():
            return pd.DataFrame([{
                "代码": "sh600519", "名称": "贵州茅台", "最新价": 103.0,
                "昨收": 100.0, "今开": 101.0, "最高": 105.0, "最低": 99.0,
                "涨跌额": 3.0, "涨跌幅": 3.0, "成交量": 12345,
                "成交额": 1265000.0,
            }])

        @staticmethod
        def stock_zh_index_spot_sina():
            return pd.DataFrame([{
                "代码": "sh000001", "名称": "上证指数", "最新价": 3003.0,
                "昨收": 3000.0, "今开": 3001.0, "最高": 3005.0,
                "最低": 2999.0, "涨跌额": 3.0, "涨跌幅": 0.1,
                "成交量": 12345, "成交额": 1265000.0,
            }])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    provider = provider_mod.AkShareProvider()
    stock = provider.get_realtime()[0]
    index = provider.get_realtime_indices(["000001.SH"])[0]

    assert stock["symbol"] == "600519.SH"
    assert stock["change_pct"] == 0.03
    assert stock["volume"] == 123
    assert index["symbol"] == "000001.SH"
    assert index["change_pct"] == 0.001
    assert index["volume"] == 123


def test_realtime_snapshot_preserves_sina_timestamp_as_epoch_ms(monkeypatch):
    provider_mod = _module()
    source_day = date(2026, 10, 4)
    source_time = f"{source_day.isoformat()} 15:00:02"
    expected_timestamp = int(
        datetime.fromisoformat(source_time)
        .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        .timestamp() * 1000
    )

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_spot():
            return pd.DataFrame([{
                "代码": "sh600519", "最新价": 103.0, "昨收": 100.0,
                "今开": 101.0, "最高": 105.0, "最低": 99.0,
                "成交量": 12345, "成交额": 1265000.0, "时间戳": source_time,
            }])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)

    row = provider_mod.AkShareProvider().get_realtime()[0]

    assert row["timestamp"] == expected_timestamp
    from app.services import quote_service

    monkeypatch.setattr(quote_service, "cn_today", lambda: source_day)
    daily = quote_service.QuoteService._build_daily([row])
    assert daily["quote_ts"].to_list() == [expected_timestamp]


def test_minute_history_is_beijing_wallclock_with_volume_in_hands(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_zh_a_minute(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame([{
                "day": "2026-09-30 09:31:00", "open": "100.0",
                "high": "101.0", "low": "99.0", "close": "100.5",
                "volume": "12345", "amount": "1240000",
            }])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    frame = provider_mod.AkShareProvider().get_minute(
        ["600519.SH"], datetime(2026, 9, 30, 9, 30),
        datetime(2026, 9, 30, 15), freq="1m",
    )

    assert calls == [{"symbol": "sh600519", "period": "1", "adjust": ""}]
    assert frame.to_dicts() == [{
        "symbol": "600519.SH",
        "datetime": datetime(2026, 9, 30, 9, 31),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 123,
        "amount": 1240000.0,
    }]


def test_share_change_history_uses_effective_and_announcement_dates(monkeypatch):
    provider_mod = _module()
    calls = []

    class FakeAkShare:
        @staticmethod
        def stock_share_change_cninfo(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame([{
                "证券代码": "600519",
                "变动日期": date(2020, 6, 30),
                "公告日期": date(2020, 7, 29),
                "总股本": 125619.78,
                "已流通股份": 125619.78,
            }])

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(provider_mod, "_today_cn", lambda: date(2026, 10, 1))
    frame = provider_mod.AkShareProvider().get_financials(
        "shares", ["600519.SH"], latest_only=False
    )

    assert calls == [{
        "symbol": "600519",
        "start_date": "20091227",
        "end_date": "20261001",
    }]
    assert frame.to_dicts() == [{
        "symbol": "600519.SH",
        "period_end": "2020-06-30",
        "announce_date": "2020-07-29",
        "total_shares": 1256197800.0,
        "float_shares": 1256197800.0,
    }]


def test_empty_cninfo_share_history_is_not_reported_as_a_provider_failure(monkeypatch):
    provider_mod = _module()

    class FakeAkShare:
        @staticmethod
        def stock_share_change_cninfo(**kwargs):
            # AkShare 1.19.1 raises this when CNINFO returns records=[] because
            # its adapter indexes the missing "公告日期" column unconditionally.
            raise KeyError("公告日期")

    monkeypatch.setattr(provider_mod, "_akshare", lambda: FakeAkShare)
    monkeypatch.setattr(
        provider_mod,
        "_hs300_symbols_for_range",
        lambda start, end: {"600837.SH"},
    )

    frame = provider_mod.AkShareProvider().get_financials(
        "shares", ["600837.SH"], latest_only=False
    )

    assert frame.is_empty()
    assert frame.columns == [
        "symbol", "period_end", "announce_date", "total_shares", "float_shares",
    ]
