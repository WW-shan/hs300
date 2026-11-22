from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r520_industry_index_feasibility_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r520_industry_index_feasibility.py"


def _module():
    assert RUNNER.exists(), f"missing R520 research runner: {RUNNER}"
    spec = importlib.util.spec_from_file_location("r520_industry", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r520_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R520"
    assert payload["start"] == "2018-01-02"
    assert payload["end"] == "2026-11-05"
    assert payload["gates"]["minimum_long_history_industries"] == 20
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r520_history_metadata_uses_explicit_source_dates_and_amount() -> None:
    module = _module()
    frame = pd.DataFrame(
        {
            "日期": [
                date(2018, 1, 2),
                date(2018, 1, 3),
                date(2026, 11, 5),
            ],
            "收盘价": [100.0, 101.0, 102.0],
            "成交额": [10.0, 0.0, 20.0],
        }
    )
    row = module.describe_history(
        frame,
        source="ths",
        code="881121",
        name="半导体",
        start="2018-01-02",
        end="2026-11-05",
    )
    assert row == {
        "source": "ths",
        "code": "881121",
        "name": "半导体",
        "rows": 3,
        "first_date": "2018-01-02",
        "last_date": "2026-11-05",
        "positive_amount_ratio": 2 / 3,
        "start_ok": True,
        "end_ok": True,
        "amount_ok": False,
    }


def test_r520_gate_requires_twenty_long_history_industries() -> None:
    module = _module()
    rows = [
        {
            "source": "shenwan",
            "start_ok": index < 20,
            "end_ok": True,
            "amount_ok": True,
        }
        for index in range(21)
    ]
    gate = module.evaluate_gate(rows)
    assert gate["pass"] is True
    assert gate["long_history_count"] == 20
    assert gate["checks"]["minimum_long_history_industries"] is True


def test_r520_gate_rejects_short_or_negative_amount_history() -> None:
    module = _module()
    rows = [
        {"source": "shenwan", "start_ok": False, "end_ok": True, "amount_ok": True},
        {"source": "shenwan", "start_ok": True, "end_ok": False, "amount_ok": True},
        {"source": "shenwan", "start_ok": True, "end_ok": True, "amount_ok": False},
    ]
    gate = module.evaluate_gate(rows)
    assert gate["pass"] is False
    assert gate["long_history_count"] == 0
    assert gate["checks"]["minimum_long_history_industries"] is False


def test_r520_outputs_are_research_only_when_present() -> None:
    output = E / "r520_industry_index_feasibility_2026-11-22.json"
    result = E / "r520_industry_index_feasibility_2026-11-22.md"
    if not output.exists() or not result.exists():
        return
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["production_enabled"] is False
