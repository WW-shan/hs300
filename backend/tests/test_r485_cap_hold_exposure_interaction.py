from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r485_cap_hold_exposure_interaction_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r485_cap_hold_exposure_interaction.py"


def _module():
    spec = importlib.util.spec_from_file_location("r485_interaction", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r485_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R485"
    assert payload["grid"]["max_hold_days"] == [20, 30, 40, 50]
    assert payload["grid"]["max_exposure_pct"] == [0.8, 0.9, 1.0]
    assert payload["fixed"]["max_new_positions_per_day"] == 1
    assert payload["fixed"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r485_samples_vary_only_hold_and_exposure() -> None:
    module = _module()
    rows = module.generate_samples()
    assert len(rows) == 12
    first = rows[0]
    for row in rows:
        assert row["weights"] == first["weights"]
        assert row["directions"] == first["directions"]
        assert row["max_positions"] == first["max_positions"]
        assert row["max_new_positions_per_day"] == 1
        assert row["entry_score"] == first["entry_score"]
        assert row["exit_score"] == first["exit_score"]
        assert row["top_rank"] == first["top_rank"]
        assert 0.0 < row["max_exposure_pct"] <= 1.0
        assert row["max_hold_days"] in {20, 30, 40, 50}
