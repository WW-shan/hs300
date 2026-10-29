#!/usr/bin/env python3
"""Record formal factor-tree identities before the R296 isolated computation."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r296_formal_factor_baseline.json"
CONFIG_IDS = ("alt_hs300", "alt_hs300_minrank", "alt_hs300_qrs")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    prereg = json.loads((EVIDENCE / "r296_preregistration.json").read_text(encoding="utf-8"))
    if prereg.get("round") != "R296" or prereg.get("status") != "frozen_before_compute":
        raise RuntimeError("R296 preregistration is not frozen")
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R296 formal-tree baseline exists; refusing overwrite")

    trees = []
    for config_id in CONFIG_IDS:
        root = ROOT / "data" / "ext_data" / config_id / "timeseries"
        partitions = []
        for path in sorted(root.glob("date=*/part.parquet")):
            stat = path.stat()
            partitions.append({
                "path": path.relative_to(ROOT).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest(path),
            })
        if not partitions:
            raise RuntimeError(f"No formal partitions found for {config_id}")
        trees.append({
            "config_id": config_id,
            "partition_count": len(partitions),
            "first_date": partitions[0]["path"].split("date=", 1)[1].split("/", 1)[0],
            "last_date": partitions[-1]["path"].split("date=", 1)[1].split("/", 1)[0],
            "partitions": partitions,
        })

    result = {
        "round": "R296",
        "classification": "pre_compute_scope_amendment_formal_factor_tree_baseline",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": hashlib.sha256(
            (EVIDENCE / "r296_preregistration.json").read_bytes()
        ).hexdigest(),
        "reason": "Capture all formal target-factor partition identities immediately before computation so non-mutation is directly checkable; original preregistration remains unchanged.",
        "factor_trees": trees,
        "limitations": [
            "Post-registration, pre-computation supplemental baseline; not represented as part of the original freeze.",
            "Covers the three target factor timeseries trees; other runtime data are protected by the declared isolated write roots and frozen input manifest.",
        ],
    }
    payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    value = write_once(OUTPUT, payload)
    print(json.dumps({
        "round": "R296",
        "classification": result["classification"],
        "tree_count": len(trees),
        "partition_count": sum(tree["partition_count"] for tree in trees),
        "baseline_sha256": value,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
