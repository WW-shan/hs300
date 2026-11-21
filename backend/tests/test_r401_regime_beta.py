from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data/factor_research/research/r401_regime_beta_preregistration.json"


def test_frozen_design() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R401"
    assert payload["core"]["id"] == 9
    assert payload["beta"]["factor_id"] == "ext_alt_hs300_trend_efficiency_60d"
    assert payload["portfolio"]["no_leverage"] is True
    assert payload["output_policy"]["active_version"] is None
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar


def test_rules_and_benchmark_are_pre_registered() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert len(payload["regime_rules"]) >= 5
    assert payload["selection"]["basis"] == "train_and_validation"
    assert payload["benchmark"]["symbol"] == "000300.SH"
