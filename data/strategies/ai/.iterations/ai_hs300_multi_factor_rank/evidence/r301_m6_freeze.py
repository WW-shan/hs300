from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
MANIFEST = EVIDENCE / "r301_input_manifest.jsonl"
AMENDMENT = EVIDENCE / "r301_manifest_amendment_A6.json"
FAILURE = EVIDENCE / "r301_attempt_1_failure.json"
AUDIT = EVIDENCE / "r301_feasibility.py"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    amendment_bytes = AMENDMENT.read_bytes()
    amendment_hash = sha256_bytes(amendment_bytes)
    amendment_sidecar = AMENDMENT.with_name(AMENDMENT.name + ".sha256")
    if amendment_sidecar.read_text(encoding="ascii").split()[0] != amendment_hash:
        raise RuntimeError("R301 M6 amendment checksum mismatch")
    amendment = json.loads(amendment_bytes)

    manifest_bytes = MANIFEST.read_bytes()
    manifest_hash = sha256_bytes(manifest_bytes)
    manifest_sidecar = MANIFEST.with_name(MANIFEST.name + ".sha256")
    if manifest_sidecar.read_text(encoding="ascii").split()[0] != manifest_hash:
        raise RuntimeError("R301 parent manifest checksum mismatch")
    if manifest_hash != amendment["parent_manifest_sha256"]:
        raise RuntimeError("R301 M6 parent manifest hash mismatch")

    inputs = []
    for relative in amendment["additional_inputs"]:
        path = ROOT / Path(relative)
        if not path.is_file():
            raise FileNotFoundError(path)
        inputs.append({
            "category": "r301_m6_repair_evidence",
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    input_hashes = {item["path"]: item["sha256"] for item in inputs}
    if input_hashes != amendment["input_sha256"]:
        raise RuntimeError("R301 M6 input hashes changed after amendment freeze")
    amendment_sidecar_path = AMENDMENT.with_name(AMENDMENT.name + ".sha256")
    inputs.extend([
        {
            "category": "r301_m6_freeze_document",
            "path": str(AMENDMENT.relative_to(ROOT)).replace("\\", "/"),
            "size_bytes": AMENDMENT.stat().st_size,
            "sha256": amendment_hash,
        },
        {
            "category": "r301_m6_freeze_document_sidecar",
            "path": str(amendment_sidecar_path.relative_to(ROOT)).replace("\\", "/"),
            "size_bytes": amendment_sidecar_path.stat().st_size,
            "sha256": sha256_file(amendment_sidecar_path),
        },
    ])
    if amendment["parent_manifest_amendment_M5_sha256"] != hashlib.sha256(
        (EVIDENCE / "r301_manifest_amendment_A5.json").read_bytes()
    ).hexdigest():
        raise RuntimeError("R301 M6 parent M5 amendment hash mismatch")

    supersedes = amendment["supersedes"]
    if len(supersedes) != 2 or {item["path"] for item in supersedes} != {
        str(AUDIT.relative_to(ROOT)).replace("\\", "/"),
        str(AUDIT.with_name(AUDIT.name + ".sha256").relative_to(ROOT)).replace("\\", "/"),
    }:
        raise RuntimeError("R301 M6 must supersede only the audit source and its sidecar")

    record = {
        "record_type": "r301_input_manifest_addendum",
        "round": "R301",
        "parent_manifest_sha256": manifest_hash,
        "manifest_amendment_M6_sha256": amendment_hash,
        "supersedes": supersedes,
        "files": inputs,
    }
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with MANIFEST.open("ab") as stream:
        stream.write(encoded)
    new_hash = sha256_file(MANIFEST)
    manifest_sidecar.write_text(f"{new_hash}  {MANIFEST.name}\n", encoding="ascii")
    print(json.dumps({
        "round": "R301",
        "amendment": "M6",
        "parent_manifest_sha256": manifest_hash,
        "manifest_sha256": new_hash,
        "appended_inputs": len(inputs),
        "analysis_data_opened": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
