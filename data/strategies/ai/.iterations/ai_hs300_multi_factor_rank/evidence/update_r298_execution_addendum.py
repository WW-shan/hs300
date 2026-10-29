from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
ADDENDUM = EVIDENCE / "r298_manifest_execution_addendum.json"
MANIFEST = EVIDENCE / "r298_input_manifest.jsonl"
SCRIPTS = [
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/freeze_r298.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r298_temporal_holdout.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/register_r298_panel_once.py",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest_sha = digest(MANIFEST)
    data = json.loads(ADDENDUM.read_text(encoding="utf-8"))
    if data.get("parent_manifest_sha256") != manifest_sha:
        raise RuntimeError("parent manifest hash mismatch")
    previous = {item["path"]: item for item in data.get("execution_script_changes", [])}
    rows = []
    manifest_rows = {
        json.loads(line)["path"]: json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    for relative in SCRIPTS:
        path = ROOT / relative
        stat = path.stat()
        item = dict(previous.get(relative, {}))
        frozen = manifest_rows.get(relative)
        if frozen is None:
            raise RuntimeError(f"execution script is absent from frozen manifest: {relative}")
        item.update(
            {
                "path": relative,
                "original_sha256": frozen["sha256"],
                "original_bytes": frozen["bytes"],
                "original_mtime_ns": frozen["mtime_ns"],
                "current_sha256": digest(path),
                "current_bytes": stat.st_size,
                "current_mtime_ns": stat.st_mtime_ns,
                "change_class": "execution_harness_integrity_only",
            }
        )
        rows.append(item)
    data["execution_script_changes"] = rows
    data["integrity_revision_at_utc"] = datetime.now(timezone.utc).isoformat()
    data["integrity_revision_scope"] = (
        "Only R298 evidence/control scripts changed after freeze. Factor, strategy, "
        "preregistration, inputs and decision rules remain unchanged; the original "
        "source manifest is preserved. The first runner attempt stopped before engine "
        "construction and before writing a research cache."
    )
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + bytes([10])
    ADDENDUM.write_bytes(encoded)
    checksum = hashlib.sha256(encoded).hexdigest()
    ADDENDUM.with_name(ADDENDUM.name + ".sha256").write_text(
        f"{checksum}  {ADDENDUM.name}\n", encoding="ascii"
    )
    print(json.dumps({"addendum_sha256": checksum, "scripts": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
