from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.factor_research.fingerprints import sha256_json

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
P9_RESULT = DATA / "factor_research" / "research" / "p9_financial_regime_2026-11-12_v3.json"
SCRIPT = ROOT / "scripts" / "research_financial_regime_p9.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "research_financial_regime_p9", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_regime_mask_uses_previous_trading_state() -> None:
    module = _module()
    dates = pl.DataFrame(
        {
            "date": [
                date(2026, 1, 5),
                date(2026, 1, 6),
                date(2026, 1, 7),
            ]
        }
    )
    history = pl.DataFrame(
        {
            "date": [
                date(2026, 1, 5),
                date(2026, 1, 6),
                date(2026, 1, 7),
            ],
            "state": ["strong", "weak", "weak"],
        }
    )

    mask = module.regime_mask_t_minus_one(dates, history)

    assert mask["is_weak"].to_list() == [False, False, True]


def test_regime_mask_fails_closed_on_missing_previous_state() -> None:
    module = _module()
    dates = pl.DataFrame(
        {"date": [date(2026, 1, 5), date(2026, 1, 6)]}
    )
    history = pl.DataFrame(
        {"date": [date(2026, 1, 6)], "state": ["weak"]}
    )

    with pytest.raises(ValueError, match="previous trading state"):
        module.regime_mask_t_minus_one(dates, history, require_previous=True)


def test_effective_regime_fingerprint_binds_actual_t_minus_one_states() -> None:
    module = _module()
    first = pl.DataFrame(
        {
            "date": [date(2026, 1, 5), date(2026, 1, 6)],
            "regime_date": [None, date(2026, 1, 5)],
            "state": [None, "weak"],
            "is_weak": [False, True],
        }
    )
    second = first.with_columns(
        pl.when(pl.col("date") == date(2026, 1, 6))
        .then(pl.lit("strong"))
        .otherwise(pl.col("state"))
        .alias("state")
    )

    first_fingerprint = module.effective_regime_fingerprint(first)
    second_fingerprint = module.effective_regime_fingerprint(second)

    assert first_fingerprint["row_count"] == 2
    assert first_fingerprint["weak_signal_date_count"] == 1
    assert first_fingerprint["sha256"] != second_fingerprint["sha256"]


def test_horizon_panel_adds_forward_returns_without_lookahead() -> None:
    module = _module()
    start = date(2026, 1, 1)
    panel = pl.DataFrame(
        {
            "symbol": ["A"] * 5,
            "date": [start + timedelta(days=index) for index in range(5)],
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
        }
    )

    out = module.build_horizon_panel(panel, horizons=(2, 3))

    assert out["forward_return_2d"][0] == pytest.approx(0.2)
    assert out["forward_return_2d"].n_unique() == 4
    assert out["forward_return_2d"].tail(2).null_count() == 2
    assert out["forward_return_3d"].tail(3).null_count() == 3


def test_conditional_factor_is_constant_outside_weak_state() -> None:
    module = _module()
    panel = pl.DataFrame(
        {
            "symbol": ["A", "B", "A", "B"],
            "date": [
                date(2026, 1, 5),
                date(2026, 1, 5),
                date(2026, 1, 6),
                date(2026, 1, 6),
            ],
            "pb_latest": [1.0, 3.0, 2.0, 4.0],
        }
    )
    mask = pl.DataFrame(
        {
            "date": [date(2026, 1, 5), date(2026, 1, 6)],
            "is_weak": [False, True],
        }
    )

    out = module.build_conditional_factor(
        panel, "pb_latest", mask, neutral_fill="median"
    )

    nonweak = out.filter(~pl.col("is_weak"))
    weak = out.filter(pl.col("is_weak"))
    assert nonweak["pb_latest_conditional"].n_unique() == 1
    assert weak["pb_latest_conditional"].to_list() == [2.0, 4.0]


def test_factor_horizon_gate_requires_two_neutralized_confirmations() -> None:
    module = _module()
    passing = {
        "industry": {"directional_ic": 0.02, "p_value": 0.04},
        "size": {"directional_ic": 0.03, "p_value": 0.03},
        "industry_size": {"directional_ic": 0.01, "p_value": 0.20},
    }
    failing = {
        "industry": {"directional_ic": 0.02, "p_value": 0.04},
        "size": {"directional_ic": -0.01, "p_value": 0.02},
        "industry_size": {"directional_ic": 0.00, "p_value": 0.80},
    }

    assert module.factor_horizon_gate(passing)["pass"] is True
    assert module.factor_horizon_gate(failing)["pass"] is False


def test_neutralized_ic_uses_full_horizon_for_newey_west_lag(monkeypatch) -> None:
    module = _module()
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(70)]
    symbols = [f"S{index:02d}" for index in range(36)]
    panel = (
        pl.DataFrame({"date": dates})
        .join(pl.DataFrame({"symbol": symbols}), how="cross")
        .with_columns(
            pl.col("symbol").str.slice(1).cast(pl.Int32).alias("symbol_id"),
            pl.col("date").rank("dense").alias("day_id"),
            pl.lit("industry_a").alias("industry_l1"),
        )
        .with_columns(
            (pl.col("symbol_id") * 0.1 + (pl.col("day_id") % 7) * 0.03).alias(
                "pb_latest"
            ),
            (pl.col("symbol_id") * 0.01 + 10.0).alias("log_float_mv"),
            (
                pl.col("symbol_id") * 0.002
                + (pl.col("day_id") % 5) * 0.001
                + ((pl.col("symbol_id") + pl.col("day_id")) % 3) * 0.0005
            ).alias("forward_return_60d"),
        )
    )
    captured_lags: list[int] = []

    def fake_newey_west(values, lag: int):
        captured_lags.append(lag)
        return (1.0, 0.0, 1.0)

    monkeypatch.setattr(module.stats_v2, "newey_west_t", fake_newey_west)

    module.neutralized_ic_grid(
        panel,
        factor_ids=("pb_latest",),
        horizons=(60,),
        variants=("raw",),
    )

    assert captured_lags == [60]


def test_weight_neighbourhood_is_sorted_and_contains_center() -> None:
    module = _module()

    weights = module.weight_neighbourhood(0.125, steps=(0.05, 0.10, 0.15, 0.20))

    assert weights == [0.05, 0.10, 0.125, 0.15, 0.20]


def test_published_p9_result_keeps_active_version_null() -> None:
    payload = json.loads(P9_RESULT.read_text(encoding="utf-8"))

    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["paper_target_generated"] is False
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert payload["candidate_count"] >= 1


def test_conditional_composite_respects_both_low_directions() -> None:
    module = _module()
    day = date(2026, 1, 5)
    panel = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [day] * 4,
            "pb_latest": [1.0, 2.0, 3.0, 4.0],
            "accrual": [1.0, 2.0, 3.0, 4.0],
        }
    )
    mask = pl.DataFrame({"date": [day], "is_weak": [True]})

    out = module.build_conditional_composite(
        panel,
        ("pb_latest", "accrual"),
        mask,
        output_column="financial_conditional_composite",
    )
    scores = dict(
        zip(
            out["symbol"].to_list(),
            out["financial_conditional_composite"].to_list(),
            strict=True,
        )
    )

    assert scores["A"] > scores["B"] > scores["C"] > scores["D"]


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_published_p9_result_is_canonical_and_pit_bound() -> None:
    payload = json.loads(P9_RESULT.read_text(encoding="utf-8"))
    p8 = json.loads(
        (DATA / "factor_research" / "research" / "p8_financial_increment_2026-11-12.json").read_text(
            encoding="utf-8"
        )
    )
    core = {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }

    assert sha256_json(core) == payload["research_id"]
    assert payload["p8_research_id"] == p8["research_id"]
    assert payload["base_set"]["pre_registered_drop_factor"] == (
        "ext_alt_hs300_holder_top10_share_chg_yoy"
    )
    assert payload["regime"]["computed_days"] >= 0
    assert payload["regime"]["effective_state"]["sha256"]
    assert payload["data_fingerprint"]
    assert payload["decision"]["status"] in {
        "no_horizon_candidate",
        "no_statistical_candidate",
        "no_portfolio_candidate",
        "no_new_watch_version",
        "watch_candidate_requires_registration",
    }


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_p9_rerun_is_idempotent() -> None:
    paths = [
        P9_RESULT,
        P9_RESULT.with_suffix(".md"),
        DATA / "factor_research" / "ledger" / "head.json",
        DATA / "factor_research" / "ledger" / "records.jsonl",
    ]
    before = _hash(paths)

    subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT / "backend",
        check=True,
        capture_output=True,
        text=True,
    )

    assert _hash(paths) == before
