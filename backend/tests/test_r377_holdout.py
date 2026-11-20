from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r377_holdout.py"
REPORT = EVIDENCE / "r377_holdout.json"


def _module():
    spec = importlib.util.spec_from_file_location("r377_holdout", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_holdout_windows_are_disjoint_and_cover_the_declared_dates() -> None:
    module = _module()
    assert date(2025, 5, 5) == module.DEVELOPMENT_END
    assert date(2025, 5, 6) == module.SEALED_START
    assert module.DEVELOPMENT_END < module.SEALED_START
    assert date(2026, 11, 5) == module.SEALED_END
    assert module.CENTRE == "fade05_h30_sl20"
    assert module.CONTROL == "fade05_h20_sl20"


def test_spearman_is_deterministic_and_bounded() -> None:
    module = _module()
    left = [0.1, 0.2, 0.3, 0.4]
    right = [0.4, 0.3, 0.2, 0.1]
    assert module.spearman(left, right) == -1.0
    assert module.spearman(left, left) == 1.0
    assert module.spearman([1, 1, 1], [1, 2, 3]) == 0.0


def test_r377_evidence_is_hash_bound_and_holdout_pass_is_explicit() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for path in (
        EVIDENCE / "r377_preregistration.json",
        REPORT,
        EVIDENCE / "r377_result.md",
    ):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R377"
    assert payload["centre"] == "fade05_h30_sl20"
    assert payload["control"] == "fade05_h20_sl20"
    assert payload["sealed"][payload["centre"]]["days"] == 366
    assert payload["all_pass"] is True
    assert payload["sealed_excess_total_return"] > 0
    assert payload["sealed_excess_sharpe"] > 0
    assert payload["ranking_persistence"] >= 0.30
    assert payload["decision"]["holdout_claim_supported"] is True
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
