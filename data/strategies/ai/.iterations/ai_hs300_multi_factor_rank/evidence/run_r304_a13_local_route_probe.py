from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A12.json"
BUNDLE = EVIDENCE / "r304_official_frontend_bundle.js"
OUTPUT = EVIDENCE / "r304_A13_local_route_probe.json"
EXPECTED_BUNDLE_SHA256 = "232cafcfdb4da07c7b2ce24efae30318b59a8168edd023566364579445f2d998"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(f"{sha(raw)}  {path.name}\n", encoding="ascii")


def main() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    bundle_raw = BUNDLE.read_bytes()
    bundle_sha = sha(bundle_raw)
    scope = amendment.get("next_discovery_scope", {})
    if amendment.get("amendment_id") != "R304-A12" or scope.get("bundle_sha256") != EXPECTED_BUNDLE_SHA256 or bundle_sha != EXPECTED_BUNDLE_SHA256:
        raise RuntimeError("A13 frozen local bundle identity check failed")
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("A13 output exists; refusing overwrite")

    text = bundle_raw.decode("utf-8", errors="replace")
    route_matches = sorted(set(re.findall(r"/indexInfo/(?:index-sample-information|sample-information)", text)))
    chunk = re.search(r"path:\s*\"/dataService/indexConstituent\".{0,350}?chunk-([a-f0-9]+)\"\)\.then\(n\.bind\(null,\"([a-f0-9]+)\"\)", text)
    request_occurrences = {route: text.count(route) for route in route_matches}
    probe = {
        "round": "R304-A13",
        "classification": "local_static_bundle_inspection_no_network",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "input_file": BUNDLE.name,
        "input_sha256": bundle_sha,
        "route_matches": route_matches,
        "route_occurrences": request_occurrences,
        "lazy_chunk_id": chunk.group(1) if chunk else None,
        "lazy_module_id": chunk.group(2) if chunk else None,
        "chunk_file_map_present": bool(chunk),
        "request_method_and_parameter_contract_found": False,
        "date_effective_sample_contract_found": False,
        "network_requests": 0,
        "conclusion": "The saved main bundle reveals route names and a lazy-loaded page chunk, but not the chunk code or its request parameters. No undocumented API request is justified yet; retrieve only the exact static chunk asset if its identity can be verified from the saved bundle, then inspect its declared request contract locally.",
    }
    raw = (json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_once(OUTPUT, raw)
    print(json.dumps({key: probe[key] for key in ("input_sha256", "route_matches", "route_occurrences", "lazy_chunk_id", "lazy_module_id", "network_requests")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
