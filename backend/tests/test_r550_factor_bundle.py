from __future__ import annotations

import json
from pathlib import Path

from app.factors import store

ROOT = Path(__file__).resolve().parents[2]
FACTOR_DIR = ROOT / "data" / "user_data" / "custom_factors"


def _definition(factor_id: str) -> dict:
    return json.loads((FACTOR_DIR / f"{factor_id}.json").read_text(encoding="utf-8"))


def test_r550_price_legs_compile_to_declared_windows() -> None:
    expected = {
        "uf_r550_amount_120": 121,
        "uf_r550_turnover_120": 121,
        "uf_r550_max_return_60": 62,
        "uf_r550_boll_width_10": 11,
    }

    for factor_id, warmup in expected.items():
        spec = store.to_spec(_definition(factor_id))
        assert spec.kind == "custom"
        assert spec.warmup_bars == warmup
        assert spec.direction == "low"


def test_r550_financial_composites_keep_frozen_signs() -> None:
    cash = _definition("cf_r550_cash_quality")["members"]
    safety = _definition("cf_r550_value_quality_safety")["members"]

    assert cash == {
        "ocf_ni": 0.3,
        "ocf_to_revenue_ttm": 0.2,
        "fcf_proxy_to_revenue_ttm": 0.2,
        "accrual": -0.3,
    }
    assert safety == {
        "pb_latest": -0.25,
        "debt_ratio_latest": -0.25,
        "ocf_debt": 0.25,
        "accrual": -0.25,
    }


def test_r550_final_bundles_keep_nested_cash_safety_structure() -> None:
    stable7 = _definition("cf_r550_stable7")["members"]
    final = _definition("cf_r550_cash_safety")["members"]

    assert len(stable7) == 7
    assert stable7["uf_r550_amount_120"] < 0
    assert stable7["uf_r550_turnover_120"] < 0
    assert stable7["uf_r550_max_return_60"] < 0
    assert stable7["uf_r550_boll_width_10"] < 0
    assert stable7["uf_r550_dividend_q70"] > 0
    assert stable7["uf_r550_fund_holding_n005"] > 0
    assert stable7["ext_alt_hs300_vol_of_vol_20d_low"] > 0
    assert final == {
        "cf_r550_stable7": 0.333333,
        "cf_r550_cash_quality": 0.333333,
        "cf_r550_value_quality_safety": 0.333334,
    }


def test_r550_rank_composite_uses_all_nine_signed_legs() -> None:
    formula = _definition("uf_r550_cash_safety_rank")["formula"]

    for positive in (
        "uf_r550_dividend_q70",
        "uf_r550_fund_holding_n005",
        "ext_alt_hs300_vol_of_vol_20d_low",
        "cf_r550_cash_quality",
        "cf_r550_value_quality_safety",
    ):
        assert f"rank({positive})" in formula
    for negative in (
        "uf_r550_amount_120",
        "uf_r550_turnover_120",
        "uf_r550_max_return_60",
        "uf_r550_boll_width_10",
    ):
        assert f"- rank({negative})" in formula
