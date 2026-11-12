from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r365_weak_reversal_20d_preregistration.json"
RESULT = EVIDENCE / "r365_weak_reversal_20d_backtest.json"
R363 = EVIDENCE / "r363_weak_reversal_blend_backtest.json"
OUTPUT = EVIDENCE / "r365_preregistered_gate_audit.json"
SCRIPT = Path(__file__).resolve()
TEST = ROOT / "backend/tests/test_r365_gate_reconciliation.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_preregistered_gates(prereg: dict, r365: dict, r363: dict) -> dict[str, bool]:
    metrics = r365["metrics"]
    prior = r363["metrics"]
    horizon = r365["comparison_vs_r363_five_day_matched_candidate_and_control"]
    candidate = metrics["candidate"]
    gates = {
        "G1_candidate_recent_return_improves_at_least_5pp_vs_5d": (
            candidate["recent_2x"] - prior["candidate"]["recent_2x"] >= 0.05
        ),
        "G2_20d_candidate_mdd_within_3pp_of_5d": (
            candidate["2x"]["mdd"] >= prior["candidate"]["2x"]["mdd"] - 0.03
        ),
        "G3_20d_marginal_annual_edge_not_more_than_1pp_worse": (
            horizon["candidate_minus_control_annual_effect_20d"]
            >= horizon["candidate_minus_control_annual_effect_5d"] - 0.01
        ),
        "G4_candidate_sharpe_within_0_10_of_5d": (
            candidate["2x"]["sharpe"] >= prior["candidate"]["2x"]["sharpe"] - 0.10
        ),
        "G5_candidate_annual_at_least_control_minus_1pp": (
            candidate["2x"]["annual"] >= metrics["control"]["2x"]["annual"] - 0.01
        ),
        "G6_candidate_recent_not_worse_than_control": (
            candidate["recent_2x"] >= metrics["control"]["recent_2x"]
        ),
        "G7_at_least_3_of_6_phase_starts_positive": (
            sum(item["total_return"] > 0 for item in candidate["phase_2x"].values()) >= 3
        ),
        "G8_candidate_3x_cost_annual_nonnegative": candidate["3x"]["annual"] >= 0,
        "G9_unlevered": (
            prereg["rule"]["max_exposure_pct"] <= 1.0
            and prereg["decision"]["leverage"] == "none"
        ),
    }
    if list(gates) != list(prereg["performance_gates"]):
        raise ValueError("R365 gate evaluator does not match preregistered gate names/order")
    return gates


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    r363 = json.loads(R363.read_text(encoding="utf-8"))
    registered_gates = evaluate_preregistered_gates(prereg, result, r363)
    payload = {
        "round": "R365",
        "purpose": "post-run reconciliation; does not overwrite frozen raw backtest artifact",
        "preregistration_sha256": sha(PREREG),
        "original_backtest_sha256": sha(RESULT),
        "r363_backtest_sha256": sha(R363),
        "audit_script_sha256": sha(SCRIPT),
        "audit_test_sha256": sha(TEST),
        "original_backtest_reported_gates": result["gates"],
        "preregistered_gates_recomputed": registered_gates,
        "all_preregistered_gates_pass": all(registered_gates.values()),
        "status": "research_only_no_candidate_after_r365" if not all(registered_gates.values()) else "research_only_candidate_for_followup",
        "selected": None,
        "leverage": "none",
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "note": "The original R365 runner emitted a gate set whose names and thresholds differed from the preregistration. The frozen raw backtest remains unchanged; this sidecar recomputes only the preregistered deterministic gates from its metrics and the frozen R363 comparator.",
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"gates": registered_gates, "all_pass": payload["all_preregistered_gates_pass"], "output": str(OUTPUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
