from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r376_regime_conditioning.py"
REPORT = EVIDENCE / "r376_regime_conditioning.json"


def _module():
    spec = importlib.util.spec_from_file_location("r376_regime_conditioning", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaling_is_never_below_declared_floor_and_never_levers_up() -> None:
    module = _module()
    assert module.MIN_WEIGHT == 0.2
    assert module.MAX_WEIGHT == 1.0
    assert all(0.2 <= value <= 1.0 for value in json.loads(module.PREREG.read_text(encoding="utf-8"))["scale_levels"])


def test_regime_fields_are_all_lagged(monkeypatch) -> None:
    import polars as pl

    module = _module()
    dates = pl.date_range(
        pl.date(2024, 1, 1),
        pl.date(2024, 3, 29),
        interval="1d",
        eager=True,
    )
    base = pl.DataFrame({
        "date": dates,
        "score": list(range(len(dates))),
        "above_ma20_pct": [0.5] * len(dates),
        "trend_score": [50] * len(dates),
        "profit_score": [60] * len(dates),
        "speculation_score": [30] * len(dates),
        "index_pct": [0.01] * len(dates),
    })
    monkeypatch.setattr(module.pl, "read_parquet", lambda _path: base)
    frame = module.regime_frame()

    # Every derived signal must be shifted at least one session and must not
    # equal the same-day source at the first observable row.
    assert frame["score_lag1"][0] is None
    assert frame["score_chg5_lag1"][0] is None
    for column in frame.columns:
        if column == "date":
            continue
        assert column.endswith("_lag1"), column


def test_baseline_contract_matches_r374_centre() -> None:
    module = _module()
    frozen = json.loads(module.R374.read_text(encoding="utf-8"))
    row = next(item for item in frozen["rows"] if item["cell"] == module.CELL)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert module.CELL == "fade05_h30_sl20"
    assert report["base"]["stats"]["annual"] == row["annual_2x"]
    assert report["base"]["stats"]["sharpe"] == row["sharpe_2x"]
    assert report["base"]["stats"]["mdd"] == row["mdd_2x"]


def test_r376_evidence_is_hash_bound_and_negative_knowledge_is_explicit() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for path in (
        EVIDENCE / "r376_preregistration.json",
        REPORT,
        EVIDENCE / "r376_result.md",
    ):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R376"
    assert payload["cell"] == "fade05_h30_sl20"
    assert payload["variant_count"] == 195
    assert payload["decision"]["conditioning_accepted"] is False
    assert payload["decision"]["regime_conditioning_is_negative_knowledge"] is True
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert payload["base"]["stats"]["annual"] == 0.13213756101409513


def test_every_reported_variant_has_bootstrap_and_plateau_fields() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["rows"]
    for row in payload["rows"]:
        assert "bootstrap" in row
        assert "plateau_pass" in row
        assert 0.2 <= row["low_scale"] <= 1.0
