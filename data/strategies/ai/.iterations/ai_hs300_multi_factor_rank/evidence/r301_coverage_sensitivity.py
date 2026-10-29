from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(EVIDENCE))
import r301_feasibility as primary  # noqa: E402

AMENDMENT_PATH = EVIDENCE / "r301_coverage_sensitivity_amendment.json"
PRIMARY_RESULT_PATH = EVIDENCE / "r301_feasibility_result.json"
OUTPUT_PATH = EVIDENCE / "r301_coverage_sensitivity.json"
AGE_LIMITS = (90, 180, 270, 365, None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_hash(path: Path) -> str:
    digest = sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != digest:
        raise RuntimeError(f"R301 sensitivity checksum mismatch: {path.name}")
    return digest


def main() -> None:
    if OUTPUT_PATH.exists() or OUTPUT_PATH.with_name(OUTPUT_PATH.name + ".sha256").exists():
        raise RuntimeError("R301 coverage sensitivity output already exists; refusing overwrite")
    amendment_hash = checked_hash(AMENDMENT_PATH)
    script_hash = sha256_file(Path(__file__))
    amendment = json.loads(AMENDMENT_PATH.read_text(encoding="utf-8"))
    if amendment.get("analysis_script_sha256") != script_hash:
        raise RuntimeError("R301 sensitivity script differs from its post-result freeze")
    primary_result_hash = checked_hash(PRIMARY_RESULT_PATH)
    if amendment.get("parent_primary_result_sha256") != primary_result_hash:
        raise RuntimeError("R301 sensitivity amendment is not bound to the primary result")
    _, _, frozen_hashes = primary._read_frozen_inputs()
    if amendment.get("parent_input_manifest_sha256") != frozen_hashes["input_manifest_sha256"]:
        raise RuntimeError("R301 sensitivity amendment is not bound to the verified input manifest")

    candidate_periods, _, _ = primary._financial_period_signals()
    eligible, dates, _ = primary._membership_universe()
    joined = {}
    for age_limit in AGE_LIMITS:
        attached = primary._attach_asof(
            eligible,
            candidate_periods.rename({"_candidate_effective": "_candidate_available"}),
            effective_column="_candidate_available",
            value_column="collection_quality_spread",
            age_limit=age_limit,
        )
        values = attached.select(["date", "symbol", "collection_quality_spread"])
        label = str(age_limit) if age_limit is not None else "unlimited"
        joined[label] = values

    primary_result = json.loads(PRIMARY_RESULT_PATH.read_text(encoding="utf-8"))
    windows = {}
    for age_limit in AGE_LIMITS:
        label = str(age_limit) if age_limit is not None else "unlimited"
        coverage = primary._coverage_audit(joined[label], eligible, dates)
        row = {
            "age_limit_days": age_limit,
            "windows": coverage,
            "row_coverage_pass": all(
                coverage[name]["member_row_coverage"] is not None
                and coverage[name]["member_row_coverage"] >= 0.90
                for name in ("full", "discovery", "validation", "recent_252")
            ),
            "date_coverage_pass": all(
                coverage[name]["date_coverage"] is not None
                and coverage[name]["date_coverage"] >= 0.95
                for name in ("full", "discovery", "validation", "recent_252")
            ),
        }
        windows[label] = row

    loss_at_180 = {}
    for name in ("full", "discovery", "validation", "recent_252"):
        with_age = windows["180"]["windows"][name]
        without_age = windows["unlimited"]["windows"][name]
        loss_at_180[name] = {
            "valid_rows_without_expiry": without_age["valid_member_rows"],
            "valid_rows_with_180_day_expiry": with_age["valid_member_rows"],
            "valid_rows_excluded_by_180_day_expiry": (
                without_age["valid_member_rows"] - with_age["valid_member_rows"]
            ),
            "coverage_percentage_point_loss": round(
                (without_age["member_row_coverage"] - with_age["member_row_coverage"]) * 100,
                4,
            ),
        }

    result = {
        "round": "R301",
        "classification": "post_result_exploratory_coverage_sensitivity_not_preregistered",
        "purpose": "Describe how the frozen 180-calendar-day signal expiry contributes to observed coverage; do not select a replacement threshold.",
        "age_limits_days": list(AGE_LIMITS),
        "coverage_by_age_limit": windows,
        "rows_excluded_by_180_day_expiry_vs_no_expiry": loss_at_180,
        "primary_preregistered_result_unchanged": {
            "gates": primary_result["gates"],
            "all_feasibility_gates_pass": primary_result["all_feasibility_gates_pass"],
            "decision": "F1 remains failed; this sensitivity does not authorize outcome testing or a threshold change.",
        },
        "data_boundary": {
            "forward_returns_loaded": False,
            "strategy_results_loaded": False,
            "trade_outcomes_loaded": False,
            "provider_called": False,
            "panel_written": False,
        },
        "frozen_hashes": {
            "primary_input_manifest_sha256": frozen_hashes["input_manifest_sha256"],
            "primary_result_sha256": primary_result_hash,
            "amendment_sha256": amendment_hash,
            "analysis_script_sha256": script_hash,
            "panel_candidate_id": primary_result["panel_candidate_id"],
        },
        "limitations": [
            "This is post-result sensitivity analysis and cannot revise the primary preregistered gate.",
            "A longer expiry may carry stale financial information and changes the signal definition; passing coverage under it would not establish predictive value.",
            "It does not distinguish missing source reports from invalid/missing accounting values; it isolates only the expiry-rule contribution.",
        ],
    }
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT_PATH.open("xb") as stream:
        stream.write(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    OUTPUT_PATH.with_name(OUTPUT_PATH.name + ".sha256").write_text(
        f"{digest}  {OUTPUT_PATH.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "round": "R301",
        "age_limits_days": {
            key: {
                window: value["windows"][window]["member_row_coverage"]
                for window in ("full", "validation", "recent_252")
            }
            for key, value in windows.items()
        },
        "rows_excluded_by_180_day_expiry": loss_at_180,
        "output_sha256": digest,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
