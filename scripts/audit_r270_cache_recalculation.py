#!/usr/bin/env python3
"""Audit whether initial and finalized R270 cache outputs are identical."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INITIAL = ROOT / "data" / "research" / "r270_cache"
FINAL = ROOT / "data" / "research" / "r270_cache_final"
OUT = (
    ROOT
    / "data"
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
    / "r270_cache_recalculation_audit.json"
)


def _proof(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _files(directory: Path, pattern: str) -> dict[str, Path]:
    return {path.name: path for path in directory.glob(pattern) if path.is_file()}


def main() -> int:
    output_comparisons = []
    manifest_comparisons = []
    all_outputs_match = True

    for pattern in ("*.parquet", "*.trades.json"):
        initial_files = _files(INITIAL, pattern)
        final_files = _files(FINAL, pattern)
        all_outputs_match = all_outputs_match and set(initial_files) == set(final_files)
        for name in sorted(set(initial_files) | set(final_files)):
            initial = initial_files.get(name)
            final = final_files.get(name)
            same = bool(
                initial
                and final
                and _proof(initial)["sha256"] == _proof(final)["sha256"]
            )
            all_outputs_match = all_outputs_match and same
            output_comparisons.append(
                {
                    "filename": name,
                    "kind": "curve" if name.endswith(".parquet") else "trades",
                    "same_bytes": same,
                    "initial": _proof(initial) if initial else None,
                    "final": _proof(final) if final else None,
                }
            )

    initial_manifests = _files(INITIAL, "*.manifest.json")
    final_manifests = _files(FINAL, "*.manifest.json")
    manifests_ok = set(initial_manifests) == set(final_manifests)
    for name in sorted(set(initial_manifests) | set(final_manifests)):
        initial_path = initial_manifests.get(name)
        final_path = final_manifests.get(name)
        if not initial_path or not final_path:
            differing_fields = ["manifest_missing"]
        else:
            initial_manifest = json.loads(initial_path.read_text(encoding="utf-8"))
            final_manifest = json.loads(final_path.read_text(encoding="utf-8"))
            keys = set(initial_manifest) | set(final_manifest)
            differing_fields = sorted(
                key
                for key in keys
                if initial_manifest.get(key) != final_manifest.get(key)
            )
        only_runner_hash_changed = differing_fields == ["runner_sha256"]
        manifests_ok = manifests_ok and only_runner_hash_changed
        manifest_comparisons.append(
            {
                "filename": name,
                "differing_fields": differing_fields,
                "only_runner_hash_changed": only_runner_hash_changed,
                "initial": _proof(initial_path) if initial_path else None,
                "final": _proof(final_path) if final_path else None,
            }
        )

    payload = {
        "round": "R270",
        "captured_at": datetime.now(UTC).isoformat(),
        "initial_cache": INITIAL.relative_to(ROOT).as_posix(),
        "final_cache": FINAL.relative_to(ROOT).as_posix(),
        "output_file_counts": {
            "curves": sum(row["kind"] == "curve" for row in output_comparisons),
            "trades": sum(row["kind"] == "trades" for row in output_comparisons),
        },
        "all_curve_and_trade_bytes_identical": all_outputs_match,
        "all_manifests_differ_only_by_runner_sha256": manifests_ok,
        "outputs": output_comparisons,
        "manifests": manifest_comparisons,
        "interpretation": {
            "first_run_results_were_exposed_before_final_recalculation": True,
            "classification": "exploratory_reproducibility_audit_not_independent_confirmation",
            "reason": (
                "Byte-identical outputs show the rerun reproduced the same artifacts; "
                "prior exposure means the rerun was not blind and cannot restore independence."
            ),
        },
        "all_checks_pass": (
            all_outputs_match
            and manifests_ok
            and len(output_comparisons) == 18
            and len(manifest_comparisons) == 9
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT.with_suffix(OUT.suffix + ".sha256").write_text(
        digest + "  " + OUT.name + "\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "audit": OUT.relative_to(ROOT).as_posix(),
                "curve_files": payload["output_file_counts"]["curves"],
                "trade_files": payload["output_file_counts"]["trades"],
                "identical_outputs": all_outputs_match,
                "manifest_differences_only_runner_hash": manifests_ok,
                "classification": payload["interpretation"]["classification"],
                "all_checks_pass": payload["all_checks_pass"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
