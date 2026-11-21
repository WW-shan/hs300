from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r394_cross_source_resonance.py"
PREREG = ROOT / "data/research/r394_cross_source_resonance_preregistration.json"
REPORT = ROOT / "data/factor_research/research/r394_cross_source_resonance_2026-11-22.json"
RESULT = ROOT / "data/factor_research/research/r394_cross_source_resonance_2026-11-22.md"


def _module():
    spec = importlib.util.spec_from_file_location("r394_cross_source_resonance", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preregistration_is_frozen_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert _sha256(PREREG) == sidecar
    assert payload["round"] == "R394"
    assert payload["minimum_valid_dates"] == 120
    assert payload["minimum_valid_symbols"] == 30
    assert payload["output_policy"]["status"] == "resonance_feasibility_complete_no_promotion"
    assert payload["output_policy"]["active_version"] is None


def test_layers_are_exactly_preregistered() -> None:
    module = _module()
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert len(module.load_conditions(payload)) == len(payload["resonance_rules"]) == 4


def test_committed_r394_evidence_is_checksum_bound_and_has_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")
    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert _sha256(path) == sidecar
    assert report["round"] == "R394"
    assert report["runner_sha256"] == _sha256(RUNNER)
    assert report["decision"]["alpha_evidence"] is False
    assert report["decision"]["active_version"] is None
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert all(layer["pass"] is False for layer in report["layers"])
    assert "不生成纸面目标" in markdown


def test_runner_does_not_write_existing_evidence(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r394.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUT", output)
    try:
        module.main()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner overwrote existing evidence")
