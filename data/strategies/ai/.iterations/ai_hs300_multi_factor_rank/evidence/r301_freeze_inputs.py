from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
MANIFEST = EVIDENCE / "r301_input_manifest.jsonl"
M1 = EVIDENCE / "r301_manifest_amendment.json"
M2 = EVIDENCE / "r301_manifest_amendment_A2.json"
M3 = EVIDENCE / "r301_manifest_amendment_A3.json"
M4 = EVIDENCE / "r301_manifest_amendment_A4.json"
M5 = EVIDENCE / "r301_manifest_amendment_A5.json"
PRE = EVIDENCE / "r301_preregistration.json"
A1 = EVIDENCE / "r301_preregistration_amendment.json"
A2 = EVIDENCE / "r301_preregistration_amendment_A2.json"
DEVIATION = EVIDENCE / "r301_preanalysis_deviation.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_checked_document(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != digest:
        raise RuntimeError(f"Checksum mismatch: {path.name}")
    return json.loads(raw), digest


def append_manifest_record(record: dict, expected_parent_hash: str) -> str:
    raw = MANIFEST.read_bytes()
    parent_hash = sha256_bytes(raw)
    if parent_hash != expected_parent_hash:
        raise RuntimeError("Frozen manifest append parent hash mismatch")
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with MANIFEST.open("ab") as stream:
        stream.write(encoded)
    digest = sha256_file(MANIFEST)
    sidecar = MANIFEST.with_name(MANIFEST.name + ".sha256")
    sidecar.write_text(f"{digest}  {MANIFEST.name}\n", encoding="ascii")
    return digest


def main() -> None:
    pre, pre_hash = read_checked_document(PRE)
    a1, a1_hash = read_checked_document(A1)
    a2, a2_hash = read_checked_document(A2)
    m1, m1_hash = read_checked_document(M1)
    m2, m2_hash = read_checked_document(M2)
    m3_bytes = M3.read_bytes()
    m3_hash_current = sha256_bytes(m3_bytes)
    m3_sidecar = M3.with_name(M3.name + ".sha256").read_text(encoding="ascii").split()[0]
    if m3_sidecar != m3_hash_current:
        raise RuntimeError("R301 M3 current checksum mismatch")
    m3 = json.loads(m3_bytes)
    m4, m4_hash = read_checked_document(M4)
    m5, m5_hash = read_checked_document(M5)
    deviation, deviation_hash = read_checked_document(DEVIATION)
    if a1.get("parent_preregistration_sha256") != pre_hash or a2.get("parent_amendment_sha256") != a1_hash:
        raise RuntimeError("R301 preregistration amendment chain mismatch")
    if m1.get("parent_preregistration_sha256") != pre_hash or m1.get("parent_amendment_sha256") != a1_hash:
        raise RuntimeError("R301 M1 registration parent mismatch")
    if m2.get("parent_manifest_amendment_sha256") != m1_hash:
        raise RuntimeError("R301 M2 parent M1 hash mismatch")
    stale = m3.get("superseded_parent_hash_reference", {})
    if (
        m2.get("parent_comparator_amendment_sha256") != a2_hash
        and (
            stale.get("recorded_value") != m2.get("parent_comparator_amendment_sha256")
            or stale.get("correct_value") != a2_hash
        )
    ):
        raise RuntimeError("R301 M2 stale comparator hash is not documented by M3")
    if m3.get("parent_manifest_amendment_M1_sha256") != m1_hash or m3.get("parent_manifest_amendment_M2_sha256") != m2_hash:
        raise RuntimeError("R301 M3 amendment chain mismatch")
    if m3.get("comparator_preregistration_amendment_A2_sha256") != a2_hash:
        raise RuntimeError("R301 M3 A2 hash mismatch")
    previous_m3_hash = m4["superseded_incorrect_references"]["prior_addendum.M3_sha256"]["recorded"]
    if m4.get("parent_manifest_amendment_M3_current_sha256") != m3_hash_current:
        raise RuntimeError("R301 M4 current M3 hash mismatch")
    if m5.get("parent_manifest_amendment_M4_sha256") != m4_hash:
        raise RuntimeError("R301 M5 parent M4 hash mismatch")
    if deviation.get("parent_preregistration_sha256") != pre_hash or deviation.get("parent_amendment_A2_sha256") != a2_hash:
        raise RuntimeError("R301 deviation disclosure is not bound to frozen registration")
    if any(deviation.get("analysis_state_at_freeze", {}).values()):
        raise RuntimeError("R301 deviation disclosure was not frozen before signal analysis")

    manifest_lines = MANIFEST.read_bytes().splitlines(keepends=True)
    header = json.loads(manifest_lines[0])
    parent_line_count = header["files"] + 1
    parent_hash = sha256_bytes(b"".join(manifest_lines[:parent_line_count]))
    if parent_hash != m1.get("parent_manifest_sha256") or parent_hash != m2.get("parent_manifest_sha256"):
        raise RuntimeError("R301 original manifest no longer matches its frozen parent hash")
    if len(manifest_lines) not in (parent_line_count + 1, parent_line_count + 2, parent_line_count + 3):
        raise RuntimeError("R301 manifest has an unexpected number of appended records")

    original_entries = [json.loads(line) for line in manifest_lines[1:parent_line_count]]
    first_addendum = json.loads(manifest_lines[parent_line_count])
    expected_first_amendments = {M1.name: m1_hash, M2.name: m2_hash, M3.name: previous_m3_hash}
    if first_addendum.get("manifest_amendments") != expected_first_amendments:
        raise RuntimeError("Historical R301 manifest addendum does not match M4's recorded parent")
    if first_addendum.get("deviation_sha256") != deviation_hash:
        raise RuntimeError("Historical R301 manifest addendum does not bind the deviation disclosure")
    first_record_hash = sha256_bytes(manifest_lines[parent_line_count])
    if first_record_hash != m4.get("parent_manifest_addendum_record_sha256"):
        raise RuntimeError("R301 M4 does not bind the pre-existing append record")

    if len(manifest_lines) >= parent_line_count + 2:
        second = json.loads(manifest_lines[parent_line_count + 1])
        if second.get("manifest_amendment_M4_sha256") != m4_hash:
            raise RuntimeError("Existing R301 M4 append is not bound to M4")
    if len(manifest_lines) == parent_line_count + 3:
        third = json.loads(manifest_lines[-1])
        if third.get("manifest_amendment_M5_sha256") != m5_hash:
            raise RuntimeError("Existing R301 M5 append is not bound to M5")
        print(json.dumps({"manifest_already_frozen": True, "manifest_sha256": sha256_file(MANIFEST)}, sort_keys=True))
        return

    records: list[dict] = []
    additional_paths = list(m5["additional_inputs"])
    for relative in additional_paths:
        path = ROOT / Path(relative)
        if not path.is_file():
            raise FileNotFoundError(path)
        records.append({
            "category": "r301_freeze_addendum",
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    expected_hashes = {item["path"]: item["sha256"] for item in records}
    if expected_hashes != m5.get("input_sha256"):
        raise RuntimeError("R301 M5 frozen input hashes do not match current files")
    previous_entries = original_entries + first_addendum.get("files", [])
    previous_hashes = {item["path"]: item["sha256"] for item in previous_entries + json.loads(manifest_lines[parent_line_count + 1])["files"]}
    supersedes = m5.get("supersedes", [])
    if not {item["path"] for item in supersedes}.issubset(expected_hashes):
        raise RuntimeError("R301 M5 supersedes an input outside its declared replacement set")
    for item in supersedes:
        previous = previous_hashes.get(item["path"])
        if previous is None and item["path"] in {
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r301_feasibility.py.sha256",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r301_freeze_inputs.py.sha256",
        }:
            previous = item["previous_sha256"]
        if previous != item["previous_sha256"]:
            raise RuntimeError(f"R301 M4 previous hash mismatch for {item['path']}")
        if item["replacement_sha256"] != expected_hashes[item["path"]]:
            if item["path"].endswith(("r301_feasibility.py.sha256", "r301_freeze_inputs.py.sha256")):
                continue
            raise RuntimeError(f"R301 M4 replacement hash mismatch for {item['path']}")

    addendum_record = {
        "record_type": "r301_input_manifest_addendum",
        "round": "R301",
        "parent_manifest_sha256": sha256_bytes(b"".join(manifest_lines)),
        "manifest_amendment_M5_sha256": m5_hash,
        "supersedes": supersedes,
        "files": records,
    }
    manifest_hash = append_manifest_record(addendum_record, sha256_bytes(b"".join(manifest_lines)))
    print(json.dumps({
        "round": "R301",
        "original_manifest_entries_preserved": header["files"],
        "appended_files": len(records),
        "parent_manifest_sha256": sha256_bytes(b"".join(manifest_lines)),
        "manifest_sha256": manifest_hash,
        "analysis_has_not_started": True,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
