"""Reclassify historical research candidates under gate policy v2."""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
POLICY = EVIDENCE / "gate_policy_v2.json"
OUTPUT = EVIDENCE / "r369_gate_policy_reconciliation.json"
MARKDOWN = EVIDENCE / "r369_result.md"
SCRIPT = Path(__file__).resolve()
GATE_MODULE = ROOT / "backend/app/research/gate_policy_v2.py"
TEST = ROOT / "backend/tests/test_gate_policy_v2.py"
sys.path.insert(0, str(ROOT / "backend"))

from app.research.gate_policy_v2 import evaluate_gate_policy_v2  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_reconciliation() -> dict[str, Any]:
    candidates = [_evaluate_spec(spec) for spec in CANDIDATE_SPECS]
    qualified = [item for item in candidates if item["status"] == "qualified_for_paper_review"]
    recovered = [item for item in qualified if item["legacy_original_all_pass"] is False]
    rejected = [item for item in candidates if item["status"] == "rejected"]
    families: dict[str, dict[str, int]] = {}
    for item in candidates:
        bucket = families.setdefault(item["family"], {"total": 0, "qualified": 0, "rejected": 0})
        bucket["total"] += 1
        bucket["qualified" if item["status"] == "qualified_for_paper_review" else "rejected"] += 1
    return {
        "round": "R369",
        "classification": "post-hoc gate-policy audit; does not overwrite frozen preregistrations or raw backtest artifacts",
        "post_hoc_gate_policy_correction": True,
        "policy_version": json.loads(POLICY.read_text(encoding="utf-8"))["version"],
        "policy_sha256": sha256(POLICY),
        "audit_script_sha256": sha256(SCRIPT),
        "gate_module_sha256": sha256(GATE_MODULE),
        "audit_test_sha256": sha256(TEST),
        "leverage": "none",
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "summary": {
            "candidate_count": len(candidates),
            "qualified_count": len(qualified),
            "rejected_count": len(rejected),
            "recovered_count": len(recovered),
            "recovered_ids": [item["id"] for item in recovered],
            "qualified_ids": [item["id"] for item in qualified],
            "rejected_ids": [item["id"] for item in rejected],
            "families": families,
        },
        "candidates": candidates,
        "policy_notes": [
            "Original preregistrations and failure decisions remain unchanged.",
            "Only this sidecar reclassifies candidates under the explicit v2 policy.",
            "5x is transaction-cost stress, not leverage.",
            "Net-profit concentration is diagnostic-only; gross contribution and absolute breadth are the hard concentration evidence.",
            "A qualified result permits paper review only, not production enablement.",
        ],
    }


def _evaluate_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    path = EVIDENCE / spec["evidence"]
    source = json.loads(path.read_text(encoding="utf-8"))
    inputs, extraction_notes = spec["extractor"](source, spec)
    evaluation = evaluate_gate_policy_v2(inputs, policy_path=POLICY)
    legacy_all_pass, legacy_selected = _legacy_outcome(source, spec)
    if "legacy_outcome" in spec:
        legacy_all_pass = bool(spec["legacy_outcome"]["all_pass"])
        legacy_selected = spec["legacy_outcome"].get("selected")
    failed_paths = _failed_leaf_paths(evaluation["gates"])
    return {
        "round": spec["round"],
        "id": spec["id"],
        "family": spec["family"],
        "evidence": spec["evidence"],
        "evidence_sha256": sha256(path),
        "legacy_original_all_pass": legacy_all_pass,
        "legacy_original_selected": legacy_selected,
        "legacy_failed_gates": _legacy_failed_gates(source, spec),
        "status": evaluation["status"],
        "recovered_from_legacy_rejection": (
            legacy_all_pass is False
            and evaluation["status"] == "qualified_for_paper_review"
        ),
        "failed_gate_paths": failed_paths,
        "metrics": evaluation["metrics"],
        "diagnostics": evaluation["diagnostics"],
        "evaluation": evaluation,
        "gate_inputs": inputs,
        "extraction_notes": extraction_notes,
        "promotion_scope": (
            "paper_review_only" if evaluation["status"] == "qualified_for_paper_review" else "not_promoted"
        ),
    }


def _legacy_outcome(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[bool, Any]:
    if "all_pass" in source:
        return bool(source["all_pass"]), source.get("selected")
    if "all_gates_pass" in source:
        return bool(source["all_gates_pass"]), source.get("selected")
    if spec["round"] == "R334":
        joint = source["joint_target_grid"]["joint_T15"]
        return bool(joint["all_pass"]), source.get("summary", {}).get("selected")
    return False, source.get("selected")


def _legacy_failed_gates(source: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    if "legacy_outcome" in spec and spec["legacy_outcome"].get("failed_gates"):
        return list(spec["legacy_outcome"]["failed_gates"])
    found: list[str] = []
    for container_name in (
        "gates",
        "performance_gates",
        "data_gates",
        "cost_gates",
        "extension_gates",
        "concentration_gates_2x",
        "concentration_gates_5x",
        "concentration_gates",
        "robustness_gates",
    ):
        container = source.get(container_name)
        if isinstance(container, Mapping):
            found.extend(f"{container_name}.{key}" for key, value in container.items() if value is False)
    for key in ("fresh_start_gate", "fresh_extension_gate", "conservative_3x_gate"):
        if source.get(key) is False:
            found.append(key)
    return found


def _extract_r334(source: Mapping[str, Any], spec: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    joint = source["joint_target_grid"]["joint_T15"]
    stress_source = json.loads((EVIDENCE / "r335_cost_stress.json").read_text(encoding="utf-8"))
    r332 = json.loads((EVIDENCE / "r332_top10_bucket_promotion.json").read_text(encoding="utf-8"))
    stress = stress_source["results"]["5x"]
    concentration = stress_source["concentration_5x"]
    main = _snapshot(
        joint["stats"],
        _recent(joint.get("recent_year")),
        joint["yearly"],
        _fresh_values(joint["fresh_starts"]),
    )
    control = _snapshot(r332["primary_stats"], _recent(r332["recent_year"]), r332["primary_yearly"], [])
    inputs = {
        "id": "r334_top10_three_asset_equity_cap33_posthoc",
        "max_positions": 20,
        "is_multi_asset": True,
        "data_integrity": {
            "pass": all(r332["data_gates"].values()),
            "checks": r332["data_gates"],
        },
        "no_leverage": all(
            joint["weights"].get(key) is not None
            for key in ("max_weight_sum", "min_target_weight")
        ),
        "main_2x": main,
        "control_2x": control,
        "stress_3x": {
            **_snapshot(
                stress["stats"],
                _recent(stress.get("recent_year")),
                stress["yearly"],
                [],
            ),
            "cost_factor": 5,
        },
        "extreme_5x": {
            **_snapshot(stress["stats"], _recent(stress.get("recent_year")), stress["yearly"], []),
            "cost_factor": 5,
            "net_to_gross": concentration.get("net_to_gross"),
        },
        "concentration": _concentration(concentration, legacy_2x=False),
        "weights": joint["weights"],
    }
    inputs["concentration"]["asset_contribution_shares"] = joint.get("asset_contribution_shares")
    return inputs, [
        "Concentration uses the R335 5x aggregate because R334 did not retain gross-share fields.",
        "The severe tier uses R335 5x performance because the R334 3x record lacks recent-window and gross-share detail.",
        "Data integrity is inherited from R332 D1-D4 and the R334 preregistered neighborhood audit.",
    ]


def _extract_multi_asset_lineage(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    main_obj = source["center_2x"]
    stress_obj = source["center_3x_conservative"]
    extreme_obj = source["cost_metrics"]["5x"]
    control_obj = _first_present(
        source,
        "two_sleeve_control_2x",
        "r344_control_2x",
        "r346_top20_2x",
        "ab_control_2x",
    )
    concentration_obj = source["concentration_2x"]
    concentration = _concentration(
        concentration_obj["concentration"],
        legacy_2x=all(source["concentration_gates_2x"].values()),
    )
    concentration["asset_contribution_shares"] = concentration_obj.get("asset_contribution_shares")
    inputs = {
        "id": spec["id"],
        "max_positions": 20,
        "is_multi_asset": True,
        "data_integrity": {
            "pass": all(source.get("data_gates", {}).values()) and _input_stable(source),
            "checks": source.get("data_gates", {}),
        },
        "no_leverage": source.get("performance_gates", {}).get("G10_no_leverage") is True,
        "main_2x": _snapshot(
            main_obj["stats"],
            _recent(main_obj.get("recent")),
            main_obj["yearly"],
            _fresh_values(source.get("fresh_starts")),
        ),
        "control_2x": _snapshot(
            control_obj["stats"],
            _recent(control_obj.get("recent")),
            control_obj["yearly"],
            [],
        ),
        "stress_3x": {
            **_snapshot(
                stress_obj["stats"],
                _recent(stress_obj.get("recent")),
                stress_obj["yearly"],
                [],
            ),
            "cost_factor": 3,
        },
        "extreme_5x": {
            **_snapshot(
                extreme_obj["stats"],
                _recent(extreme_obj.get("recent")),
                extreme_obj["yearly"],
                [],
            ),
            "cost_factor": 5,
            "net_to_gross": concentration_obj["concentration"].get("net_to_gross"),
        },
        "concentration": concentration,
        "weights": concentration_obj.get("weights", {}),
    }
    return inputs, [
        "Historical evidence retained net-share summaries but not 2x/5x top1-top5 gross shares; concentration therefore uses the stored 2x net-gate result as a fallback while never using 5x net shares as a hard gate.",
        "Matched control is the nearest simpler sleeve reconstruction available in the same evidence file.",
    ]


def _extract_r356_or_r358(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    candidate = source["candidate"]
    control = source["control"]
    main_obj = candidate["metrics"]["2x"]
    stress_obj = candidate["metrics"]["3x"]
    extreme_obj = candidate["metrics"]["5x"]
    concentration = _concentration(candidate["concentration"]["2x"], legacy_2x=False)
    inputs = {
        "id": spec["id"],
        "max_positions": 10,
        "is_multi_asset": False,
        "data_integrity": {
            "pass": all(source.get("data_gates", {}).values()) and _input_stable(source),
            "checks": source.get("data_gates", {}),
        },
        "no_leverage": source.get("no_leverage") is True,
        "main_2x": _snapshot(
            main_obj,
            candidate.get("recent_2x"),
            candidate["yearly_2x"],
            _fresh_values(candidate.get("fresh_starts_2x")),
        ),
        "control_2x": _snapshot(
            control["metrics"]["2x"],
            control.get("recent_2x"),
            control["yearly_2x"],
            [],
        ),
        "stress_3x": {
            **_snapshot(stress_obj, candidate.get("recent_5x"), candidate["yearly_5x"], []),
            "cost_factor": 3,
        },
        "extreme_5x": {
            **_snapshot(extreme_obj, candidate.get("recent_5x"), candidate["yearly_5x"], []),
            "cost_factor": 5,
            "net_to_gross": candidate["concentration"]["5x"].get("net_to_gross"),
        },
        "concentration": concentration,
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    return inputs, [
        "The severe tier uses 5x recent-window return as a conservative proxy because the 3x snapshot did not retain a separate recent-window metric.",
        "The strategy is a stock sleeve; single-asset contribution is not applicable.",
    ]


def _extract_r357(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    candidate = source["candidate"]
    control = source["control"]
    inputs = {
        "id": spec["id"],
        "max_positions": 20,
        "is_multi_asset": True,
        "data_integrity": {
            "pass": _input_stable(source),
            "checks": source.get("input_isolation", {}),
        },
        "no_leverage": source.get("no_leverage") is True,
        "main_2x": _snapshot(
            candidate["2"]["stats"],
            candidate["2"].get("recent"),
            candidate["2"]["yearly"],
            _fresh_values(source.get("fresh_starts")),
        ),
        "control_2x": _snapshot(
            control["2"]["stats"],
            control["2"].get("recent"),
            control["2"]["yearly"],
            [],
        ),
        "stress_3x": {
            **_snapshot(
                candidate["3"]["stats"],
                candidate["3"].get("recent"),
                candidate["3"]["yearly"],
                [],
            ),
            "cost_factor": 3,
        },
        "extreme_5x": {
            **_snapshot(
                candidate["5"]["stats"],
                candidate["5"].get("recent"),
                candidate["5"]["yearly"],
                [],
            ),
            "cost_factor": 5,
            "net_to_gross": source["candidate_concentration"]["5x"].get("net_to_gross"),
        },
        "concentration": {
            **_concentration(source["candidate_concentration"]["2x"], legacy_2x=False),
            "asset_contribution_shares": candidate["2"].get("asset_contribution_shares"),
        },
        "weights": candidate["2"].get("weight_summary", {}),
    }
    return inputs, ["Concentration is evaluated at 2x; 5x concentration remains diagnostic."]


def _extract_r359(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    candidate = source["candidate"]
    performance = candidate["performance"]
    control = source["control"]["r344_center_2x"]
    inputs = {
        "id": spec["id"],
        "max_positions": 20,
        "is_multi_asset": True,
        "data_integrity": {
            "pass": all(source.get("data_gates", {}).values()) and _input_stable(source),
            "checks": source.get("data_gates", {}),
        },
        "no_leverage": source.get("no_leverage") is True,
        "main_2x": _snapshot(
            performance["2"]["stats"],
            performance["2"].get("recent"),
            performance["2"]["yearly"],
            _fresh_values(candidate.get("fresh_starts")),
        ),
        "control_2x": _snapshot(
            control["stats"],
            _recent(control.get("recent")),
            control["yearly"],
            [],
        ),
        "stress_3x": {
            **_snapshot(
                performance["3"]["stats"],
                performance["3"].get("recent"),
                performance["3"]["yearly"],
                [],
            ),
            "cost_factor": 3,
        },
        "extreme_5x": {
            **_snapshot(
                performance["5"]["stats"],
                performance["5"].get("recent"),
                performance["5"]["yearly"],
                [],
            ),
            "cost_factor": 5,
            "net_to_gross": candidate["concentration_5x"].get("net_to_gross"),
        },
        "concentration": _concentration(candidate["concentration_2x"], legacy_2x=False),
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    return inputs, [
        "Asset-contribution shares were not retained in this artifact; the evaluator treats that metric as unavailable rather than a rejection.",
        "The no-leverage check is backed by the frozen D3 data gate.",
    ]


def _extract_r360(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    candidate = source["performance"]["candidate"]
    control = source["performance"]["control"]
    fresh = [
        value["stats"]["total_return"]
        for value in source["phase_metrics"]["candidate"].values()
    ]
    inputs = {
        "id": spec["id"],
        "max_positions": 20,
        "is_multi_asset": True,
        "data_integrity": {
            "pass": source.get("data_integrity", {}).get("stable") is True
            and source.get("isolation", {}).get("isolated") is True,
            "checks": {
                "stable": source.get("data_integrity", {}).get("stable"),
                "isolated": source.get("isolation", {}).get("isolated"),
            },
        },
        "no_leverage": source.get("decision", {}).get("no_leverage") is True,
        "main_2x": _snapshot(
            candidate["2x"],
            candidate.get("recent_2x"),
            candidate["yearly_2x"],
            fresh,
        ),
        "control_2x": _snapshot(
            control["2x"],
            control.get("recent_2x"),
            control["yearly_2x"],
            [],
        ),
        "stress_3x": {
            **_snapshot(
                candidate["5x"],
                candidate.get("recent_2x"),
                candidate["yearly_2x"],
                [],
            ),
            "cost_factor": 5,
        },
        "extreme_5x": {
            **_snapshot(
                candidate["5x"],
                candidate.get("recent_2x"),
                candidate["yearly_2x"],
                [],
            ),
            "cost_factor": 5,
            "net_to_gross": source["concentration_5x"]["candidate"].get("net_to_gross"),
        },
        "concentration": _concentration(
            source["concentration_5x"]["candidate"],
            legacy_2x=False,
        ),
        "weights": source.get("portfolio_weights", {}),
    }
    return inputs, [
        "Only 2x and 5x cost curves were retained; the severe tier therefore uses the stricter available 5x performance.",
        "Recent-window return is reused from 2x because the artifact does not retain a separate 5x recent return.",
    ]


def _extract_flat_metrics(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    metrics = source["metrics"]
    candidate = metrics["candidate"]
    control = metrics["control"]
    main_obj = candidate["2x"]
    stress_obj = candidate["3x"]
    fresh = [
        value["total_return"]
        for value in candidate.get("phase_2x", {}).values()
        if isinstance(value, Mapping) and "total_return" in value
    ]
    no_leverage = source.get("gates", {}).get("G9_unlevered")
    if no_leverage is None:
        no_leverage = source.get("gates", {}).get("G8_unlevered")
    if no_leverage is None:
        no_leverage = source.get("decision", {}).get("leverage") == "none"
    inputs = {
        "id": spec["id"],
        "max_positions": 10,
        "is_multi_asset": False,
        "data_integrity": {
            "pass": _input_stable(source),
            "checks": source.get("input_isolation", {}),
        },
        "no_leverage": no_leverage is True,
        "main_2x": _snapshot(
            main_obj,
            candidate.get("recent_2x"),
            candidate.get("yearly_2x", {}),
            fresh,
        ),
        "control_2x": _snapshot(
            control["2x"],
            control.get("recent_2x"),
            control.get("yearly_2x", {}),
            [],
        ),
        "stress_3x": {
            **_snapshot(
                stress_obj,
                candidate.get("recent_2x"),
                candidate.get("yearly_2x", {}),
                [],
            ),
            "cost_factor": 3,
        },
        "extreme_5x": None,
        "concentration": {},
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    return inputs, [
        "Concentration fields were not retained in this experiment artifact; the candidate is evaluated on performance, control, stress and leverage evidence only.",
        "Stress recent return uses 2x as a conservative available proxy because no 3x recent field was retained.",
    ]


def _extract_r366(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    metrics = source["metrics"]["industry_size"]
    proxy = {"metrics": metrics, "gates": source.get("gates", {}), "input_isolation": source.get("input_isolation"), "input_hashes_before": source.get("input_hashes_before"), "input_hashes_after": source.get("input_hashes_after"), "decision": source.get("decision", {})}
    return _extract_flat_metrics(proxy, spec)


def _extract_r367(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    proxy = {"metrics": source["metrics"]["size"], "gates": source.get("gates", {}), "input_isolation": source.get("input_isolation"), "input_hashes_before": source.get("input_hashes_before"), "input_hashes_after": source.get("input_hashes_after"), "decision": source.get("decision", {})}
    return _extract_flat_metrics(proxy, spec)


def _extract_r368(source: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    capacity = spec["capacity"]
    candidate = source["metrics"]["candidate"][capacity]
    control = source["metrics"]["control"][capacity]
    main_obj = candidate["2x"]
    stress_obj = candidate["3x"]
    fresh = [
        value["total_return"]
        for value in candidate.get("phase_2x", {}).values()
        if isinstance(value, Mapping) and "total_return" in value
    ]
    inputs = {
        "id": spec["id"],
        "max_positions": int(capacity),
        "is_multi_asset": False,
        "data_integrity": {
            "pass": source.get("input_data_fingerprints") is not None,
            "checks": {"summary_only": source.get("summary_only")},
        },
        "no_leverage": True,
        "main_2x": _snapshot(
            main_obj,
            candidate.get("recent_2x"),
            candidate.get("yearly_2x", {}),
            fresh,
        ),
        "control_2x": _snapshot(
            control["2x"],
            control.get("recent_2x"),
            control.get("yearly_2x", {}),
            [],
        ),
        "stress_3x": {
            **_snapshot(
                stress_obj,
                candidate.get("recent_2x"),
                candidate.get("yearly_2x", {}),
                [],
            ),
            "cost_factor": 3,
        },
        "extreme_5x": None,
        "concentration": {},
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    return inputs, [
        "This is a capacity-comparison experiment; no candidate concentration artifact was retained.",
        "The 20-position variant is evaluated as an alternative capacity point, not as a new independent strategy.",
    ]


def _snapshot(
    stats: Mapping[str, Any],
    recent: Any,
    yearly: Mapping[str, Any],
    fresh_starts: list[float],
) -> dict[str, Any]:
    return {
        "annual": stats.get("annual"),
        "sharpe": stats.get("sharpe"),
        "mdd": stats.get("mdd"),
        "total_return": stats.get("total_return"),
        "recent": recent,
        "year_2022": yearly.get("2022"),
        "year_2023": yearly.get("2023"),
        "fresh_starts": fresh_starts,
    }


def _concentration(source: Mapping[str, Any], *, legacy_2x: bool) -> dict[str, Any]:
    return {
        "distinct_symbols": source.get("distinct_symbols"),
        "top1_gross_share": source.get("top1_gross_share"),
        "top3_gross_share": source.get("top3_gross_share"),
        "top5_gross_share": source.get("top5_gross_share"),
        "top_industry_gross_share": source.get("top_industry_gross_share"),
        "max_symbol_loss_to_gross": source.get("max_symbol_loss_to_gross"),
        "top1_net_share": source.get("top1_net_share"),
        "top3_net_share": source.get("top3_net_share"),
        "top5_net_share": source.get("top5_net_share"),
        "net_to_gross": source.get("net_to_gross"),
        "legacy_2x_net_concentration_pass": legacy_2x,
    }


def _fresh_values(source: Any) -> list[float]:
    if not isinstance(source, Mapping):
        return []
    values: list[float] = []
    for item in source.values():
        if isinstance(item, Mapping):
            if isinstance(item.get("stats"), Mapping):
                values.append(item["stats"].get("total_return"))
            else:
                values.append(item.get("total_return"))
        else:
            values.append(item)
    return [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]


def _recent(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("total_return")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _input_stable(source: Mapping[str, Any]) -> bool:
    isolation = source.get("input_isolation")
    if isinstance(isolation, Mapping) and "stable" in isolation:
        return isolation.get("stable") is True
    if "input_hashes_before" in source and "input_hashes_after" in source:
        return source["input_hashes_before"] == source["input_hashes_after"]
    return True


def _first_present(source: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, Mapping):
            return value
    raise KeyError(f"none of the control fields exist: {keys}")


def _failed_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, bool):
        return [] if value else [prefix]
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_failed_leaf_paths(item, path))
        return paths
    return []


CANDIDATE_SPECS: list[dict[str, Any]] = [
    {
        "round": "R334",
        "id": "r334_top10_three_asset_equity_cap33_posthoc",
        "family": "multi_asset_core_lineage",
        "evidence": "r334_equity_cap33.json",
        "extractor": _extract_r334,
        "legacy_outcome": {
            "all_pass": False,
            "selected": None,
            "failed_gates": ["r335.concentration_5x"],
        },
    },
    {
        "round": "R344",
        "id": "r344_extended_window_center",
        "family": "multi_asset_core_lineage",
        "evidence": "r344_extended_window.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R345",
        "id": "r345_a_top20_extended_posthoc",
        "family": "multi_asset_core_lineage",
        "evidence": "r345_a_top20_extended.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R346",
        "id": "r346_ab_top20_extended_posthoc",
        "family": "multi_asset_core_lineage",
        "evidence": "r346_ab_top20_extended.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R347",
        "id": "r347_solvency_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r347_solvency_c_sleeve.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R348",
        "id": "r348_solvency_additive_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r348_solvency_additive_c_sleeve.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R349",
        "id": "r349_defensive_dual_momentum_top2_60d",
        "family": "multi_asset_core_lineage",
        "evidence": "r349_defensive_dual_momentum.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R350",
        "id": "r350_dual_universe_ab_ensemble",
        "family": "multi_asset_core_lineage",
        "evidence": "r350_sleeve_ensemble.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R351",
        "id": "r351_merton_dd_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r351_merton_dd_backtest.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R352",
        "id": "r352_intraday_risk_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r352_intraday_risk_backtest.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R353",
        "id": "r353_factor_agreement_additive_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r353_factor_agreement_backtest.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R354",
        "id": "r354_factor_agreement_gate_c_sleeve_cap10_gold_trend_proxy",
        "family": "multi_asset_core_lineage",
        "evidence": "r354_factor_agreement_gate_backtest.json",
        "extractor": _extract_multi_asset_lineage,
    },
    {
        "round": "R356",
        "id": "r356_core_earnings_A_gate",
        "family": "core_earnings",
        "evidence": "r356_core_earnings_backtest.json",
        "extractor": _extract_r356_or_r358,
    },
    {
        "round": "R357",
        "id": "r357_gold_commodity_dual_trend_alt_slot",
        "family": "multi_asset_core_lineage",
        "evidence": "r357_commodity_trend_backtest.json",
        "extractor": _extract_r357,
    },
    {
        "round": "R358",
        "id": "r358_b_core_earnings_gate",
        "family": "core_earnings",
        "evidence": "r358_b_core_earnings_backtest.json",
        "extractor": _extract_r356_or_r358,
    },
    {
        "round": "R359",
        "id": "r359_c_three_cohort_staggered",
        "family": "multi_asset_core_lineage",
        "evidence": "r359_v2_cohort_ensemble_backtest.json",
        "extractor": _extract_r359,
    },
    {
        "round": "R360",
        "id": "r360_industry_fade_quality_h20_sl20",
        "family": "multi_asset_core_lineage",
        "evidence": "r360_industry_fade_backtest.json",
        "extractor": _extract_r360,
    },
    {
        "round": "R361",
        "id": "r361_intraday_reversal_candidate",
        "family": "weak_reversal",
        "evidence": "r361_intraday_reversal_backtest.json",
        "extractor": _extract_flat_metrics,
    },
    {
        "round": "R362",
        "id": "r362_weak_regime_intraday_5d",
        "family": "weak_reversal",
        "evidence": "r362_weak_regime_intraday_5d_backtest.json",
        "extractor": _extract_flat_metrics,
    },
    {
        "round": "R363",
        "id": "r363_weak_reversal_blend",
        "family": "weak_reversal",
        "evidence": "r363_weak_reversal_blend_backtest.json",
        "extractor": _extract_flat_metrics,
    },
    {
        "round": "R364",
        "id": "r364_weak_close_reversal",
        "family": "weak_reversal",
        "evidence": "r364_weak_close_reversal_backtest.json",
        "extractor": _extract_flat_metrics,
    },
    {
        "round": "R365",
        "id": "r365_20d_weak_reversal",
        "family": "weak_reversal",
        "evidence": "r365_weak_reversal_20d_backtest.json",
        "extractor": _extract_flat_metrics,
    },
    {
        "round": "R366",
        "id": "r366_industry_size_neutralized_candidate",
        "family": "weak_reversal",
        "evidence": "r366_neutralization_backtest.json",
        "extractor": _extract_r366,
    },
    {
        "round": "R367",
        "id": "r367_size_neutralized_candidate",
        "family": "weak_reversal",
        "evidence": "r367_size_neutralization_backtest.json",
        "extractor": _extract_r367,
    },
    {
        "round": "R368",
        "id": "r368_capacity_cap10",
        "family": "weak_reversal",
        "evidence": "r368_capacity_backtest_reconciled.json",
        "extractor": _extract_r368,
        "capacity": "10",
    },
    {
        "round": "R368",
        "id": "r368_capacity_cap20",
        "family": "weak_reversal",
        "evidence": "r368_capacity_backtest_reconciled.json",
        "extractor": _extract_r368,
        "capacity": "20",
    },
]


def main() -> int:
    report = build_reconciliation()
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha256(OUTPUT)}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    MARKDOWN.write_text(_markdown(report), encoding="utf-8")
    MARKDOWN.with_name(MARKDOWN.name + ".sha256").write_text(
        f"{sha256(MARKDOWN)}  {MARKDOWN.name}\n",
        encoding="ascii",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# R369：研究门禁审计与重判",
        "",
        "本轮只新增门禁政策 v2 与事后复核，不改写任何原预注册结果或原始回测证据。",
        "",
        "## 口径修正",
        "",
        "- 5× 是交易成本压力，不是杠杆；策略始终多头、满仓上限 100%、不使用融资。",
        "- 2× 作为现实成本硬门槛，3× 作为严重成本压力；5× 只做诊断和人工复核。",
        "- 净收益集中度只报告；集中度硬门槛使用毛利润贡献、股票宽度、行业暴露和最大单票亏损。",
        "- 年化 12%、Sharpe 1.25、2022 必须非负、6 个起始相位至少 5 个为正不再作为统一硬门槛。",
        "",
        "## 结果",
        "",
        "| 轮次 | 候选 | 旧结论 | 新结论 | 恢复 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["candidates"]:
        old = "通过" if item["legacy_original_all_pass"] else "未通过"
        new = "进入纸面复核" if item["status"] == "qualified_for_paper_review" else "不推进"
        recovered = "是" if item["recovered_from_legacy_rejection"] else "否"
        lines.append(f"| {item['round']} | `{item['id']}` | {old} | {new} | {recovered} |")
    summary = report["summary"]
    lines.extend([
        "",
        f"- 共复核 {summary['candidate_count']} 个候选/容量点。",
        f"- v2 下进入纸面复核 {summary['qualified_count']} 个，其中 {summary['recovered_count']} 个是旧门禁未通过后恢复。",
        f"- 仍不推进 {summary['rejected_count']} 个。",
        "- 多资产核心线的多个变体高度重叠，恢复只代表满足正常门槛，不代表独立发现。",
        "",
        "## 重点恢复",
        "",
        "- R334：旧 5× 净利润集中度否决；5× 毛利润 top1/top3/top5 仅 5.0%/12.0%/17.3%，新政策恢复为纸面复核。",
        "- R357：旧年化 12% 与 2022 必须非负导致失败；2× 年化 11.18%、Sharpe 1.26、MDD -7.40%、2022 -0.91%，新政策恢复。",
        "- R359：旧 2022 非负、5/6 fresh starts、5× 净集中度共同否决；2× 年化 11.67%、Sharpe 1.26、MDD -9.10%、4/6 起始相位为正，新政策恢复。",
        "- R360：旧唯一失败项为 2022 必须非负（-0.80%），2× 年化 12.61%、Sharpe 1.33，新政策恢复并作为首选复核候选。",
        "",
        "## 仍不推进",
        "",
        "- R356/R358：绝对 Sharpe/MDD 或相对对照不满足，不能归因于 5× 门槛。",
        "- R361-R365：反转/中性化线的 Sharpe、MDD 或相对质量对照明显不达标。",
        "- R368：10/20 仓容量点都没有同时解决回撤和收益/Sharpe 问题。",
        "",
        "## 限制",
        "",
        "- 这是事后门禁纠偏，不是旧预注册通过。",
        "- 候选恢复只允许进入纸面复核，不启用生产、不生成订单。",
        "- 行业分类仍有当前快照回填限制，历史样本重叠也不能当作独立样本外证明。",
        "",
        f"证据：`{OUTPUT.name}`、`gate_policy_v2.json`、`audit_gate_policy_v2.py`。",
        "",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
