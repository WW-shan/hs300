from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r373_hold30_recheck.py"
REPORT = EVIDENCE / "r373_hold30_recheck.json"


def _module():
    spec = importlib.util.spec_from_file_location("r373_hold30_recheck", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r373_center_reconstructs_r360_and_keeps_research_only() -> None:
    module = _module()
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    sidecar = REPORT.with_name(REPORT.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == sidecar
    assert payload["control_reconstructs_r360"] is True
    assert payload["decision"]["selected"] is None
    assert payload["decision"]["no_leverage"] is True
    assert all(row["pass"] is True for row in payload["rows"])
    assert payload["cost_scope"].startswith("2x only")
    assert module._cell_slug(5, 30, -0.2) == "fade05_h30_sl20"
