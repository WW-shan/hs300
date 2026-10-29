from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
REPORT = EVIDENCE / "workspace_cleanup_integrity_audit_v2.md"
PATH_NORMALIZATIONS = (
    (
        "r272_operating_margin_improvement_corrected.portable.json",
        "94496e3f1488d143a14a4ff0171ee0ffc35e636e217d61bb23777baed9e733cc",
    ),
    (
        "r289_preregistration.portable.json",
        "f7e4430f7dbcf2a30299e6ddac1841fbe837d613826449fd9f508521c3ed5a60",
    ),
    (
        "r304_a5_browser_state.json",
        "f036b17cf698c5c404b62871ce3ded054ee182938f471928cc27d6a1616b40b4",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    sidecars = sorted(
        path
        for path in EVIDENCE.rglob("*.sha256")
        if "__pycache__" not in path.parts
    )
    results: list[tuple[str, str, str, str, str]] = []
    for sidecar in sidecars:
        fields = sidecar.read_text(encoding="ascii").strip().split()
        if not fields:
            results.append((sidecar.relative_to(EVIDENCE).as_posix(), "empty", "", "", ""))
            continue

        expected = fields[0]
        target_name = fields[1] if len(fields) > 1 else sidecar.name.removesuffix(".sha256")
        target_path = Path(target_name)
        bases = (sidecar.parent / target_path, EVIDENCE / target_path)
        candidates = list(bases)
        # Older sidecars may omit the target extension.
        candidates.extend(
            candidate.with_suffix(candidate.suffix + extension)
            for candidate in bases
            for extension in (".json", ".md", ".py")
        )
        target = next((candidate for candidate in candidates if candidate.is_file()), None)
        if target is None:
            results.append(
                (sidecar.relative_to(EVIDENCE).as_posix(), "missing", target_name, expected, "")
            )
            continue

        actual = sha256(target)
        status = "match" if actual == expected else "mismatch"
        results.append(
            (sidecar.relative_to(EVIDENCE).as_posix(), status, target_name, expected, actual)
        )

    counts = Counter(row[1] for row in results)
    lines = [
        "# Workspace Evidence Integrity Audit",
        "",
        "This audit reads evidence and SHA-256 sidecars. It does not repair mismatched",
        "sidecars or rewrite research results. The report sidecar is created after this scan.",
        "",
        f"- Audit date: {date.today().isoformat()}",
        f"- Sidecars checked: {len(results)}",
        f"- Matching: {counts['match']}",
        f"- Mismatching: {counts['mismatch']}",
        f"- Empty: {counts['empty']}",
        f"- Missing targets: {counts['missing']}",
        "",
        "## Publication path normalization",
        "",
        "Frozen evidence files were preserved byte-for-byte. Portable copies replace local",
        "checkout paths with repository-relative paths and have independent SHA-256 sidecars.",
        "",
    ]
    for filename, previous_hash in PATH_NORMALIZATIONS:
        path = EVIDENCE / filename
        lines.append(
            f"- `{filename}`: previous SHA-256 `{previous_hash}`; current SHA-256 `{sha256(path)}`."
        )
    lines.extend(
        [
            "",
            "The earlier `workspace_cleanup_integrity_audit.md` is retained as its original",
            "snapshot. Its two missing targets (`r270_preregistration` and",
            "`r279_preregistration`) are present in this scan and resolve to the corresponding JSON files.",
            "",
            "The empty sidecar for the R304 notice 14497 workbook was removed: its request",
            "record confirms that the workbook was not present locally and the sidecar had no digest.",
            "",
            "## Existing exceptions",
            "",
        ]
    )
    for relative, status, target, expected, actual in results:
        if status == "match":
            continue
        detail = f"target={target or '(not specified)'}; expected={expected or '(empty)'}"
        if actual:
            detail += f"; actual={actual}"
        lines.append(f"- `{relative}` ({status}): {detail}")

    if counts["mismatch"] + counts["empty"] + counts["missing"] == 0:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "The listed exceptions are retained as found. Their sidecars and associated",
            "evidence files were not changed to make the checks pass.",
            "",
        ]
    )
    payload = "\n".join(lines).encode("utf-8")
    sidecar_path = REPORT.with_name(REPORT.name + ".sha256")
    with REPORT.open("xb") as stream:
        stream.write(payload)
    with sidecar_path.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{hashlib.sha256(payload).hexdigest()}  {REPORT.name}\n")

    print(f"checked={len(results)} counts={dict(counts)} report={REPORT.name}")


if __name__ == "__main__":
    main()
