from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r389_core_set_joint_mc.py"
PREREG = EVIDENCE / "r389_core_set_joint_mc_preregistration.json"
REPORT = EVIDENCE / "r389_core_set_joint_mc.json"
RESULT = EVIDENCE / "r389_core_set_joint_mc_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r389_core_set_joint_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_freezes_parameters_and_train_test_split() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R389"
    assert payload["freeze"]["parameters"] == {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 110,
        "overlay_target_vol": 0.17,
        "overlay_equity_weight_max": 0.37,
    }
    assert payload["windows"]["train_end"] < payload["windows"]["test_start"]
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["output_policy"]["status"] == "joint_mc_complete_no_promotion"


def test_generate_joint_samples_is_deterministic_and_bounded() -> None:
    module = _module()
    first = module.generate_joint_samples()
    second = module.generate_joint_samples()

    assert first == second
    assert len(first) == 96
    assert len({row["sample_id"] for row in first}) == len(first)
    for row in first:
        construction = row["construction"]
        assert construction["lookback_days"] in (40, 60, 90)
        assert construction["min_weight"] in (0.15, 0.20, 0.25)
        assert construction["max_weight"] in (0.50, 0.60, 0.70)
        assert construction["min_weight"] <= construction["max_weight"]
        assert "execution" not in row


def test_dropout_keeps_at_least_one_leg_and_renormalizes() -> None:
    module = _module()
    base = {"A": 0.5, "B": 0.3, "C": 0.2}
    weights = module.renormalize_after_dropout(base, ["C"])
    assert weights == pytest.approx({"A": 0.625, "B": 0.375, "C": 0.0})
    with pytest.raises(ValueError, match="at least one"):
        module.renormalize_after_dropout(base, ["A", "B", "C"])


def test_no_leverage_gate_rejects_above_one() -> None:
    module = _module()
    assert module.weights_within_limit({"A": 0.5, "B": 0.3, "C": 0.2}) is True
    assert module.weights_within_limit({"A": 0.8, "B": 0.3, "C": 0.1}) is False


def test_runner_refuses_to_overwrite_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r389.json"
    result = tmp_path / "r389.md"
    monkeypatch.setattr(module, "OUTPUT", output)
    monkeypatch.setattr(module, "RESULT", result)

    module.ensure_outputs_absent()
    output.write_text("occupied", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        module.ensure_outputs_absent()


def test_committed_r389_evidence_matches_final_runner_and_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")

    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar

    assert report["round"] == "R389"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["decision"]["status"] == "joint_mc_complete_no_promotion"
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert report["no_leverage"] is True
    assert report["costs"]["5x"]["warning"]["hard_rejection"] is False
    assert "不生成订单" in markdown


def test_benchmark_construction_reproduces_r386_equity_sleeve() -> None:
    module = _module()
    result = module.evaluate_sample(
        {
            "sample": 0,
            "construction": {"lookback_days": 60, "min_weight": 0.20, "max_weight": 0.60},
            "parameters": {
                "c_satellite_cap": 0.13,
                "equity_target_vol": 0.17,
                "overlay_lookback_days": 110,
                "overlay_target_vol": 0.17,
                "overlay_equity_weight_max": 0.37,
            },
            "execution": {"entry_score": 70, "exit_score": 40, "hold_days": 30, "max_positions": 20},
            "dropped": [],
            "weights": {"A": 0.5, "B": 0.3, "C": 0.2},
        },
        cost="2x",
    )
    assert result["sleeve_curve"].height > 900
    assert result["curve"].height > 900
    assert result["max_weight_sum"] <= 1.0
