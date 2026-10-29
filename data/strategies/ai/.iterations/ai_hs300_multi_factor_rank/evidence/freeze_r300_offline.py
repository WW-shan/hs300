from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
BASE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank"
ROUND = "R300"


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def main() -> int:
    feasibility_path = EVIDENCE / "r300_feasibility.json"
    feasibility_sidecar = feasibility_path.with_name(feasibility_path.name + ".sha256")
    if sha_file(feasibility_path) != feasibility_sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R300 feasibility proof checksum mismatch")
    feasibility = json.loads(feasibility_path.read_text(encoding="utf-8"))
    if feasibility.get("round") != ROUND or feasibility.get("parent_round") != "R299":
        raise RuntimeError("unexpected R300 feasibility parent")

    prereg = EVIDENCE / "r300_preregistration.json"
    manifest = EVIDENCE / "r300_input_manifest.jsonl"
    outputs = [prereg, manifest]
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in outputs):
        raise FileExistsError("R300 offline preregistration/manifest already exists; refusing overwrite")

    parent_result = EVIDENCE / "r299_result.json"
    parent_report = EVIDENCE / "r299_result_v2.md"
    parent_index = EVIDENCE / "r299_round_index_v2.md"
    for path in (parent_result, parent_report, parent_index):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha_file(path) != sidecar:
            raise RuntimeError(f"R299 parent checksum mismatch: {path.name}")

    parent = json.loads(parent_result.read_text(encoding="utf-8"))
    expected_segments = [
        ("2023-05-04", "2024-12-31"),
        ("2025-01-02", "2026-10-16"),
    ]
    segments = parent["execution"]["runs"]["candidate_3x"]["segments"]
    if [(item["start"], item["end"]) for item in segments] != expected_segments:
        raise RuntimeError("R299 frozen segment boundaries do not match R300 protocol")

    input_paths = [
        parent_result, EVIDENCE / "r299_result.json.sha256",
        parent_report, EVIDENCE / "r299_result_v2.md.sha256",
        parent_index, EVIDENCE / "r299_round_index_v2.md.sha256",
        feasibility_path, feasibility_sidecar,
        ROOT / "scripts/backtest_r239_concentration_audit.py",
        ROOT / "backend/app/services/industry_map.py",
        ROOT / "data/ext_data/ext_hy_ths/part.parquet",
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_preregistration.json",
    ]
    for item in feasibility["fixed_segments"]:
        cache_path = ROOT / item["source_trade_cache_path"]
        if sha_file(cache_path) != item["source_trade_cache_sha256"]:
            raise RuntimeError(f"R299 source trade cache hash changed: {cache_path.name}")
        input_paths.append(cache_path)

    records = []
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(f"R300 frozen input missing: {path}")
        stat = path.stat()
        records.append({"path": path.relative_to(ROOT).as_posix(), "bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns, "sha256": sha_file(path)})
    if len({item["path"] for item in records}) != len(records):
        raise RuntimeError("duplicate R300 frozen input paths")

    preregistration = {
        "round": ROUND,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "post_hoc_R299_trade_subsegment_concentration_diagnostic_not_alpha_confirmation",
        "selection_note": "The R257/R299 strategy, result and some exploratory segment/year attribution were already inspected before this protocol was written. This is an explicitly post-hoc descriptive audit, not blind or independent validation and not evidence of a new alpha.",
        "question": "Within the two already-frozen R299 discovery and validation segments, does the original stock-level net-P&L concentration failure persist in both segments?",
        "parent_round": "R299",
        "parent_candidate_id": "2d19f0651b3948bab8cd30c85e7c4e1a",
        "parent_result_sha256": sha_file(parent_result),
        "time_segments": [{"start": a, "end": b} for a, b in expected_segments],
        "source": "R299's existing corrected-cost StrategyBacktestService trade records only; no new backtest",
        "metric_definition_source": "scripts/backtest_r239_concentration_audit.py::_aggregate and R257 preregistration C2-C4",
        "analysis": {
            "unit": "closed trade P&L grouped by symbol within each frozen segment",
            "primary_metrics": ["trade_count", "distinct_symbols", "total_net_pnl", "top1_net_share", "top3_net_share", "top5_net_share"],
            "secondary_metrics": ["top1_gross_profit_share", "top3_gross_profit_share", "top5_gross_profit_share"],
            "annual_slice_handling": "Optional descriptive entry-calendar-year trade attribution only; not annual portfolio returns. Original C2-C4 are evaluated only for a positive net-P&L slice.",
            "no_parameter_search": True,
            "no_threshold_changes": True,
        },
        "decision_rules": {
            "C2": "top1 positive symbol net P&L / segment total net P&L <= 0.25, only if total net P&L > 0",
            "C3": "top3 positive symbol net P&L / segment total net P&L <= 0.55, only if total net P&L > 0",
            "C4": "top5 positive symbol net P&L / segment total net P&L <= 0.75, only if total net P&L > 0",
            "non_positive_net_pnl_slice": "C2-C4 not applicable; report the net P&L and do not substitute a denominator",
        },
        "panel_status": "not registered in this execution because the user's instruction explicitly prohibited panel API registration or viewing; no further panel requests are made",
        "panel_preflight_note": "The preserved R300 feasibility artifact includes one accidental read-only GET from the preceding failed attempt. It is retained unmodified; this freeze and subsequent analysis do not call the panel API.",
        "industry_snapshot": feasibility["industry_map_snapshot"],
        "feasibility_path": "evidence/r300_feasibility.json",
        "feasibility_sha256": sha_file(feasibility_path),
        "input_manifest_path": "evidence/r300_input_manifest.jsonl",
        "input_count": len(records),
        "side_effect_boundary": "Only R300 evidence is written. No panel API, provider, builder, market/factor/strategy data write, backtest, paper account, or order flow.",
    }
    prereg_bytes = (json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    prereg_hash = write_once(prereg, prereg_bytes)

    manifest_bytes = b"".join((json.dumps(item, sort_keys=True) + "\n").encode("utf-8") for item in records)
    manifest_hash = write_once(manifest, manifest_bytes)
    preregistration["input_manifest_sha256"] = manifest_hash
    preregistration["input_manifest_file_count"] = len(records)
    prereg_updated = (json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    prereg.write_bytes(prereg_updated)
    prereg.with_name(prereg.name + ".sha256").write_text(f"{hashlib.sha256(prereg_updated).hexdigest()}  {prereg.name}\n", encoding="ascii")

    print(json.dumps({"round": ROUND, "classification": preregistration["classification"],
                      "input_manifest_sha256": manifest_hash, "input_count": len(records),
                      "preregistration_sha256": hashlib.sha256(prereg_updated).hexdigest(),
                      "panel_api_calls_in_this_script": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
