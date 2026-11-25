from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from app.backtest.matrix import build_market_matrix_from_signals, make_signal_matrix
from app.backtest.synthetic_gbm import (
    ASSET_COUNT,
    CORRELATIONS,
    COST_MULTIPLIERS,
    DEFAULT_MASTER_SEED,
    EVALUATION_DAYS,
    INDUSTRY_CAP,
    INDUSTRY_COUNT,
    MAX_HOLD_DAYS,
    MAX_POSITIONS,
    SCENARIOS,
    SIGNAL_MODES,
    STOP_VARIANTS,
    WARMUP_DAYS,
    SyntheticPathSpec,
    aggregate_observations,
    build_project_market,
    build_strategy_market,
    build_strategy_signals,
    compute_signal_scores,
    generate_synthetic_market,
    matcher_config,
    run_synthetic_path,
)


def _small_spec(*, beta: float = 0.0, path_id: int = 0) -> SyntheticPathSpec:
    return SyntheticPathSpec(
        scenario_id="neutral",
        beta=beta,
        path_id=path_id,
        master_seed=DEFAULT_MASTER_SEED,
    )


def _small_market(**kwargs):
    return generate_synthetic_market(
        _small_spec(**kwargs),
        asset_count=24,
        warmup_days=20,
        evaluation_days=80,
        start=date(2024, 1, 2),
    )


def test_generation_is_seeded_and_paths_diverge() -> None:
    first = _small_market()
    replay = _small_market()
    other = _small_market(path_id=1)

    for name in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        np.testing.assert_array_equal(getattr(first, name), getattr(replay, name))
    for mode in SIGNAL_MODES:
        np.testing.assert_array_equal(first.scores[mode], replay.scores[mode])
    assert not np.array_equal(first.close, other.close)


def test_generation_preserves_ohlcv_and_industry_invariants() -> None:
    market = _small_market(beta=0.7)

    assert market.close.shape == (80, 24)
    assert len(market.dates) == 80
    assert len(market.symbols) == 24
    assert len(set(market.industries.values())) == 3
    assert np.isfinite(market.close).all()
    assert (market.close > 0).all()
    assert (market.open > 0).all()
    assert (market.volume > 0).all()
    assert (market.amount > 0).all()
    assert (market.turnover_rate > 0).all()
    assert np.all(market.low <= np.minimum(market.open, market.close))
    assert np.all(market.high >= np.maximum(market.open, market.close))


def test_signal_scores_are_bounded_and_ignore_future_prices() -> None:
    rng = np.random.default_rng(12345)
    full_close = 20.0 * np.exp(
        np.cumsum(rng.normal(0.0002, 0.01, size=(140, 24)), axis=0)
    )
    cutoff = 50
    changed = np.array(full_close, copy=True)
    changed[cutoff + 1 :] *= np.linspace(
        0.5, 1.5, changed.shape[0] - cutoff - 1
    )[:, None]
    before = compute_signal_scores(
        full_close, warmup_days=20, random_seed=2026
    )
    after = compute_signal_scores(
        changed, warmup_days=20, random_seed=2026
    )
    market = _small_market()

    for mode in SIGNAL_MODES:
        assert (before[mode][:20] == 0).all()
        np.testing.assert_array_equal(
            before[mode][: cutoff + 1],
            after[mode][: cutoff + 1],
        )
        assert np.isfinite(market.scores[mode]).all()
        assert (market.scores[mode] >= 0).all()
        assert (market.scores[mode] <= 100).all()


def test_strategy_signal_t_is_filled_at_next_open_and_never_same_bar() -> None:
    market = _small_market()
    base = build_project_market(market)
    strategy = build_strategy_market(market, "momentum_20", base_market=base)
    source_entry = build_strategy_signals(
        market, "momentum_20", base_market=base
    ).entry

    for time_id in range(21, len(market.dates) - 1):
        active = np.flatnonzero(source_entry[time_id])
        if len(active):
            np.testing.assert_array_equal(
                strategy.entry[time_id + 1],
                source_entry[time_id].astype(np.uint8),
            )
            if time_id > 0:
                np.testing.assert_array_equal(
                    strategy.entry[time_id],
                    source_entry[time_id - 1].astype(np.uint8),
                )
            return
    raise AssertionError("test fixture produced no momentum signal")


def test_manual_signal_uses_project_t_plus_one_delay() -> None:
    market = _small_market()
    base = build_project_market(market)
    entry = np.zeros(base.shape, dtype=np.uint8)
    exit_ = np.zeros(base.shape, dtype=np.uint8)
    entry[3, 0] = 1
    signals = make_signal_matrix(
        base.shape,
        entry=entry,
        exit=exit_,
        score=np.full(base.shape, 50.0, dtype=np.float32),
        entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
        exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
        entry_signal_ids=("test_entry",),
        exit_signal_ids=("test_exit",),
    )
    matrix = build_market_matrix_from_signals(
        base,
        signals,
        entry_delay_bars=1,
        exit_delay_bars=1,
    )

    assert matrix.entry[3, 0] == 0
    assert matrix.entry[4, 0] == 1
    assert matrix.entry_signal_time[4, 0] == 3


def test_project_execution_skeleton_has_all_cost_cases_and_no_leverage() -> None:
    configs = {
        multiplier: matcher_config(multiplier, stop_loss_pct=-0.12)
        for multiplier in COST_MULTIPLIERS
    }

    assert configs[2].commission_pct == pytest.approx(0.0004)
    assert configs[3].commission_pct == pytest.approx(0.0006)
    assert configs[5].commission_pct == pytest.approx(0.0010)
    assert configs[2].slippage_bps == pytest.approx(10)
    assert configs[3].slippage_bps == pytest.approx(15)
    assert configs[5].slippage_bps == pytest.approx(25)
    for config in configs.values():
        assert config.matching == "open_t+1"
        assert config.max_exposure_pct <= 1.0
        assert config.max_exposure_pct > 0
        assert config.position_sizing == "equal"
        assert config.stop_loss_pct == pytest.approx(-0.12)


def test_full_axis_constants_match_preregistered_matrix() -> None:
    assert tuple(SCENARIOS) == ("neutral", "bull", "bear", "high_volatility")
    assert CORRELATIONS == (0.0, 0.7)
    assert len(SIGNAL_MODES) == 6
    assert STOP_VARIANTS == (("no_stop", None), ("stop_12", -0.12))
    assert COST_MULTIPLIERS == (2, 3, 5)
    assert (ASSET_COUNT, INDUSTRY_COUNT, INDUSTRY_CAP) == (300, 30, 3)
    assert (MAX_POSITIONS, MAX_HOLD_DAYS) == (10, 60)
    assert (WARMUP_DAYS, EVALUATION_DAYS) == (60, 504)


def test_one_path_runs_every_mode_stop_and_cost_with_benchmark() -> None:
    payload = run_synthetic_path(
        _small_spec(),
        asset_count=24,
        warmup_days=20,
        evaluation_days=100,
        max_positions=5,
        max_hold_days=20,
        top_rank=12,
        industry_cap=2,
    )

    assert len(payload["observations"]) == len(SIGNAL_MODES) * len(STOP_VARIANTS) * len(COST_MULTIPLIERS)
    assert len(payload["benchmarks"]) == len(COST_MULTIPLIERS)
    strategy_keys = {
        (
            row["signal_mode"],
            row["stop_variant"],
            row["cost_multiplier"],
        )
        for row in payload["observations"]
    }
    assert len(strategy_keys) == 36
    assert all(row["max_exposure"] > 0.99 for row in payload["benchmarks"])
    by_variant = {
        (
            row["signal_mode"],
            row["stop_variant"],
            row["cost_multiplier"],
        ): row
        for row in payload["observations"]
    }
    for mode in SIGNAL_MODES:
        for stop_variant, _stop in STOP_VARIANTS:
            returns = [
                by_variant[(mode, stop_variant, cost)]["strategy_total_return"]
                for cost in COST_MULTIPLIERS
            ]
            assert returns[0] >= returns[1] >= returns[2]
    for row in payload["observations"]:
        assert math.isfinite(row["strategy_total_return"])
        assert math.isfinite(row["benchmark_total_return"])
        assert row["excess_return"] == pytest.approx(
            row["strategy_total_return"] - row["benchmark_total_return"]
        )
        assert row["strategy_n_trades"] >= 0
        assert row["strategy_turnover"] >= 0


def test_aggregate_requires_every_cell_and_every_path() -> None:
    rows = []
    for scenario in SCENARIOS:
        for beta in CORRELATIONS:
            for mode in SIGNAL_MODES:
                for stop_variant, stop in STOP_VARIANTS:
                    for cost in COST_MULTIPLIERS:
                        for path_id in range(2):
                            benchmark = 0.001 * path_id + 0.01 * cost
                            strategy = benchmark + 0.002 * (path_id + 1)
                            rows.append({
                                "scenario_id": scenario,
                                "beta": beta,
                                "signal_mode": mode,
                                "stop_variant": stop_variant,
                                "stop_loss_pct": stop,
                                "cost_multiplier": cost,
                                "path_id": path_id,
                                "strategy_total_return": strategy,
                                "strategy_annual_return": strategy * 1.2,
                                "strategy_sharpe": 0.5,
                                "strategy_max_drawdown": -0.1,
                                "strategy_turnover": 3.0,
                                "strategy_n_trades": 12,
                                "benchmark_total_return": benchmark,
                                "benchmark_annual_return": benchmark * 1.2,
                                "benchmark_sharpe": 0.3,
                                "benchmark_max_drawdown": -0.2,
                                "benchmark_turnover": 0.05,
                                "benchmark_n_trades": 300,
                                "excess_return": strategy - benchmark,
                            })

    summary = aggregate_observations(rows, expected_paths=2)
    assert summary["expected_cell_count"] == 288
    assert summary["observed_cell_count"] == 288
    assert summary["min_paths_per_cell"] == 2
    assert len(summary["cells"]) == 288
    assert isinstance(summary["summary_by_cost"], list)
    assert len(summary["summary_by_cost"]) == 3
    assert isinstance(summary["summary_by_beta"], list)
    assert len(summary["summary_by_beta"]) == 2
    assert isinstance(summary["summary_by_scenario"], list)
    assert len(summary["summary_by_scenario"]) == 4

    with pytest.raises(ValueError, match="path count"):
        aggregate_observations(rows[:-1], expected_paths=2)


def test_synthetic_market_spec_rejects_bad_beta_and_unknown_scenario() -> None:
    with pytest.raises(ValueError, match="beta"):
        SyntheticPathSpec("neutral", 1.2, 0, DEFAULT_MASTER_SEED)
    with pytest.raises(ValueError, match="scenario"):
        SyntheticPathSpec("unknown", 0.0, 0, DEFAULT_MASTER_SEED)
    with pytest.raises(ValueError, match="path_id"):
        SyntheticPathSpec("neutral", 0.0, -1, DEFAULT_MASTER_SEED)


def test_realized_volatility_matches_neutral_gbm_scenario() -> None:
    market = generate_synthetic_market(_small_spec())
    log_returns = np.diff(np.log(market.close), axis=0)
    realized = np.std(log_returns, axis=0, ddof=1) * math.sqrt(252)
    assert 0.18 <= float(np.median(realized)) <= 0.22


def _gbm_runner():
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    path = root / "scripts/research_gbm_market_stress.py"
    spec = importlib.util.spec_from_file_location("research_gbm_market_stress_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_contract_and_design_cover_all_axes() -> None:
    runner = _gbm_runner()
    assert runner.RESEARCH_TYPE == "synthetic_market_stress"
    assert runner.RESEARCH_PREREGISTRATION.endswith(
        "r_gbm_001_preregistration.json"
    )
    design = runner.scientific_design()
    assert design["path_count"] == 100
    assert design["regime_count"] == 4
    assert design["correlation_count"] == 2
    assert design["signal_mode_count"] == 6
    assert design["stop_variant_count"] == 2
    assert design["cost_multiplier_count"] == 3
    assert design["expected_cells"] == 288
    assert design["expected_strategy_observations"] == 28_800
    assert design["expected_benchmark_observations"] == 2_400
    assert design["execution"]["leverage"] == "none"
    assert design["execution"]["max_exposure_pct"] == 1.0
    assert design["execution"]["five_x_policy"] == "warning_only"


def test_full_diagnostic_payload_and_markdown_preserve_all_cells() -> None:
    runner = _gbm_runner()
    observations = []
    benchmarks = []
    realized = []
    for scenario in SCENARIOS:
        for beta in CORRELATIONS:
            for path_id in range(100):
                realized.append({
                    "scenario_id": scenario,
                    "beta": beta,
                    "path_id": path_id,
                    "equal_weight_annual_return": 0.01,
                    "equal_weight_annual_volatility": 0.2,
                })
                for cost in COST_MULTIPLIERS:
                    benchmark = 0.001 * path_id - 0.01 * cost
                    benchmarks.append({
                        "scenario_id": scenario,
                        "beta": beta,
                        "path_id": path_id,
                        "cost_multiplier": cost,
                        "total_return": benchmark,
                        "annual_return": benchmark,
                        "sharpe": 0.2,
                        "max_drawdown": -0.1,
                        "avg_exposure": 0.99,
                        "max_exposure": 0.995,
                        "n_trades": 300,
                        "turnover": 0.5,
                    })
                for mode in SIGNAL_MODES:
                    for stop_variant, stop in STOP_VARIANTS:
                        for cost in COST_MULTIPLIERS:
                            benchmark = 0.001 * path_id - 0.01 * cost
                            strategy = benchmark + 0.002
                            observations.append({
                                "scenario_id": scenario,
                                "beta": beta,
                                "path_id": path_id,
                                "signal_mode": mode,
                                "stop_variant": stop_variant,
                                "stop_loss_pct": stop,
                                "cost_multiplier": cost,
                                "strategy_total_return": strategy,
                                "strategy_annual_return": strategy,
                                "strategy_sharpe": 0.6,
                                "strategy_max_drawdown": -0.12,
                                "strategy_turnover": 2.0,
                                "strategy_n_trades": 20,
                                "strategy_max_exposure": 0.99,
                                "benchmark_total_return": benchmark,
                                "benchmark_annual_return": benchmark,
                                "benchmark_sharpe": 0.2,
                                "benchmark_max_drawdown": -0.1,
                                "benchmark_turnover": 0.5,
                                "benchmark_n_trades": 300,
                                "excess_return": strategy - benchmark,
                            })
    payload = runner.build_result_payload(
        {
            "observations": observations,
            "benchmarks": benchmarks,
            "realized": realized,
            "workers": 1,
            "seconds": 1.0,
        },
        path_count=100,
        replay_ok=True,
    )
    assert payload["conclusion"] == "diagnostic_only"
    assert payload["gate_results"]["complete"] is True
    assert payload["aggregate"]["observed_cell_count"] == 288
    assert payload["aggregate"]["observed_observations"] == 28_800
    assert payload["benchmark_controls"]["observations"] == 2_400
    pooled = payload["aggregate"]["summary_by_signal"][0]["excess_return"]
    assert math.isfinite(pooled["sign_test_p"])
    assert math.isfinite(pooled["sign_test_log10_p"])
    markdown = runner.render_markdown(payload)
    assert "diagnostic_only" in markdown
    assert "全部 288 个诊断 cell" in markdown
    assert markdown.count("| 2× |") + markdown.count("| 3× |") + markdown.count("| 5× |") >= 288
