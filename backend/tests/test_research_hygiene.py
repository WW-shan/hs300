from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
A = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
CORRECTIONS = E / "legacy_sidecar_corrections_2026-11-23.json"
INVENTORY = E / "research_hygiene_inventory_2026-11-23.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_tree_sidecars(root: Path) -> None:
    missing = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix in {".sha256", ".parquet"}:
            continue
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists():
            missing.append(path.relative_to(ROOT).as_posix())
            continue
        parts = sidecar.read_text(encoding="ascii").strip().split()
        assert len(parts) == 2, sidecar
        assert parts[1] == path.name, sidecar
        assert parts[0] == _digest(path), sidecar
    assert not missing, missing


def test_factor_research_tree_is_digest_bound() -> None:
    _assert_tree_sidecars(E)


def test_ai_evidence_tree_is_digest_bound_except_missing_xlsx_gaps() -> None:
    allowed = {
        str(ROOT / row["sidecar"]) for row in json.loads(CORRECTIONS.read_text())["missing"]
    }
    missing = []
    for path in A.rglob("*"):
        if not path.is_file() or path.suffix in {".sha256", ".parquet"}:
            continue
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists():
            if str(sidecar) not in allowed:
                missing.append(path.relative_to(ROOT).as_posix())
            continue
        parts = sidecar.read_text(encoding="ascii").strip().split()
        assert len(parts) == 2, sidecar
        assert parts[1] == path.name, sidecar
        assert parts[0] == _digest(path), sidecar
    assert not missing, missing


def test_corrections_and_inventory_are_checksummed() -> None:
    for path in (CORRECTIONS, INVENTORY):
        assert _digest(path) == path.with_name(path.name + ".sha256").read_text(
            encoding="ascii"
        ).split()[0]
    corrections = json.loads(CORRECTIONS.read_text())
    assert corrections["normalized_count"] == len(corrections["corrections"])
    assert corrections["unresolved_attachment_gaps"] == len(corrections["missing"])
    inventory = json.loads(INVENTORY.read_text())
    assert inventory["factor_output_count"] >= 150
    assert inventory["evidence_missing_sidecars"] == 0


def test_preregistration_chain_audit_is_checksummed_and_explicit() -> None:
    path = E / "preregistration_chain_audit_2026-11-23.json"
    assert _digest(path) == path.with_name(path.name + ".sha256").read_text(
        encoding="ascii"
    ).split()[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mismatch_count"] == sum(not row["match"] for row in payload["checks"])
    assert payload["mismatch_count"] == 4
