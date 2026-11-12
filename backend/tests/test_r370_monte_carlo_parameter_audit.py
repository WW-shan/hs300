from __future__ import annotations

import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
    "run_r370_monte_carlo_parameter_audit.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r370_monte_carlo_audit", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cell(*, annual: float = 0.12, sharpe: float = 1.25, mdd: float = -0.09) -> dict:
    return {
        "metrics": {
            "stats": {"annual": annual, "sharpe": sharpe, "mdd": mdd},
            "recent": {"total_return": 0.04},
            "stress": {"annual": 0.09, "sharpe": 1.05, "mdd": -0.11},
            "yearly": {"2022": -0.01, "2023": 0.07},
            "bootstrap": {"positive_rate": 0.90, "mdd_p05": -0.11},
        }
    }


def test_circular_block_bootstrap_is_deterministic() -> None:
    module = _module()
    returns = [0.001] * 260

    first = module.circular_block_bootstrap(returns, block_days=20, horizon=252, paths=200, seed=7)
    second = module.circular_block_bootstrap(returns, block_days=20, horizon=252, paths=200, seed=7)

    assert first == second
    assert first["positive_rate"] == 1.0
    assert first["return_p05"] > 0
    assert math.isclose(first["return_median"], 1.001**252 - 1.0, rel_tol=1e-12)


def test_parameter_cell_evaluation_uses_v2_performance_and_bootstrap_thresholds() -> None:
    module = _module()

    good = module.evaluate_parameter_cell(_cell())
    weak = module.evaluate_parameter_cell(_cell(annual=0.07, sharpe=0.95, mdd=-0.21))

    assert good["point_performance_pass"] is True
    assert good["bootstrap_pass"] is True
    assert good["joint_pass"] is True
    assert weak["point_performance_pass"] is False
    assert weak["joint_pass"] is False


def test_parameter_cell_reads_nested_5x_stats_as_stress() -> None:
    module = _module()
    cell = _cell()
    cell["metrics_2x"] = cell.pop("metrics")
    cell["metrics_2x"].pop("stress")
    cell["metrics_5x"] = {
        "stats": {"annual": 0.10, "sharpe": 1.10, "mdd": -0.12},
        "bootstrap": {"positive_rate": 0.88, "mdd_p05": -0.13},
    }

    evaluated = module.evaluate_parameter_cell(cell)

    assert evaluated["point_performance_pass"] is True
    assert evaluated["joint_pass"] is True


def test_audit_reconstructs_r360_and_separates_tested_from_untested_parameters() -> None:
    module = _module()

    report = module.build_monte_carlo_audit()

    reconstructed = report["r360_reconstruction"]
    assert math.isclose(reconstructed["stats"]["annual"], 0.126054, abs_tol=5e-6)
    assert math.isclose(reconstructed["stats"]["sharpe"], 1.329779, abs_tol=5e-6)
    assert reconstructed["matches_frozen_result"] is True
    assert set(report["parameter_lineages"]) >= {"r344", "r350", "r354"}
    assert report["r360_path_monte_carlo"]["circular_blocks"]["20"]["positive_rate"] >= 0.80
    inventory = {row["parameter"]: row for row in report["parameter_inventory"]}
    assert inventory["stock_rel_industry_lookback_days"]["status"] == "not_monte_carlo_tested"
    assert inventory["industry_fade_change_days"]["status"] == "not_monte_carlo_tested"
    assert inventory["risk_parity_lookback_days"]["status"] in {
        "qualified_plateau",
        "partially_qualified",
    }
