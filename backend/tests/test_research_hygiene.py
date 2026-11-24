from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTOR = ROOT / "data/factor_research"
E = FACTOR / "research"
A = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
CORRECTIONS = E / "legacy_sidecar_corrections_2026-11-23.json"
INVENTORY = E / "research_hygiene_inventory_2026-11-23.json"
LEGACY_SIDECARS = E / "legacy_orphan_sidecars_2026-11-23.json"
ROUND_ALIASES = E / "round_alias_crosswalk_2026-11-23.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_runtime_artifact(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def _is_tracked_immutable_artifact(path: Path) -> bool:
    return not _is_runtime_artifact(path) and path.suffix not in {".tmp", ".lock"}


def _assert_sidecar(path: Path) -> bool:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return False
    parts = sidecar.read_text(encoding="ascii").strip().split()
    assert len(parts) == 2, sidecar
    assert parts[1] == path.name, sidecar
    assert parts[0] == _digest(path), sidecar
    return True


def _assert_tree_sidecars(root: Path) -> None:
    missing = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix == ".sha256":
            continue
        if not _is_tracked_immutable_artifact(path):
            continue
        if not _assert_sidecar(path):
            missing.append(path.relative_to(ROOT).as_posix())
    assert not missing, missing


def test_factor_research_tree_is_digest_bound() -> None:
    _assert_tree_sidecars(FACTOR)


def test_ai_evidence_tree_is_digest_bound() -> None:
    _assert_tree_sidecars(A)


def test_corrections_and_hygiene_audits_are_checksummed() -> None:
    for path in (CORRECTIONS, INVENTORY, LEGACY_SIDECARS, ROUND_ALIASES):
        assert _assert_sidecar(path)
    corrections = json.loads(CORRECTIONS.read_text())
    assert corrections["normalized_count"] == len(corrections["corrections"])
    assert corrections["unresolved_attachment_gaps"] == len(corrections["missing"])
    inventory = json.loads(INVENTORY.read_text())
    assert inventory["factor_output_count"] >= 150
    evidence_files = [
        path for path in A.rglob("*")
        if path.is_file() and not path.name.endswith(".sha256") and "__pycache__" not in path.parts
    ]
    assert inventory["evidence_file_count"] == len(evidence_files)
    assert inventory["evidence_missing_sidecars"] == 0
    assert inventory["known_gaps"][0]["count"] == 4


def test_legacy_orphan_sidecars_are_fully_audited_and_removed() -> None:
    payload = json.loads(LEGACY_SIDECARS.read_text(encoding="utf-8"))
    assert payload["count"] == len(payload["rows"]) == 21
    assert all(row["equivalent_duplicate"] for row in payload["rows"])
    assert not any((ROOT / row["legacy_sidecar"]).exists() for row in payload["rows"])
    assert all((ROOT / row["canonical_sidecar"]).exists() for row in payload["rows"])


def test_amendment_runner_hashes_are_current() -> None:
    import json

    path = E / "diagnostic_simulation_amendment_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        assert _digest(ROOT / row["runner"]) == row["runner_sha256"], row["runner"]


def test_supplemental_round_aliases_are_explicit() -> None:
    payload = json.loads(ROUND_ALIASES.read_text(encoding="utf-8"))
    rounds = {row["round"] for row in payload["rows"]}
    assert rounds == {"R398N", "R398R", "R403N", "R403V2"}
    for row in payload["rows"]:
        assert (ROOT / row["runner"]).is_file()
        assert row["source_round"] in row["round"]


def test_preregistration_chain_audit_is_checksummed_and_explicit() -> None:
    path = E / "preregistration_chain_audit_2026-11-23.json"
    assert _assert_sidecar(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mismatch_count"] == sum(not row["match"] for row in payload["checks"])
    assert payload["mismatch_count"] == 4


def test_research_asset_ledger_matches_current_strategy_contract() -> None:
    """Every versioned AI strategy must be research-only and contract-complete."""
    from app.strategy.engine import StrategyEngine

    ai_dir = ROOT / "data" / "strategies" / "ai"
    engine = StrategyEngine(strategy_dirs=[ai_dir])
    assert engine.load_errors() == []
    definitions = engine.strategy_definitions()
    assert definitions
    assert all(s.meta.get("research_only") is True for s in definitions)
    assert all(s.meta.get("params") is not None for s in definitions)
    assert all(s.meta.get("scoring") for s in definitions)
    assert all(s.execution_backend in {"polars_expr", "matrix_native"} for s in definitions)

    ledger = json.loads(
        (E / "research_asset_ledger_2026-11-25_v34.json").read_text(encoding="utf-8")
    )
    strategy_rows = [
        row for row in ledger["rows"]
        if row.get("kind") == "strategy" and row["path"].startswith("data/strategies/ai/")
    ]
    assert len(strategy_rows) == len(definitions)
    assert all(row["status"] == "valid" for row in strategy_rows)
    assert all(row["disposition"] == "keep" for row in strategy_rows)


def test_strategy_audit_ledger_is_checksummed_and_all_ai_strategies_are_kept() -> None:
    path = E / "research_asset_ledger_2026-11-25_v34.json"
    assert _assert_sidecar(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    ai = [
        row for row in rows
        if row.get("kind") == "strategy" and row["path"].startswith("data/strategies/ai/")
    ]
    assert len(ai) == len(list((ROOT / "data/strategies/ai").glob("*.py")))
    assert all(row["status"] == "valid" for row in ai)
    assert all(row["disposition"] == "keep" for row in ai)
    assert payload["counts"]["valid"] >= len(ai)
    assert payload["dispositions"].get("convert_or_quarantine", 0) == 0
    assert payload["dispositions"].get("convert_or_delete", 0) == 0
    assert set(payload["dispositions"]).issubset(
        {"keep", "keep_as_negative_knowledge", "keep_private"}
    )
    assert payload["counts"]["non_return_research"] >= 1
    assert payload["counts"]["project_chain_with_formal_evidence"] >= 80
    assert payload["counts"].get("project_chain_without_formal_evidence", 0) == 0
    assert payload["counts"].get("non_project_backtest_or_custom_simulation", 0) == 0
    diagnostic_rows = [
        row for row in rows
        if row.get("kind") == "runner"
        and row["status"] == "diagnostic_custom_simulation"
    ]
    assert diagnostic_rows
    assert all(row["disposition"] == "keep_as_negative_knowledge" for row in diagnostic_rows)
