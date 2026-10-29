#!/usr/bin/env python3
"""Add a transparent correction and capture first/last factor partitions."""
from __future__ import annotations
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r294_preregistration.json"
SNAPSHOT = EVIDENCE / "r294_input_snapshot.json"
OUTPUT = EVIDENCE / "r294_partition_inventory_erratum.json"
CONFIG_IDS = ("alt_hs300", "alt_hs300_minrank", "alt_hs300_qrs")
def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest
def main() -> int:
    if OUTPUT.exists() or Path(str(OUTPUT) + ".sha256").exists():
        raise RuntimeError("R294 inventory erratum exists; refusing to overwrite")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if prereg.get("round") != "R294" or snapshot.get("round") != "R294":
        raise RuntimeError("R294 identity mismatch")
    frozen = {row["path"]: row["sha256"] for row in snapshot["source_files"]}
    for relative in ("scripts/build_alt_factor_data.py", "scripts/build_r253_min_rank_factor.py", "scripts/build_r257_quality_relative_strength_factor.py"):
        if sha((ROOT / relative).read_bytes()) != frozen.get(relative):
            raise RuntimeError(f"Frozen builder source changed: {relative}")
    trees = []
    for config_id in CONFIG_IDS:
        root = ROOT / "data" / "ext_data" / config_id / "timeseries"
        paths = sorted(root.glob("date=*/part.parquet"))
        if len(paths) < 2:
            raise RuntimeError(f"Expected at least two partitions for {config_id}")
        trees.append({
            "config_id": config_id,
            "partition_count_at_capture": len(paths),
            "first_date": paths[0].parent.name[5:],
            "last_date": paths[-1].parent.name[5:],
            "sampled_partitions": [
                {"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
                for path in (paths[0], paths[-1])
            ],
        })
    payload = {
        "round": "R294",
        "classification": "post_freeze_method_execution_erratum",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": sha(PREREG.read_bytes()),
        "original_input_snapshot_sha256": sha(SNAPSHOT.read_bytes()),
        "correction": "The preregistration specified two partition hashes per factor tree. The original selector matched only 2026-10-19 and 2026-10-27; all factor trees ended on 2026-10-19, so it captured one file per tree. Original evidence is preserved. This post-freeze erratum separately captures each tree's first and last partition hashes.",
        "partition_trees": trees,
        "limitations": [
            "Supplemental inventory captured after preregistration and publication probe.",
            "Verifies file identity only; does not read parquet values, rebuild factors, or test live publication.",
            "Does not establish point-in-time validity, predictive value, or alpha.",
        ],
    }
    digest = write_once(OUTPUT, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"round": "R294", "factor_trees": len(trees), "sampled_partition_files": sum(len(tree["sampled_partitions"]) for tree in trees), "erratum_sha256": digest}, ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
