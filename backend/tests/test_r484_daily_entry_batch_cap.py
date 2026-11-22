from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r484_daily_entry_batch_cap_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r484_daily_entry_batch_cap.py"


def _module():
    spec = importlib.util.spec_from_file_location("r484_cap", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r484_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R484"
    assert payload["caps"] == [None, 1, 2, 3]
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r484_samples_freeze_everything_except_cap() -> None:
    module = _module()
    rows = module.generate_samples()
    assert len(rows) == 4
    base = rows[0]
    assert base["max_new_positions_per_day"] is None
    for row in rows:
        assert row["weights"] == base["weights"]
        assert row["directions"] == base["directions"]
        assert row["max_positions"] == base["max_positions"]
        assert row["max_hold_days"] == base["max_hold_days"]
        assert row["max_exposure_pct"] == base["max_exposure_pct"]
        assert row["entry_score"] == base["entry_score"]
        assert row["exit_score"] == base["exit_score"]
        assert row["top_rank"] == base["top_rank"]
        assert row["max_new_positions_per_day"] in {None, 1, 2, 3}


def test_r484_outputs_are_confirmation_only_and_cap_is_real() -> None:
    output = E / "r484_daily_entry_batch_cap_2026-11-22.json"
    result = E / "r484_daily_entry_batch_cap_2026-11-22.md"
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
    baseline = next(row for row in payload["rows"] if row["max_new_positions_per_day"] is None)
    capped = next(row for row in payload["rows"] if row["max_new_positions_per_day"] == 1)
    assert (
        capped["test"]["execution"]["buy_no_slot"] != baseline["test"]["execution"]["buy_no_slot"]
    )
    assert payload["selected"]["max_new_positions_per_day"] == 1
