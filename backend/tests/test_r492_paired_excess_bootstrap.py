from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r492_paired_excess_bootstrap_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r492_paired_excess_bootstrap.py"


def _module():
    spec = importlib.util.spec_from_file_location("r492_paired", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r492_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R492"
    assert payload["source_selected_label"] == "equal"
    assert payload["bootstrap"]["pairing"].startswith("exact shared")
    assert payload["output_policy"]["active_version"] is None
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r492_pairing_fails_on_missing_dates() -> None:
    module = _module()
    assert module.pair_returns({"2024-01-02": 0.01}, {"2024-01-02": 0.0}) == [
        ("2024-01-02", 0.01, 0.0)
    ]
    try:
        module.pair_returns(
            {f"2024-01-{day:02d}": 0.01 for day in range(1, 32)}, {"2024-01-01": 0.0}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("missing benchmark dates beyond tolerance must fail closed")
    long_strategy = {
        f"2024-{month:02d}-{day:02d}": 0.01
        for month, day in [(m, d) for m in (1, 2, 3, 4) for d in range(1, 29)][:101]
    }
    long_benchmark = {day: 0.0 for day in list(long_strategy)[:-1]}
    assert len(module.pair_returns(long_strategy, long_benchmark)) == 100


def test_r492_outputs_separate_absolute_and_excess() -> None:
    output = E / "r492_paired_excess_bootstrap_2026-11-22.json"
    result = E / "r492_paired_excess_bootstrap_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    for window in ("test", "recent"):
        item = payload["windows"][window]
        assert item["pair_count"] > 0
        assert item["strategy_date_count"] >= item["pair_count"]
        assert item["missing_benchmark_dates"] == ["2026-10-15"]
        assert "absolute" in item and "active" in item
    assert payload["decision"]["active_version"] is None
