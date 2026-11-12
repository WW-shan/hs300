from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
CACHE = ROOT / "data/research/r368_cache"
PRE_REG = EVIDENCE / "r368_capacity_preregistration.json"
PRELIGHT = EVIDENCE / "r368_capacity_preflight.json"
FAILURE = EVIDENCE / "r368_capacity_backtest_attempt1_failure.json"
R367_RESULT = EVIDENCE / "r367_size_neutralization_backtest.json"
SUMMARY_TEST = ROOT / "backend/tests/test_r368_capacity_summary.py"
MANIFEST = EVIDENCE / "r368_reconciliation_manifest.json"
OUTPUT = EVIDENCE / "r368_capacity_backtest_reconciled.json"
FINAL_STATUS = EVIDENCE / "r368_reconciled_final_status.json"
RESULT_MD = EVIDENCE / "r368_reconciled_result.md"
PHASES = ["2022-05-05", "2023-01-03", "2024-01-02", "2025-01-02", "2025-10-14", "2026-01-05"]
CAPACITIES = (10, 20)
COSTS = (2, 3)
LABELS = ("candidate", "control")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_cache_names() -> tuple[str, ...]:
    return tuple(
        f"{label}_cap{capacity}_{start}_{cost}x.parquet"
        for label in LABELS
        for capacity in CAPACITIES
        for start in PHASES
        for cost in COSTS
    )


def curve_cache_manifest() -> dict[str, str]:
    expected = set(expected_cache_names())
    actual = {path.name for path in CACHE.glob("*.parquet")}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise RuntimeError(
            f"R368 cache set mismatch; missing={missing}, unexpected={unexpected}"
        )
    return {name: sha(CACHE / name) for name in sorted(expected)}


def reconciliation_manifest() -> dict:
    return {
        "round": "R368",
        "classification": "deterministic reconciliation manifest for preserved system-backtest curves",
        "summary_source_curves": "data/research/r368_cache",
        "system_backtests_rerun": False,
        "original_preregistration_sha256": sha(PRE_REG),
        "original_preflight_sha256": sha(PRELIGHT),
        "original_failure_sha256": sha(FAILURE),
        "r367_result_sha256": sha(R367_RESULT),
        "summary_script_sha256": sha(Path(__file__).resolve()),
        "summary_test_sha256": sha(SUMMARY_TEST),
        "curve_count": len(expected_cache_names()),
        "curve_sha256": curve_cache_manifest(),
        "trades_persisted": False,
        "trade_count_limitation": "The original R368 runner did not persist trade counts alongside the 48 preserved curves; scorecard trades remain null rather than inferred.",
    }


def load_or_create_manifest() -> dict:
    payload = reconciliation_manifest()
    checksum_path = MANIFEST.with_name(MANIFEST.name + ".sha256")
    if MANIFEST.exists() or checksum_path.exists():
        if not MANIFEST.is_file() or not checksum_path.is_file():
            raise RuntimeError("R368 reconciliation manifest is incomplete")
        existing = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError("R368 reconciliation manifest does not match current frozen inputs")
        if checksum_path.read_text(encoding="ascii").split()[0] != sha(MANIFEST):
            raise RuntimeError("R368 reconciliation manifest checksum mismatch")
        return existing
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_path.write_text(f"{sha(MANIFEST)}  {MANIFEST.name}\n", encoding="ascii")
    return payload


def r367_candidate_metrics(r367: dict) -> dict:
    try:
        return r367["metrics"]["size"]["candidate"]
    except (KeyError, TypeError) as exc:
        raise ValueError("R367 result must contain metrics.size.candidate") from exc


def r367_control_metrics(r367: dict) -> dict:
    try:
        return r367["metrics"]["size"]["control"]
    except (KeyError, TypeError) as exc:
        raise ValueError("R367 result must contain metrics.size.control") from exc


def compare_capacity_metrics(metrics: dict, r367: dict) -> dict:
    candidate = metrics["candidate"]
    control = metrics["control"]
    baseline_candidate = r367_candidate_metrics(r367)
    baseline_control = r367_control_metrics(r367)
    candidate_edge_10 = candidate["10"]["2x"]["annual"] - control["10"]["2x"]["annual"]
    candidate_edge_20 = candidate["20"]["2x"]["annual"] - control["20"]["2x"]["annual"]
    return {
        "candidate_20_minus_10_annual": candidate["20"]["2x"]["annual"] - candidate["10"]["2x"]["annual"],
        "candidate_20_minus_10_sharpe": candidate["20"]["2x"]["sharpe"] - candidate["10"]["2x"]["sharpe"],
        "candidate_20_minus_10_mdd": candidate["20"]["2x"]["mdd"] - candidate["10"]["2x"]["mdd"],
        "candidate_20_minus_10_recent": candidate["20"]["recent_2x"] - candidate["10"]["recent_2x"],
        "control_20_minus_10_annual": control["20"]["2x"]["annual"] - control["10"]["2x"]["annual"],
        "control_20_minus_10_mdd": control["20"]["2x"]["mdd"] - control["10"]["2x"]["mdd"],
        "control_20_minus_10_recent": control["20"]["recent_2x"] - control["10"]["recent_2x"],
        "candidate_control_edge_10": candidate_edge_10,
        "candidate_control_edge_20": candidate_edge_20,
        "candidate_control_edge_change_20_minus_10": candidate_edge_20 - candidate_edge_10,
        "cap20_candidate_annual_change_vs_r367": candidate["20"]["2x"]["annual"] - baseline_candidate["2x"]["annual"],
        "cap20_candidate_recent_change_vs_r367": candidate["20"]["recent_2x"] - baseline_candidate["recent_2x"],
        "cap20_candidate_mdd_change_vs_r367": candidate["20"]["2x"]["mdd"] - baseline_candidate["2x"]["mdd"],
        "cap20_control_annual_change_vs_r367": control["20"]["2x"]["annual"] - baseline_control["2x"]["annual"],
    }


def evaluate_capacity_gates(prereg: dict, metrics: dict, r367: dict) -> dict[str, bool]:
    comparison = compare_capacity_metrics(metrics, r367)
    candidate_20 = metrics["candidate"]["20"]
    control_20 = metrics["control"]["20"]
    gates = {
        "G1_cap20_candidate_mdd_improves_3pp": comparison["candidate_20_minus_10_mdd"] >= 0.03,
        "G2_cap20_candidate_recent_improves_3pp": comparison["candidate_20_minus_10_recent"] >= 0.03,
        "G3_cap20_candidate_annual_not_more_than_3pp_lower": comparison["candidate_20_minus_10_annual"] >= -0.03,
        "G4_cap20_sharpe_not_more_than_0_05_lower": comparison["candidate_20_minus_10_sharpe"] >= -0.05,
        "G5_candidate_control_edge_not_worse_2pp": comparison["candidate_control_edge_20"] >= comparison["candidate_control_edge_10"] - 0.02,
        "G6_cap20_candidate_recent_at_least_control": candidate_20["recent_2x"] >= control_20["recent_2x"],
        "G7_at_least_4_of_6_cap20_phases_positive": sum(
            item["total_return"] > 0 for item in candidate_20["phase_2x"].values()
        ) >= 4,
        "G8_cap20_candidate_3x_annual_nonnegative": candidate_20["3x"]["annual"] >= 0,
        "G9_unlevered": prereg["rule"]["max_exposure_pct"] <= 1.0 and prereg["decision"]["leverage"] == "none",
    }
    if list(gates) != list(prereg["performance_gates"]):
        raise ValueError("R368 gate names/order do not match the preregistration")
    return gates


def load_metrics_from_cache() -> tuple[dict, dict]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("r368_runner", EVIDENCE / "run_r368_capacity.py")
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)
    if tuple(phase.isoformat() for phase in runner.PHASES) != tuple(PHASES):
        raise ValueError("R368 runner phases differ from the reconciler")
    curves = {}
    scorecard = {label: {str(capacity): {} for capacity in CAPACITIES} for label in LABELS}
    metrics = {label: {} for label in LABELS}
    for label in LABELS:
        for capacity in CAPACITIES:
            phase_curves = {}
            for start in PHASES:
                for cost in COSTS:
                    name = f"{label}_cap{capacity}_{start}_{cost}x"
                    path = CACHE / f"{name}.parquet"
                    if not path.is_file():
                        raise FileNotFoundError(f"missing R368 completed cache: {path}")
                    curve = pl.read_parquet(path)
                    start_date = date.fromisoformat(start)
                    if curve["date"].min() != start_date or curve["date"].max() != runner.END:
                        raise ValueError(f"R368 curve date coverage mismatch: {name}")
                    if cost == 2:
                        phase_curves[start] = curve
                    curves[name] = curve
            main_2x = phase_curves[runner.START.isoformat()]
            phase_stats = {
                str(start): runner.stats(curve) for start, curve in phase_curves.items()
            }
            recent = runner.segment_return(main_2x, runner.RECENT_START)
            three_x = runner.stats(curves[f"{label}_cap{capacity}_{runner.START.isoformat()}_3x"])
            scorecard[label][str(capacity)] = {
                f"{label}_cap{capacity}_{start}_{cost}x": {
                    "metrics": runner.stats(curves[f"{label}_cap{capacity}_{start}_{cost}x"]),
                    "trades": None,
                }
                for start in PHASES
                for cost in COSTS
            }
            metrics[label][str(capacity)] = {
                "2x": runner.stats(main_2x),
                "3x": three_x,
                "recent_2x": recent,
                "phase_2x": phase_stats,
            }
    return metrics, scorecard


def main() -> int:
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    preflight = json.loads(PRELIGHT.read_text(encoding="utf-8"))
    if not preflight.get("all_checks") or preflight.get("no_return_or_backtest_evaluated") is not True:
        raise RuntimeError("R368 preflight not passed")
    if preflight.get("preregistration_sha256") != sha(PRE_REG):
        raise RuntimeError("R368 preflight refers to different preregistration")
    checksum_path = PRELIGHT.with_name(PRELIGHT.name + ".sha256")
    if checksum_path.read_text(encoding="ascii").split()[0] != sha(PRELIGHT):
        raise RuntimeError("R368 preflight checksum mismatch")
    manifest = load_or_create_manifest()
    r367 = json.loads(R367_RESULT.read_text(encoding="utf-8"))
    metrics, scorecard = load_metrics_from_cache()
    comparisons = compare_capacity_metrics(metrics, r367)
    gates = evaluate_capacity_gates(prereg, metrics, r367)
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R368 reconciled summary already exists; refusing overwrite")
    if FINAL_STATUS.exists() or FINAL_STATUS.with_name(FINAL_STATUS.name + ".sha256").exists() or RESULT_MD.exists():
        raise RuntimeError("R368 reconciled final artifact already exists; refusing overwrite")
    all_gates_pass = all(gates.values())
    result = {
        "round": "R368",
        "classification": "post-run reconciliation summary over the unchanged 48 R368 system-backtest curves",
        "hypothesis": prereg["hypothesis"],
        "rule": prereg["rule"],
        "preregistration_sha256": sha(PRE_REG),
        "preflight_sha256": sha(PRELIGHT),
        "reconciliation_manifest_sha256": sha(MANIFEST),
        "input_data_fingerprints": prereg["frozen_inputs"]["data_fingerprints"],
        "summary_source_curves": "data/research/r368_cache",
        "original_runner_failure": "r368_capacity_backtest_attempt1_failure.json",
        "metrics": metrics,
        "comparisons": comparisons,
        "scorecard": scorecard,
        "gates": gates,
        "all_gates_pass": all_gates_pass,
        "selected": None,
        "decision": {
            "status": "research_only_no_candidate_after_r368" if not all_gates_pass else "research_only_candidate_for_followup",
            "leverage": "none",
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "limits": prereg["known_limits"],
        "summary_only": True,
        "system_backtests_rerun": False,
        "trades_persisted": manifest["trades_persisted"],
        "trade_count_limitation": manifest["trade_count_limitation"],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    status = {
        "round": "R368",
        "status": result["decision"]["status"],
        "all_gates_pass": all_gates_pass,
        "failed_gates": [key for key, value in gates.items() if not value],
        "selected": None,
        "no_leverage": True,
        "paper_target_generated": False,
        "orders_generated": False,
        "production_enabled": False,
        "result_sha256": sha(OUTPUT),
    }
    FINAL_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FINAL_STATUS.with_name(FINAL_STATUS.name + ".sha256").write_text(f"{sha(FINAL_STATUS)}  {FINAL_STATUS.name}\n", encoding="ascii")
    c10 = metrics["candidate"]["10"]
    c20 = metrics["candidate"]["20"]
    q10 = metrics["control"]["10"]
    q20 = metrics["control"]["20"]
    note = [
        "# R368 · 市值中性反转策略持仓容量对照",
        "",
        (
            f"10仓候选年化 {c10['2x']['annual']:.2%}, Sharpe {c10['2x']['sharpe']:.3f}, "
            f"MDD {c10['2x']['mdd']:.2%}, 近一年 {c10['recent_2x']:.2%}；20仓年化 "
            f"{c20['2x']['annual']:.2%}, Sharpe {c20['2x']['sharpe']:.3f}, "
            f"MDD {c20['2x']['mdd']:.2%}, 近一年 {c20['recent_2x']:.2%}。"
        ),
        (
            f"质量对照10/20仓年化 {q10['2x']['annual']:.2%}/{q20['2x']['annual']:.2%}；"
            f"候选相对对照的年化边际 10/20仓 "
            f"{comparisons['candidate_control_edge_10']:+.2%}/"
            f"{comparisons['candidate_control_edge_20']:+.2%}。"
        ),
        (
            f"门槛通过 {sum(gates.values())}/{len(gates)}，selected=null。"
            "48条项目系统回测曲线均由首轮运行生成；本文件只从曲线做汇总，未重跑回测。"
        ),
        "无杠杆、未生产启用。",
        "",
        "证据：`r368_capacity_backtest_reconciled.json`、`r368_reconciliation_manifest.json`、`r368_reconciled_final_status.json`。",
        "",
    ]
    RESULT_MD.write_text("\n".join(note), encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps({"metrics": metrics, "comparisons": comparisons, "gates": gates, "status": status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
