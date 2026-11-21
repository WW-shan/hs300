from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_preregistration_freezes_nonoverlapping_windows() -> None:
    path = E / "r398_holdout_preregistration.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert sha(path) == sidecar
    assert payload["windows"]["train_end"] < payload["windows"]["validation_start"]
    assert payload["windows"]["validation_end"] < payload["windows"]["test_start"]
    assert payload["portfolio"]["max_hold_override"] is True
    assert payload["output_policy"]["active_version"] is None


def test_grid_has_all_subsets_and_hold_variants() -> None:
    runner = module(ROOT / "scripts/research_r398_strict_mc.py", "r398_runner_test")
    prereg = json.loads((E / "r398_holdout_preregistration.json").read_text(encoding="utf-8"))
    rows = runner.grid(prereg, prereg["factor_ids"])
    assert len(rows) == 1962
    assert {row["max_hold_days"] for row in rows} == {20, 30, 40}
    assert all(abs(sum(row["weights"].values()) - 1.0) < 1e-9 for row in rows)


def test_portfolio_config_passes_max_hold_override() -> None:
    runner = module(ROOT / "scripts/research_factor_set_portfolio.py", "r398_portfolio_test")
    config = runner._backtest_config(
        {"factor": 1.0},
        {"factor": "high"},
        start=runner.date(2025, 1, 1),
        end=runner.date(2025, 2, 1),
        max_positions=10,
        cost=runner._cost_parameters("2x"),
        max_hold_days=40,
    )
    assert config.overrides == {"max_hold_days": 40}


def test_committed_reports_are_checksum_bound_and_no_execution() -> None:
    names = [
        "r398_strict_mc_2026-11-22.json",
        "r398_strict_mc_2026-11-22.md",
        "r398_neighborhood_2026-11-22.json",
        "r398_neighborhood_2026-11-22.md",
        "r398_rolling_2026-11-22.json",
        "r398_rolling_2026-11-22.md",
    ]
    for name in names:
        path = E / name
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert sha(path) == sidecar
    report = json.loads((E / "r398_strict_mc_2026-11-22.json").read_text(encoding="utf-8"))
    assert report["decision"]["status"] == "strict_holdout_complete_no_promotion"
    assert report["decision"]["active_version"] is None
    assert all(row.get("test_2x", {}).get("benchmark_return") is not None for row in report["validation_stable_candidates"])
