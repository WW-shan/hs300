from __future__ import annotations

import hashlib
import json
import os
import platform
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018"
OUTPUTS = (
    EVIDENCE / "r303_input_manifest.jsonl",
    EVIDENCE / "r303_panel_baseline.json",
    EVIDENCE / "r303_current_members_readback.json",
    EVIDENCE / "r303_mining_availability.json",
    EVIDENCE / "r303_feasibility_audit.json",
)
CODE_PATHS = (
    "backend/app/api/mining.py",
    "backend/app/backtest/mining_runtime.py",
    "backend/app/backtest/mining.py",
    "backend/app/backtest/factor.py",
    "backend/app/backtest/strategy.py",
    "backend/app/backtest/engine.py",
    "backend/app/hs300/current.py",
    "backend/app/api/hs300.py",
    "backend/app/services/mining_preflight.py",
    "backend/app/services/mining_jobs.py",
    "backend/app/services/mining_candidates.py",
    "docs/mining.md",
    "backend/tests/test_mining_api.py",
    "backend/tests/backtest/test_mining_runtime.py",
    "backend/tests/backtest/test_mining.py",
    "backend/tests/test_hs300_backtest_membership.py",
    "backend/tests/test_hs300_current.py",
    "backend/app/backtest/candidates.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r303_preregistration.json",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r303_freeze_audit.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r303_register_panel_once.py",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    digest = sha(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.status, response.read()


def file_record(path: Path, label: str | None = None) -> dict:
    raw = path.read_bytes()
    stat = path.stat()
    return {
        "path": label or path.relative_to(ROOT).as_posix(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha(raw),
    }


def main() -> None:
    for output in OUTPUTS:
        if output.exists() or output.with_name(output.name + ".sha256").exists():
            raise RuntimeError(f"R303 freeze evidence exists; refusing overwrite: {output.name}")

    source_records = []
    for relative in CODE_PATHS:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        source_records.append(file_record(path, relative))

    enriched_root = ROOT / "data/kline_daily_enriched"
    partition_files = sorted(enriched_root.glob("date=*/part.parquet"))
    partition_records = [file_record(path) for path in partition_files]
    hs300_files = sorted(path for path in (ROOT / "data/hs300").rglob("*") if path.is_file())
    membership_records = [file_record(path) for path in hs300_files]
    extra_data = [
        ROOT / "data/instruments/instruments.parquet",
        ROOT / "data/ext_data/ext_hy_ths/config.json",
        ROOT / "data/ext_data/ext_hy_ths/part.parquet",
    ]
    for path in extra_data:
        if path.is_file():
            source_records.append(file_record(path))

    panel_status, panel_raw = request("/api/backtest/candidates")
    current_status, current_raw = request("/api/hs300/current")
    availability_status, availability_raw = request(
        "/api/backtest/mining/availability?asset_type=stock&budget_profile=balanced"
    )
    if (panel_status, current_status, availability_status) != (200, 200, 200):
        raise RuntimeError(
            f"read-only preflight failed: candidates={panel_status}, current={current_status}, availability={availability_status}"
        )

    panel = json.loads(panel_raw)
    items = panel.get("items", [])
    if not isinstance(items, list):
        raise ValueError("candidate panel response has no items list")
    current = json.loads(current_raw)
    availability = json.loads(availability_raw)
    if len(current.get("members", [])) != 300:
        raise ValueError("official current HS300 response is not exactly 300 members")
    if not availability.get("eligible"):
        raise ValueError("balanced mining availability is false; unexpected preflight state")
    baseline = {str(item["id"]): str(item.get("status")) for item in items}
    if len(baseline) != len(items):
        raise ValueError("candidate panel contains duplicate IDs")

    samples = {}
    for label in ("2018-01-02", "2020-01-02", "2023-01-03", "2026-10-27"):
        path = enriched_root / f"date={label}" / "part.parquet"
        if path.is_file():
            frame = pl.read_parquet(path, columns=["symbol", "date", "close"])
            samples[label] = {
                "rows": frame.height,
                "unique_symbols": frame.get_column("symbol").n_unique(),
                "date_min": str(frame.get_column("date").min()),
                "date_max": str(frame.get_column("date").max()),
            }

    input_records = source_records + partition_records + membership_records
    manifest_raw = b"".join(
        (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for record in input_records
    )
    prereg_hash = sha((EVIDENCE / "r303_preregistration.json").read_bytes())
    audit = {
        "round": "R303",
        "classification": "read-only_nested_mining_universe_feasibility",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_hash,
        "freeze_script_sha256": sha(Path(__file__).read_bytes()),
        "registration_script_sha256": sha((EVIDENCE / "r303_register_panel_once.py").read_bytes()),
        "input_manifest_entries": len(input_records),
        "input_manifest_sha256": sha(manifest_raw),
        "code_input_count": len(source_records),
        "enriched_partition_count": len(partition_records),
        "enriched_partition_bytes": sum(row["size_bytes"] for row in partition_records),
        "membership_files": membership_records,
        "membership_file_count": len(membership_records),
        "membership_files_are_current_only": [row["path"].endswith("current_members.json") for row in membership_records],
        "enriched_sample_symbol_counts": samples,
        "official_current_membership": {
            "http_status": current_status,
            "response_sha256": sha(current_raw),
            "as_of": current.get("as_of"),
            "source": current.get("source"),
            "count": len(current.get("members", [])),
        },
        "mining_availability": {
            "http_status": availability_status,
            "response_sha256": sha(availability_raw),
            "body": availability,
        },
        "panel_baseline": {
            "http_status": panel_status,
            "response_sha256": sha(panel_raw),
            "candidate_count": len(items),
            "ids_and_statuses": baseline,
        },
        "environment": {
            "python": platform.python_version(),
            "polars": pl.__version__,
            "os": platform.platform(),
            "process_id": os.getpid(),
        },
        "execution_boundary": "No mining job, factor return calculation, strategy backtest, provider, or write API was called.",
    }

    write_once(OUTPUTS[0], manifest_raw)
    write_once(OUTPUTS[1], (json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    write_once(OUTPUTS[2], (json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    write_once(OUTPUTS[3], (json.dumps(availability, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    write_once(OUTPUTS[4], (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({key: audit[key] for key in (
        "input_manifest_entries", "input_manifest_sha256", "enriched_partition_count",
        "membership_files", "official_current_membership", "mining_availability",
        "panel_baseline", "execution_boundary",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
