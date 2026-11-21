from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r387_forward_shadow_confirmation.py"
PREREG = EVIDENCE / "r387_preregistration.json"
REPORT = EVIDENCE / "r387_forward_shadow_confirmation.json"
RESULT = EVIDENCE / "r387_forward_shadow_result.md"
SYMBOLS = tuple(f"{index:06d}.SZ" for index in range(300))


def _module():
    spec = importlib.util.spec_from_file_location("r387_forward_shadow_confirmation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _membership(module) -> pl.DataFrame:
    return pl.DataFrame([
        {
            "query_date": day,
            "update_date": day,
            "symbol": symbol,
            "name": symbol,
        }
        for day in module.NEW_SESSIONS
        for symbol in SYMBOLS
    ]).with_columns(pl.col("query_date", "update_date").cast(pl.Date))


def _extension(module) -> pl.DataFrame:
    fields = (*module.MACRO_FIELDS, *module.INDUSTRY_FIELDS)
    old = pl.DataFrame({
        "date": [module.ANCHOR],
        "symbol": [SYMBOLS[0]],
        **{field: [0.1] for field in fields},
        "holder_top10_free_float_share": [0.1],
    }).with_columns(pl.col("date").cast(pl.Date))
    new = pl.DataFrame([
        {
            "date": day,
            "symbol": symbol,
            **{field: 0.2 for field in fields},
            "holder_top10_free_float_share": 0.2,
        }
        for day in module.NEW_SESSIONS
        for symbol in SYMBOLS
    ]).with_columns(pl.col("date").cast(pl.Date))
    return pl.concat([old, new], how="diagonal_relaxed")


def _write_enriched(
    root: Path,
    module,
    *,
    duplicate: bool = False,
    missing_symbol: bool = False,
    future_partition: bool = False,
) -> None:
    for day in module.NEW_SESSIONS:
        symbols = list(SYMBOLS[1:] if missing_symbol else SYMBOLS)
        frame = pl.DataFrame({
            "date": [day] * len(symbols),
            "symbol": symbols,
            "raw_close": [10.0] * len(symbols),
            "close": [10.0] * len(symbols),
        })
        if duplicate:
            frame = pl.concat([frame, frame.head(1)], how="vertical")
        partition = root / "kline_daily_enriched" / f"date={day.isoformat()}"
        partition.mkdir(parents=True)
        frame.write_parquet(partition / "part.parquet")
    if future_partition:
        day = module.END + timedelta(days=1)
        partition = root / "kline_daily_enriched" / f"date={day.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame({
            "date": [day],
            "symbol": [SYMBOLS[0]],
            "raw_close": [10.0],
            "close": [10.0],
        }).write_parquet(partition / "part.parquet")


def test_compose_costs_unwraps_strategy_outputs_before_cohort_combiner(monkeypatch) -> None:
    module = _module()
    dates = [module.START, module.ANCHOR, *module.NEW_SESSIONS]
    values = [1.0 + index * 0.001 for index in range(len(dates))]

    def curve(offset: float = 0.0) -> pl.DataFrame:
        return pl.DataFrame({"date": dates, "value": [value + offset for value in values]})

    def output(offset: float = 0.0):
        return curve(offset), {
            "window_trades": {
                "entry_count": 0,
                "exit_count": 0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            },
            "full_trade_count": 0,
        }

    outputs = {}
    for cost in module.COSTS:
        outputs[f"A_{cost}"] = output()
        outputs[f"B_{cost}_{module.B_CELL}"] = output(0.01)
        for index in range(3):
            outputs[f"C_{cost}_{index}"] = output(0.02 + index * 0.001)

    class FakeRuntime:
        END = module.END
        R381_MEMBERSHIP = None
        R381_EXT_FRAME = None
        SCENARIOS: ClassVar = {cost: {"defensive_turnover": 0.0} for cost in module.COSTS}

        @staticmethod
        def _context():
            return {}

        @staticmethod
        def prepare_assets(_ctx):
            return {}

        @staticmethod
        def combine_c(curves, cost, _ctx):
            cohort_curves = [curves[f"C_{cost}_{index}"] for index in range(3)]
            assert all(isinstance(item, pl.DataFrame) for item in cohort_curves)
            return cohort_curves[0]

        @staticmethod
        def compose_portfolio(_ctx, _assets, _a, _b, c, _cost, _parameters):
            active = {
                "date": module.ANCHOR,
                "target_equity_weight": 0.37,
                "target_gold_weight": 0.23,
                "target_treasury_weight": 0.20,
                "target_dividend_weight": 0.20,
                "target_cash_weight": 0.0,
            }
            return {
                "curve": c,
                "sleeve_weights": [{
                    "date": module.ANCHOR,
                    "weight_a": 0.40,
                    "weight_b": 0.30,
                    "weight_c": 0.30,
                }],
                "rebalances": [active],
                "weight_summary": {"max_weight_sum": 1.0},
            }

    monkeypatch.setattr(module, "module", lambda *_args, **_kwargs: FakeRuntime)
    verified = {
        "benchmark": pl.DataFrame({"date": dates, "close": [100.0 + index for index in range(len(dates))]}),
    }

    composed = module.compose_costs(verified, {"outputs": outputs})

    assert set(composed["costs"]) == set(module.COSTS)


def test_benchmark_rejects_duplicate_dates_even_when_row_count_matches(tmp_path, monkeypatch) -> None:
    module = _module()
    expected_dates = [module.ANCHOR, *module.NEW_SESSIONS]
    duplicated_dates = [module.ANCHOR, module.ANCHOR, *module.NEW_SESSIONS[1:]]

    frame = pl.DataFrame({
        "date": duplicated_dates,
        "symbol": [module.BENCHMARK] * len(duplicated_dates),
        "close": [float(close) for close in range(100, 112)],
    })
    for day_frame in frame.partition_by("date", maintain_order=True):
        day = day_frame["date"][0]
        partition = tmp_path / "kline_index_enriched" / f"date={day.isoformat()}"
        partition.mkdir(parents=True)
        day_frame.write_parquet(partition / "part.parquet")

    monkeypatch.setattr(module, "DATA", tmp_path)

    with pytest.raises(RuntimeError, match="benchmark"):
        module.benchmark_frame()

    assert len(set(duplicated_dates)) == len(expected_dates) - 1


def test_preregistration_freezes_parameters_and_keeps_five_x_warning_only() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    expected = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert expected == sidecar
    assert payload["round"] == "R387"
    assert payload["freeze"]["parameter_search_allowed"] is False
    assert payload["freeze"]["post_result_parameter_change_allowed"] is False
    assert payload["freeze"]["regime_switch_allowed"] is False
    assert payload["freeze"]["parameters"] == {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 110,
        "overlay_target_vol": 0.17,
        "overlay_equity_weight_max": 0.37,
    }
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["evaluation_protocol"]["performance_hard_gate_on_eleven_days"] is False
    assert payload["evaluation_protocol"]["blind_test_claim_allowed"] is False


def test_membership_rejects_future_update_dates_and_duplicate_rows() -> None:
    module = _module()
    membership = _membership(module)

    audit = module.validate_membership(membership, module.NEW_SESSIONS)
    assert audit["rows"] == 3300
    assert audit["symbols_per_session"] == [300]

    future = membership.with_columns(
        pl.when(pl.col("query_date") == module.NEW_SESSIONS[0])
        .then(pl.col("query_date") + pl.duration(days=1))
        .otherwise(pl.col("update_date"))
        .alias("update_date")
    )
    with pytest.raises(RuntimeError, match="future update dates"):
        module.validate_membership(future, module.NEW_SESSIONS)

    duplicate = pl.concat([membership, membership.head(1)], how="vertical")
    with pytest.raises(RuntimeError, match="duplicate"):
        module.validate_membership(duplicate, module.NEW_SESSIONS)


def test_extension_rejects_modified_prefix_and_new_macro_industry_nulls() -> None:
    module = _module()
    membership = _membership(module)
    extension = _extension(module)

    audit = module.validate_extension(extension, membership, module.NEW_SESSIONS, extension.filter(pl.col("date") <= module.ANCHOR))
    assert audit["prefix_matches_r381"] is True
    assert audit["macro_nulls"] == {field: 0 for field in module.MACRO_FIELDS}
    assert audit["industry_nulls"] == {field: 0 for field in module.INDUSTRY_FIELDS}

    changed = extension.with_columns(
        pl.when(pl.col("date") == module.ANCHOR)
        .then(pl.lit(0.9))
        .otherwise(pl.col(module.MACRO_FIELDS[0]))
        .alias(module.MACRO_FIELDS[0])
    )
    with pytest.raises(RuntimeError, match="frozen R381 history"):
        module.validate_extension(
            changed,
            membership,
            module.NEW_SESSIONS,
            extension.filter(pl.col("date") <= module.ANCHOR),
        )

    nulled = extension.with_columns(
        pl.when((pl.col("date") == module.NEW_SESSIONS[0]) & (pl.col("symbol") == SYMBOLS[0]))
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col(module.INDUSTRY_FIELDS[0]))
        .alias(module.INDUSTRY_FIELDS[0])
    )
    with pytest.raises(RuntimeError, match="null macro/industry"):
        module.validate_extension(
            nulled,
            membership,
            module.NEW_SESSIONS,
            extension.filter(pl.col("date") <= module.ANCHOR),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"duplicate": True}, "duplicate"),
        ({"missing_symbol": True}, "300 rows"),
        ({"future_partition": True}, "future enriched"),
    ],
)
def test_enriched_rejects_duplicate_missing_symbol_and_future_partition(
    tmp_path, monkeypatch, mutation, message
) -> None:
    module = _module()
    _write_enriched(tmp_path, module, **mutation)
    monkeypatch.setattr(module, "DATA", tmp_path)

    with pytest.raises(RuntimeError, match=message):
        module.validate_enriched(module.NEW_SESSIONS, _membership(module))


def test_defensive_asset_validation_rejects_stale_symbol_tail(tmp_path, monkeypatch) -> None:
    module = _module()
    for kind, symbols in module.DEFENSIVE_SYMBOLS.items():
        rows = []
        for symbol in symbols:
            for day in module.NEW_SESSIONS:
                if kind == "gold" and symbol == symbols[0] and day == module.END:
                    continue
                rows.append({"date": day, "symbol": symbol, "close": 10.0})
        frame = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))
        frame.write_parquet(tmp_path / f"{kind}_etf_qfq.parquet")

    monkeypatch.setattr(module, "ASSET_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="stale"):
        module.validate_assets()


def test_exposure_summary_rejects_total_weight_above_one() -> None:
    module = _module()
    bundle = {
        "rebalances": [{
            "date": module.ANCHOR,
            "target_equity_weight": 0.38,
            "target_gold_weight": 0.21,
            "target_treasury_weight": 0.21,
            "target_dividend_weight": 0.21,
            "target_cash_weight": 0.0,
        }],
        "sleeve_weights": [{
            "date": module.ANCHOR,
            "weight_a": 0.50,
            "weight_b": 0.37,
            "weight_c": 0.13,
        }],
        "weight_summary": {"max_weight_sum": 1.01},
    }

    with pytest.raises(RuntimeError, match=r"exceed 1\.0"):
        module.exposure_summary(bundle, bundle["sleeve_weights"])


def test_r387_refuses_to_overwrite_existing_evidence(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r387.json"
    result = tmp_path / "r387.md"
    monkeypatch.setattr(module, "OUTPUT", output)
    monkeypatch.setattr(module, "RESULT", result)

    module.ensure_outputs_absent()
    output.write_text("occupied", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        module.ensure_outputs_absent()


def test_committed_evidence_matches_final_runner_and_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")

    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar

    assert report["round"] == "R387"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["all_pass"] is True
    assert report["no_leverage"] is True
    assert report["cost_protocol"]["five_x_hard_rejection"] is False
    assert report["summary_2x"]["max_weight_sum"] <= 1.0
    assert report["decision"] == {
        "status": "shadow_observation_complete_no_decision",
        "upgrade_supported": False,
        "blind_test_claim_supported": False,
        "strategy_decision_supported": False,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
        "reason": "Eleven trading days are a data-freshness and behavior observation window, not a statistical selection sample.",
    }
    assert "11 天收益不设硬门槛" in markdown
    assert "没有订单、纸面目标或生产配置" in markdown
