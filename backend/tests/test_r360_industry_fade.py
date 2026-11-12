from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.strategy import StrategyDependencyResolver
from app.factors.ext_factors import ensure_synced
from app.factors.registry import get_factor, register_factor, unregister_factor
from app.factors.store import to_spec
from app.indicators import pipeline
from app.strategy import custom_signals
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
FACTOR_PATH = ROOT / "data/user_data/custom_factors/uf_industry_relative_strength_change_5d.json"
SIGNAL_PATH = (
    ROOT / "data/user_data/custom_signals/r360_rel_strength_fade.json"
)
STRATEGY_PATH = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
    "r360_strategies/ai_hs300_industry_fade_quality_h20_sl20.py"
)
FACTOR_ID = "uf_industry_relative_strength_change_5d"
REL_FACTOR = "ext_alt_hs300_stock_rel_industry_20d"
SIGNAL_COLUMN = "csg_r360_rel_strength_fade"


def _signal() -> dict:
    return json.loads(SIGNAL_PATH.read_text(encoding="utf-8"))


def _register_factor() -> bool:
    ensure_synced(ROOT / "data")
    spec = to_spec(json.loads(FACTOR_PATH.read_text(encoding="utf-8")))
    if get_factor(FACTOR_ID) is None:
        register_factor(spec)
        return True
    assert get_factor(FACTOR_ID) == spec
    return False


def test_relative_strength_fade_signal_uses_only_current_and_five_prior_sessions() -> None:
    registered_here = _register_factor()
    try:
        signal = _signal()
        custom_signals.validate(signal)
        expressions = custom_signals.build_expressions([signal])
        values = {
            "A": [0.10, 0.08, 0.09, 0.04, 0.00, -0.02, -0.01, 0.01],
            "B": [-0.10, -0.08, -0.06, -0.04, -0.02, -0.01, 0.00, 0.02],
            "C": [0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03],
            "D": [0.10, 0.09, 0.07, 0.05, 0.02, -0.01, 0.02, 0.03],
        }
        start = date(2026, 1, 5)
        panel = pl.DataFrame([
            {"symbol": symbol, "date": start + timedelta(days=index), REL_FACTOR: value}
            for symbol, series in values.items()
            for index, value in enumerate(series)
        ]).sort(["symbol", "date"])

        evaluated = custom_signals.materialize_factor_columns(panel, expressions)
        evaluated = custom_signals.inject(evaluated, expressions)
        actual = {
            part["symbol"][0]: part[SIGNAL_COLUMN].to_list()
            for part in evaluated.partition_by("symbol", maintain_order=True)
        }

        assert actual["A"] == [False, False, False, False, False, True, True, False]
        assert actual["B"] == [None, None, None, None, None, False, False, False]
        assert actual["C"] == [False] * 8
        assert actual["D"] == [False, False, False, False, False, True, False, False]
    finally:
        if registered_here:
            unregister_factor(FACTOR_ID)


def test_strategy_resolves_factor_exit_signal_and_industry_history(monkeypatch) -> None:
    registered_here = _register_factor()
    original_load = custom_signals.load_all
    signal = _signal()
    try:
        monkeypatch.setattr(custom_signals, "load_all", lambda data_dir: [*original_load(data_dir), signal])
        monkeypatch.setattr(pipeline, "_custom_signal_exprs", None)
        monkeypatch.setattr(pipeline, "_custom_signal_exprs_today", None)
        monkeypatch.setattr(pipeline, "_custom_prev_fields_cache", None)
        engine = StrategyEngine(strategy_dirs=[STRATEGY_PATH.parent])
        strategy = engine.get("ai_hs300_industry_fade_quality_h20_sl20")
        baseline_engine = StrategyEngine(
            strategy_dirs=[ROOT / "data/strategies/ai"],
        )
        baseline = baseline_engine.get("ai_hs300_industry_relative_strength_quality_h20_sl20")
        plan = StrategyDependencyResolver().resolve(
            strategy,
            params={},
            basic_filter=strategy.basic_filter,
            entry_signals=strategy.entry_signals,
            exit_signals=strategy.exit_signals,
            asset_type="stock",
        )

        assert strategy.max_hold_days == 20
        assert strategy.stop_loss == -0.20
        assert strategy.meta["scoring"] == baseline.meta["scoring"]
        assert strategy.basic_filter == baseline.basic_filter
        assert strategy.max_hold_days == baseline.max_hold_days
        assert strategy.stop_loss == baseline.stop_loss
        assert set(strategy.exit_signals) == {SIGNAL_COLUMN}
        assert FACTOR_ID in strategy.required_features
        assert SIGNAL_COLUMN in plan.signal_columns
        assert REL_FACTOR in plan.ext_columns
    finally:
        if registered_here:
            unregister_factor(FACTOR_ID)


def test_signal_exit_is_filled_at_next_session_open() -> None:
    start = date(2026, 1, 5)
    panel = pl.DataFrame({
        "symbol": ["A"] * 4,
        "date": [start + timedelta(days=index) for index in range(4)],
        "open": [9.0, 10.0, 12.0, 14.0],
        "high": [9.0, 10.0, 12.0, 14.0],
        "low": [9.0, 10.0, 12.0, 14.0],
        "close": [9.0, 10.0, 12.0, 14.0],
        "signal_entry": [True, False, False, False],
        SIGNAL_COLUMN: [False, False, True, False],
        "name": ["A"] * 4,
    })

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        panel["signal_entry"],
        panel[SIGNAL_COLUMN],
        MatcherConfig(matching="open_t+1", fees_pct=0, slippage_bps=0, max_positions=1),
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "signal"
    assert result.trades[0].exit_date == str(start + timedelta(days=3))
    assert result.trades[0].exit_price == 14.0


def test_preflight_and_backtest_share_frozen_r360_inputs() -> None:
    import importlib.util

    evidence = STRATEGY_PATH.parent.parent
    preflight_path = evidence / "run_r360_preflight.py"
    backtest_path = evidence / "run_r360_industry_fade.py"

    def load(path: Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    preflight = load(preflight_path, "r360_preflight_contract_test")
    backtest = load(backtest_path, "r360_backtest_contract_test")

    assert preflight.PRE_REG == backtest.PRE_REG
    assert preflight.PRE_REG.name == "r360_v2_preregistration.json"
    assert preflight.OUTPUT == backtest.PREFLIGHT
    assert preflight.input_fingerprints() == backtest.input_fingerprints()

    prereg = json.loads(preflight.PRE_REG.read_text(encoding="utf-8"))
    frozen = prereg["frozen_inputs"]
    assert {
        "baseline_result_sha256",
        "data_fingerprints",
        "code_sha256",
        "runner_sha256",
        "preflight_runner_sha256",
        "test_sha256",
        "candidate_strategy_sha256",
        "factor_definition_sha256",
        "exit_signal_sha256",
        "isolation_helper_sha256",
    } <= frozen.keys()


def test_preflight_tree_hash_accepts_isolated_paths_outside_repository(tmp_path: Path) -> None:
    import importlib.util

    evidence = STRATEGY_PATH.parent.parent
    path = evidence / "run_r360_preflight.py"
    spec = importlib.util.spec_from_file_location("r360_preflight_tree_hash_test", path)
    assert spec is not None and spec.loader is not None
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)

    isolated = tmp_path / "snapshot"
    partition = isolated / "date=2026-01-02"
    partition.mkdir(parents=True)
    (partition / "part.parquet").write_bytes(b"fixture")

    fingerprint = preflight.tree_hash(isolated)

    assert fingerprint["files"] == 1
    assert fingerprint["bytes"] == len(b"fixture")
    assert fingerprint == preflight.tree_hash(isolated)


def test_backtest_preflight_binding_uses_runner_hashes_emitted_by_preflight() -> None:
    import importlib.util

    import pytest

    evidence = STRATEGY_PATH.parent.parent
    path = evidence / "run_r360_industry_fade.py"
    spec = importlib.util.spec_from_file_location("r360_backtest_binding_test", path)
    assert spec is not None and spec.loader is not None
    backtest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backtest)

    prereg = {
        "frozen_inputs": {
            "runner_sha256": "backtest-runner",
            "preflight_runner_sha256": "preflight-runner",
            "test_sha256": "test",
            "isolation_helper_sha256": "isolation",
        }
    }
    manifest = {
        "all_pass": True,
        "preregistration_sha256": "prereg",
        "backtest_runner_sha256": "backtest-runner",
        "preflight_runner_sha256": "preflight-runner",
        "test_sha256": "test",
        "isolation_helper_sha256": "isolation",
    }

    backtest.validate_preflight_manifest(
        manifest,
        prereg,
        preregistration_sha256="prereg",
        preflight_sha256="actual-preflight",
        checksum_sha256="actual-preflight",
    )
    manifest["backtest_runner_sha256"] = "wrong-runner"
    with pytest.raises(RuntimeError, match="backtest runner"):
        backtest.validate_preflight_manifest(
            manifest,
            prereg,
            preregistration_sha256="prereg",
            preflight_sha256="actual-preflight",
            checksum_sha256="actual-preflight",
        )
