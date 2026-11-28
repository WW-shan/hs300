from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r559_ready_regime_gate.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r559_ready_regime_gate", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r559_declares_frozen_ready_strategy_and_six_gates() -> None:
    module = _runner()
    design = module.scientific_design()

    assert module.RESEARCH_ID == "R559"
    assert module.RESEARCH_TYPE == "project_formal"
    assert design["strategy_id"] == "ai_hs300_cash_safety_v14"
    assert design["factor_id"] == "uf_r550_cash_safety_rank"
    assert design["policy_id"] == "up_vol_target_14"
    assert design["fixed"]["max_positions"] == 10
    assert design["fixed"]["max_hold_days"] == 60
    assert design["fixed"]["stop_loss"] == -0.12
    assert [row["gate_id"] for row in design["gates"]] == [
        "always_on",
        "score_min_35",
        "score_min_40",
        "strong_lean_strong_only",
        "exclude_weak_with_score30",
        "risk_on_combo",
    ]


def test_r559_uses_t_minus_one_and_does_not_repeat_r534_failed_rules() -> None:
    module = _runner()
    rules = module.regime_rules()
    filters = {row["gate_id"]: row["regime_filter"] for row in rules}

    assert filters["always_on"] is None
    assert filters["score_min_35"] == {"states": [], "min_score": 35}
    assert filters["score_min_40"] == {"states": [], "min_score": 40}
    assert filters["strong_lean_strong_only"] == {
        "states": ["strong", "lean_strong"],
    }
    assert filters["exclude_weak_with_score30"] == {
        "states": ["strong", "lean_strong", "range", "lean_weak"],
        "min_score": 30,
    }
    assert filters["risk_on_combo"] == {
        "states": ["strong", "lean_strong", "range"],
        "min_score": 40,
    }
    assert module.scientific_design()["alignment"] == "T-1"
    assert filters["exclude_weak_with_score30"] != {
        "states": ["strong", "lean_strong", "range"],
        "min_score": 20,
    }


def test_r559_prepared_cache_key_includes_normalized_regime_filter() -> None:
    module = _runner()
    first = module.prepared_cache_key(
        module.date(2025, 1, 1),
        module.date(2025, 10, 13),
        {"states": ["strong", "lean_strong"]},
    )
    second = module.prepared_cache_key(
        module.date(2025, 1, 1),
        module.date(2025, 10, 13),
        {"states": ["strong"]},
    )
    same = module.prepared_cache_key(
        module.date(2025, 1, 1),
        module.date(2025, 10, 13),
        {"states": ["lean_strong", "strong"]},
    )

    assert first != second
    assert first == same


def test_r559_synthetic_service_exposes_data_dir_for_regime_loading(tmp_path: Path) -> None:
    module = _runner()
    service = module.build_synthetic_service(tmp_path)

    assert service.engine.repo.store.data_dir == tmp_path


def test_r559_declares_standard_validation_and_train_validation_selection() -> None:
    module = _runner()
    design = module.scientific_design()

    assert design["standard_validation"]["gbm"] == {
        "required": True,
        "path_count": 100,
    }
    assert design["standard_validation"]["parameter_mc"]["selection_policy"] == (
        "train_select_validation_confirm"
    )
    assert design["standard_validation"]["parameter_mc"]["recent_used_for_selection"] is False
    assert design["gates_gate"]["five_x_hard_rejection"] is False
    assert design["gates_gate"]["validation_beats_always_on"] is True


def _r558_result(module, *, entry=80.0, top=30, cap=2):
    return {
        "research_id": "R558",
        "conclusion": "watch_only",
        "metrics": {
            "selection": {
                "selected": {
                    "eligible": True,
                    "params": {
                        "entry_score": entry,
                        "top_rank": top,
                        "max_industry_positions": cap,
                    },
                }
            }
        },
    }


def test_r559_loads_r558_selected_params_and_rejects_mismatch(tmp_path: Path) -> None:
    module = _runner()
    root = tmp_path
    path = root / module.R558_RESULT.relative_to(module.ROOT)
    path.parent.mkdir(parents=True)
    path.write_text(__import__("json").dumps(_r558_result(module)), encoding="utf-8")

    assert module.load_r558_params(root) == {
        "entry_score": 80.0,
        "top_rank": 30,
        "max_industry_positions": 2,
    }

    path.write_text(__import__("json").dumps(_r558_result(module, top=20)), encoding="utf-8")
    try:
        module.load_r558_params(root)
    except RuntimeError as exc:
        assert "R558 selected params changed" in str(exc)
    else:
        raise AssertionError("mismatched R558 params must be rejected")


def test_r559_selects_gate_on_training_only_and_ignores_validation(tmp_path: Path) -> None:
    module = _runner()
    points = [
        {
            "gate_id": "always_on",
            "regime_filter": None,
            "train": {"stats": {"annual_return": 0.10, "sharpe": 1.30, "max_drawdown": -0.08, "n_trades": 40}},
            "validation": {"stats": {"annual_return": 0.99, "sharpe": 9.0, "max_drawdown": -0.01, "n_trades": 40}},
        },
        {
            "gate_id": "score_min_35",
            "regime_filter": {"states": [], "min_score": 35},
            "train": {"stats": {"annual_return": 0.12, "sharpe": 1.20, "max_drawdown": -0.07, "n_trades": 40}},
            "validation": {"stats": {"annual_return": -0.50, "sharpe": -9.0, "max_drawdown": -0.40, "n_trades": 1}},
        },
    ]
    selection = module.select_training_gate(points, baseline_training_annual=0.10)
    assert selection["selected"]["gate_id"] == "score_min_35"
    assert selection["selected"]["train"]["stats"]["annual_return"] == 0.12
    assert selection["selection_inputs"] == "train_only"


def test_r559_falls_back_to_always_on_when_no_gate_passes() -> None:
    module = _runner()
    points = [
        {
            "gate_id": "always_on",
            "regime_filter": None,
            "train": {"stats": {"annual_return": 0.05, "sharpe": 0.80, "max_drawdown": -0.20, "n_trades": 5}},
            "validation": {"stats": {"annual_return": 0.20, "sharpe": 2.0, "max_drawdown": -0.01, "n_trades": 20}},
        },
        {
            "gate_id": "score_min_35",
            "regime_filter": {"states": [], "min_score": 35},
            "train": {"stats": {"annual_return": 0.04, "sharpe": 0.70, "max_drawdown": -0.22, "n_trades": 4}},
            "validation": {"stats": {"annual_return": 0.30, "sharpe": 3.0, "max_drawdown": -0.01, "n_trades": 20}},
        },
    ]
    selection = module.select_training_gate(points, baseline_training_annual=0.05)
    assert selection["selected"]["gate_id"] == "always_on"
    assert selection["fallback"] is True
    assert selection["selected"]["eligible"] is False


def test_r559_always_on_cannot_be_promoted() -> None:
    module = _runner()
    assert module.conclusion_for_gate("always_on", gate_pass=True) == "rejected"
    assert module.conclusion_for_gate("score_min_35", gate_pass=True) == "watch_only"
    assert module.conclusion_for_gate("score_min_35", gate_pass=False) == "rejected"


def test_r559_gbm_starts_on_second_day_to_preserve_t_minus_one() -> None:
    module = _runner()
    dates = tuple(module.date(2026, 1, day) for day in range(1, 6))
    assert module.gbm_formal_start(dates) == dates[1]
    assert module.gbm_benchmark_years(504) == 503 / 252


def test_r559_preregistration_is_immutable_and_runner_bound(tmp_path: Path, monkeypatch) -> None:
    module = _runner()
    root = tmp_path
    inputs = {}
    for name in ("r558_result", "r558_preregistration", "strategy", "factor", "regime", "matrix", "position_policies"):
        path = root / "inputs" / f"{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
        inputs[name] = path
    monkeypatch.setattr(module, "_input_paths", lambda _root: inputs)

    path = module.freeze_preregistration(root)
    assert path.exists()
    assert path.with_name(path.name + ".sha256").exists()
    try:
        module.freeze_preregistration(root)
    except FileExistsError:
        pass
    else:
        raise AssertionError("preregistration must not be overwritten")

    module._verify_binding(root)
    inputs["strategy"].write_text("changed", encoding="utf-8")
    try:
        module._verify_binding(root)
    except RuntimeError as exc:
        assert "input changed" in str(exc)
    else:
        raise AssertionError("changed input must invalidate the binding")


def test_r559_preregistration_binds_attempt1_failure_history(tmp_path: Path) -> None:
    module = _runner()

    inputs = module._input_paths(tmp_path)

    assert inputs["r559_attempt_preregistration"].name == (
        "r559_attempt1_ready_regime_gate_preregistration.json"
    )
    assert inputs["r559_attempt_failure"].name == (
        "r559_attempt1_standard_validation_receipt_failure.json"
    )


def test_r559_config_injects_frozen_r558_params_and_regime_filter(monkeypatch) -> None:
    module = _runner()

    class Cost:
        def __call__(self, _p5, _label):
            return {
                "fees_pct": 0.0003,
                "commission_pct": 0.0003,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 2.0,
            }

    runtime = {
        "r533": type("R533", (), {"cost_parameters": staticmethod(Cost())})(),
        "symbols": ["000001"],
        "industries": {"000001": "银行"},
    }
    monkeypatch.setattr(module, "load_r558_params", lambda _root: {"entry_score": 80.0, "top_rank": 30, "max_industry_positions": 2})
    config = module._config(
        object(),
        runtime,
        start=module.date(2025, 1, 1),
        end=module.date(2025, 10, 13),
        cost_label="3x",
        regime_filter={"states": ["strong"], "min_score": 35},
    )

    assert config.params["entry_score"] == 80.0
    assert config.params["top_rank"] == 30
    assert config.params["max_industry_positions"] == 2
    assert config.regime_filter == {"states": ["strong"], "min_score": 35}
    assert config.max_positions == 10
    assert config.max_exposure_pct == 1.0


def test_r559_gate_uses_validation_comparison_and_keeps_five_x_as_warning() -> None:
    module = _runner()
    stats = {"annual_return": 0.12, "sharpe": 1.20, "max_drawdown": -0.10, "total_return": 0.30}
    gate = module.gate_for_gate(
        full={"stats": stats},
        full_3x={"stats": stats},
        full_5x={"stats": {**stats, "annual_return": -0.50}},
        validation={"stats": stats},
        always_on_validation={"stats": {**stats, "annual_return": 0.05}},
        recent={"stats": stats},
        fresh=[{"stats": stats}] * 5,
        long_phase=[{"stats": stats}] * 10,
        formal_evidence={"persistence": "saved", "lookahead": "passed", "execution_timing": "passed"},
        always_on_formal_evidence={"persistence": "saved", "lookahead": "passed", "execution_timing": "passed"},
    )

    assert gate["checks"]["validation_beats_always_on"] is True
    assert gate["checks"]["formal_evidence"] is True
    assert gate["five_x_hard_rejection"] is False
    assert gate["pass"] is True


def test_r559_standard_parameter_receipt_uses_training_selection(monkeypatch) -> None:
    module = _runner()
    selection = {
        "selected": {
            "gate_id": "score_min_35",
            "params": {"entry_score": 80.0, "top_rank": 30, "max_industry_positions": 2},
            "train": {"stats": {"annual_return": 0.12, "max_drawdown": -0.10}, "trade_count": 20},
            "validation": {"stats": {"annual_return": 0.02}},
        },
        "points": [{"gate_id": "always_on"}, {"gate_id": "score_min_35"}],
    }
    bootstrap = {"method": "moving_block_bootstrap", "windows": {"train": {"total_p50": 0.1}}}
    monkeypatch.setattr(module, "bootstrap_equity_curve", lambda *args, **kwargs: {"total_p50": 0.1})
    receipt = module.standard_parameter_receipt(selection, bootstrap)

    assert receipt["status"] == "complete"
    assert receipt["grid_size"] == 6
    assert receipt["selected"]["gate_id"] == "score_min_35"
    assert receipt["selected"]["regime_filter"] is None
    assert receipt["constraints"]["validation_confirmed"] is True
    assert receipt["recent_used_for_selection"] is False


def test_r559_publish_refuses_existing_result(tmp_path: Path, monkeypatch) -> None:
    module = _runner()
    result = tmp_path / "result.json"
    result.write_text("already here", encoding="utf-8")
    monkeypatch.setattr(module, "RESULT", result)
    try:
        module._require_absent_result()
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing result must not be overwritten")
