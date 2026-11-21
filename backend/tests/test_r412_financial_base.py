from __future__ import annotations

import importlib.util
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "research_r412_financial_base.py"


def _module():
    spec = importlib.util.spec_from_file_location("r412_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r412_classify_marks_missing_data_and_requires_all_gates() -> None:
    module = _module()
    rows = {
        "ok": {
            "coverage": {"non_null_rows": 1000},
            "marginal": {
                "n_dates": 200,
                "directional_delta_mean": 0.01,
                "delta_p_value": 0.02,
            },
            "max_correlation": 0.2,
        },
        "weak": {
            "coverage": {"non_null_rows": 1000},
            "marginal": {
                "n_dates": 200,
                "directional_delta_mean": 0.01,
                "delta_p_value": 0.2,
            },
            "max_correlation": 0.2,
        },
        "missing": {
            "coverage": {"non_null_rows": 0},
            "marginal": {"n_dates": 0},
            "max_correlation": None,
        },
        "correlated": {
            "coverage": {"non_null_rows": 1000},
            "marginal": {
                "n_dates": 200,
                "directional_delta_mean": 0.01,
                "delta_p_value": 0.02,
            },
            "max_correlation": 0.95,
        },
    }

    module.classify(rows)

    assert rows["ok"]["status"] == "statistical_pass"
    assert rows["weak"]["status"] == "statistical_rejected"
    assert rows["missing"]["status"] == "data_unavailable"
    assert rows["correlated"]["status"] == "statistical_rejected"


def test_r412_correlation_summary_uses_fixed_key(monkeypatch) -> None:
    module = _module()

    def fake_corr(frame, columns, *, minimum_symbols):
        assert columns == {"core": "_core", "candidate": "_candidate"}
        assert minimum_symbols == 30
        return {}, {"core|candidate": {"mean_abs": -0.25}}

    monkeypatch.setattr(module, "pairwise_cross_sectional_correlation", fake_corr)

    assert module.correlation_summary(pl.DataFrame({"date": [1], "_core": [1.0], "_candidate": [2.0]}), "_core", "_candidate") == 0.25
