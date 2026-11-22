from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r487_rebalance_reentry_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r487_rebalance_reentry.py"


def _module():
    spec = importlib.util.spec_from_file_location("r487_horizon", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r487_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R487"
    assert payload["hold_days_grid"] == [10, 15, 20, 25, 30, 40, 50]
    assert payload["fixed"]["max_new_positions_per_day"] == 1
    assert payload["fixed"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r487_samples_vary_only_horizon() -> None:
    module = _module()
    rows = module.generate_samples()
    assert len(rows) == 7
    first = rows[0]
    for row in rows:
        assert row["weights"] == first["weights"]
        assert row["directions"] == first["directions"]
        assert row["max_new_positions_per_day"] == 1
        assert row["max_exposure_pct"] == 1.0
        assert row["max_hold_days"] in {10, 15, 20, 25, 30, 40, 50}


def test_r487_outputs_are_confirmation_only() -> None:
    output = E / "r487_rebalance_reentry_2026-11-22.json"
    result = E / "r487_rebalance_reentry_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["selected"]["max_hold_days"] in {10, 15, 20, 25, 30, 40, 50}
    assert payload["selected"]["max_exposure_pct"] <= 1.0
