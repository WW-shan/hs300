from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r469_operating_efficiency_attribution_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r469_operating_efficiency_attribution.py"
OUTPUT = E / "r469_operating_efficiency_attribution_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r469_attribution", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r469_preregistration_freezes_source_and_no_reselection() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R469"
    assert _sha(PREREG) == sidecar
    assert payload["source_round"] == "R468"
    assert payload["rules"]["selection_allowed"] is False
    assert payload["rules"]["test_or_recent_used_for_new_parameters"] is False
    assert payload["rules"]["five_x_hard_rejection"] is False


def test_r469_top_trade_removal_is_deterministic() -> None:
    module = _module()
    trades = [{"pnl_amount": value, "symbol": f"S{index}", "entry_date": "2026-01-01"} for index, value in enumerate([10.0, -3.0, 7.0, 2.0])]
    result = module.remove_top_trades(trades, count=1)
    assert result["removed_count"] == 1
    assert result["remaining_pnl"] == 6.0
    assert result["removed_pnl"] == 10.0


def test_r469_outputs_are_descriptive_only() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["source_round"] == "R468"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "operating_efficiency_attribution_complete_no_promotion"
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["diagnostics"]["five_x_warning_only"] is True


def test_r469_output_records_concentration_risk() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["trade_count"] == 164
    assert payload["top_trade_removal"]["10"]["remaining_pnl"] < payload["top_trade_removal"]["1"]["remaining_pnl"]
    assert payload["top_trade_removal"]["10"]["remaining_pnl"] < payload["total_pnl_amount"] * 0.1
