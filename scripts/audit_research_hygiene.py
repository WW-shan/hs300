#!/usr/bin/env python3
"""Audit factor-research provenance without changing research payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "factor_research"
EVIDENCE = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
)
RESEARCH = DATA / "research"
INVENTORY = RESEARCH / "research_hygiene_inventory_2026-11-23.json"
LEGACY_SIDECARS = RESEARCH / "legacy_orphan_sidecars_2026-11-23.json"
ROUND_ALIASES = RESEARCH / "round_alias_crosswalk_2026-11-23.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def sidecar_text(path: Path) -> str:
    return f"{digest(path)}  {path.name}\n"


def sidecar_targets(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.name.endswith(".sha256")
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo", ".tmp", ".lock"}
    )


def write_sidecar(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        sidecar_text(path), encoding="ascii"
    )


def is_verified_legacy_pointer(sidecar: Path) -> bool:
    """Return true only for a legacy pointer whose declared target is intact."""
    target = sidecar.with_name(sidecar.name[:-7])
    if target.exists():
        return False
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2:
        return False
    declared = sidecar.parent / parts[1]
    canonical = sidecar.parent / (parts[1] + ".sha256")
    return (
        declared.is_file()
        and canonical.is_file()
        and digest(declared) == parts[0]
        and (
            sidecar.read_bytes() == canonical.read_bytes()
            or sidecar.read_text(encoding="ascii").strip() == digest(declared)
        )
    )


def legacy_audit_row(sidecar: Path) -> dict[str, object] | None:
    if not is_verified_legacy_pointer(sidecar):
        return None
    parts = sidecar.read_text(encoding="ascii").strip().split()
    declared = sidecar.parent / parts[1]
    canonical = sidecar.parent / (parts[1] + ".sha256")
    return {
        "legacy_sidecar": relative(sidecar),
        "legacy_sha256": digest(sidecar),
        "legacy_content": sidecar.read_text(encoding="ascii").strip(),
        "declared_target": parts[1],
        "target": relative(declared),
        "target_sha256": digest(declared),
        "declared_digest": parts[0],
        "canonical_sidecar": relative(canonical),
        "canonical_sha256": digest(canonical),
        "equivalent_duplicate": sidecar.read_bytes() == canonical.read_bytes(),
    }



def main() -> int:
    # Every immutable research artifact under data/factor_research gets a
    # sidecar, including the ledger, catalog, pools, sets, PIT runs and audits.
    missing: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    for path in sidecar_targets(DATA):
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists():
            missing.append({"target": relative(path), "sidecar": relative(sidecar)})
            write_sidecar(path)
            continue
        parts = sidecar.read_text(encoding="ascii").strip().split()
        expected = digest(path)
        if len(parts) != 2 or parts[0] != expected or parts[1] != path.name:
            mismatched.append(
                {
                    "target": relative(path),
                    "sidecar": relative(sidecar),
                    "expected": expected,
                    "actual": sidecar.read_text(encoding="ascii").strip(),
                }
            )
            write_sidecar(path)

    prior_legacy = json.loads(LEGACY_SIDECARS.read_text(encoding="utf-8"))
    orphan_rows = {
        row["legacy_sidecar"]: row for row in prior_legacy["rows"]
    }
    for sidecar in sorted(EVIDENCE.glob("*.sha256")):
        row = legacy_audit_row(sidecar)
        if row is not None:
            orphan_rows[row["legacy_sidecar"]] = row

    for row in orphan_rows.values():
        assert row["declared_digest"] == row["target_sha256"], row["legacy_sidecar"]
        assert (ROOT / row["canonical_sidecar"]).is_file(), row["canonical_sidecar"]
        assert row["equivalent_duplicate"] is True, row["legacy_sidecar"]

    legacy_payload = {
        **prior_legacy,
        "count": len(orphan_rows),
        "rows": sorted(orphan_rows.values(), key=lambda row: row["legacy_sidecar"]),
    }
    LEGACY_SIDECARS.write_text(
        json.dumps(legacy_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_sidecar(LEGACY_SIDECARS)

    ROUND_ALIASES.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-11-23",
                "description": (
                    "Rounds with evidence files whose identifiers are not literal "
                    "plan headings; the source round and the runner remain explicit."
                ),
                "rows": [
                    {
                        "round": "R398N",
                        "source_round": "R398",
                        "reason": "neighborhood audit of the frozen R398 candidates",
                        "runner": "scripts/research_r398_neighborhood.py",
                    },
                    {
                        "round": "R398R",
                        "source_round": "R398",
                        "reason": "rolling/year audit of the frozen R398 candidates",
                        "runner": "scripts/research_r398_rolling.py",
                    },
                    {
                        "round": "R403N",
                        "source_round": "R403",
                        "reason": "neighborhood audit of the R403 core/beta family",
                        "runner": "scripts/research_r403_neighborhood.py",
                    },
                    {
                        "round": "R403V2",
                        "source_round": "R403",
                        "reason": "corrected v2 rerun of the frozen R403 core/beta family",
                        "runner": "scripts/research_r403_v2_core_beta.py",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_sidecar(ROUND_ALIASES)

    # Keep the existing inventory facts, but refresh counts and make the
    # historical gaps and alias coverage machine-readable.
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    inventory["evidence_file_count"] = sum(
        1
        for path in EVIDENCE.rglob("*")
        if path.is_file()
        and not path.name.endswith(".sha256")
        and "__pycache__" not in path.parts
    )
    inventory["evidence_missing_sidecars"] = sum(
        1
        for path in EVIDENCE.rglob("*")
        if path.is_file()
        and not path.name.endswith(".sha256")
        and "__pycache__" not in path.parts
        and not path.with_name(path.name + ".sha256").exists()
    )
    inventory["known_gaps"] = [
        {
            "kind": "preregistration_digest_mismatch",
            "count": 4,
            "audit": "data/factor_research/research/preregistration_chain_audit_2026-11-23.json",
            "disposition": "preserved; historical source unavailable",
        }
    ]
    inventory["round_aliases"] = "data/factor_research/research/round_alias_crosswalk_2026-11-23.json"
    inventory["legacy_orphan_sidecars"] = str(LEGACY_SIDECARS.relative_to(ROOT))
    INVENTORY.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_sidecar(INVENTORY)

    print(
        json.dumps(
            {
                "missing_sidecars_repaired": len(missing),
                "mismatched_sidecars_repaired": len(mismatched),
                "legacy_orphans_recorded": len(orphan_rows),
                "evidence_file_count": inventory["evidence_file_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
