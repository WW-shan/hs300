#!/usr/bin/env python3
"""Build a reproducible research manifest over the registered factor catalog."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG = DATA / "factor_research" / "catalog" / "latest.json"
SNAPSHOTS = DATA / "factor_research" / "catalog" / "snapshots"
IC_SCAN = DATA / "factor_research" / "research" / "alt_hs300_rank_ic_5d_2026-11-10_v2.json"
R391 = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r391_factor_library_statistical.json"
ABLATION = DATA / "factor_research" / "research" / "multi_dimension_v1_ablation_2026-11-10.json"
PARAMETERS = DATA / "factor_research" / "parameters" / "families.json"
OUTPUT = DATA / "factor_research" / "catalog" / "library_manifest_2026-11-22.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError(f"manifest output exists: {OUTPUT.name}")
    latest = load(CATALOG)
    snapshot_id = latest["snapshot_id"]
    snapshot = load(SNAPSHOTS / f"{snapshot_id.removeprefix('sha256:')}.json")
    factors = snapshot["factors"]
    ic = load(IC_SCAN)
    r391 = load(R391)
    ablation = load(ABLATION)
    parameter_registry = load(PARAMETERS)
    family_by_factor: dict[str, dict[str, Any]] = {}
    for family in parameter_registry.get("families", []):
        for factor_id in family.get("member_factor_ids", []):
            family_by_factor[factor_id] = {
                "family_id": family["family_id"],
                "parameter": family.get("current_parameter"),
                "status": family.get("status"),
                "parameter_grid": family.get("parameter_grid"),
            }
    ic_results = ic.get("results", {})
    r391_rows = r391.get("candidates", {})
    effects = ablation.get("factor_effects", {})
    records = []
    for factor in factors:
        factor_id = factor["factor_id"]
        variant = ic_results.get(factor_id, {}).get("variants", {}).get("industry_size_neutral") or ic_results.get(factor_id, {}).get("variants", {}).get("raw")
        recent = variant.get("recent", {}) if variant else {}
        full = variant.get("full", {}) if variant else {}
        r391_row = r391_rows.get(factor_id, {})
        effect = effects.get("full", {}).get(factor_id, {})
        records.append({
            "factor_id": factor_id,
            "label": factor.get("label"),
            "data_class": factor.get("data_class"),
            "group": factor.get("group"),
            "kind": factor.get("kind"),
            "direction": factor.get("direction"),
            "cross_section_type": factor.get("cross_section_type"),
            "pit_status": factor.get("pit_status"),
            "future_data_status": factor.get("future_data_status"),
            "scale_free": factor.get("scale_free"),
            "warmup_bars": factor.get("warmup_bars"),
            "definition_fingerprint": factor.get("definition_fingerprint"),
            "research": {
                "ic_5d_full": full.get("directional_ic_mean"),
                "ic_5d_recent": recent.get("directional_ic_mean"),
                "ic_q_value": variant.get("q_value") if variant else None,
                "ic_yearly_sign_consistency": variant.get("yearly_sign_consistency") if variant else None,
                "incremental_status": r391_row.get("status"),
                "incremental_ic": r391_row.get("directional_delta_mean"),
                "incremental_q": r391_row.get("q_value"),
                "max_core_correlation": r391_row.get("mean_abs_correlation"),
                "drop_one_contribution": effect.get("marginal_contribution"),
                "single_factor_total_return": effect.get("single_total_return"),
            },
            "parameter_family": family_by_factor.get(factor_id),
            "library_state": "research_evidence" if r391_row or effect else "catalog_only",
        })
    records.sort(key=lambda row: (row["data_class"] or "", row["factor_id"]))
    counts = {
        "total": len(records),
        "with_research_evidence": sum(row["library_state"] == "research_evidence" for row in records),
        "incremental_pass": sum(row["research"]["incremental_status"] == "statistical_pass" for row in records),
    }
    payload = {
        "schema_version": 1, "generated_at": "2026-11-22", "catalog_snapshot_id": snapshot_id,
        "source_hashes": {"catalog": sha(CATALOG), "ic_scan": sha(IC_SCAN), "r391": sha(R391), "ablation": sha(ABLATION), "parameters": sha(PARAMETERS)},
        "counts": counts, "records": records,
        "notes": ["The manifest is a research index, not an active factor set.", "Missing IC/incremental evidence is explicit as null, not zero.", "Direction remains the catalog-defined economic direction."],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
