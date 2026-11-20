"""Validate Monte Carlo recommendations and the R360 platform with real backtests.

The runner deliberately reuses the existing factor-set portfolio service and the
frozen R371 core grid. It does not invent a second execution path, and every
result is tagged with a trial id so PBO/DSR can consume the same ledger later.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r372_preregistration.json"
R371 = EVIDENCE / "r371_alpha_parameter_grid.json"
OUTPUT = EVIDENCE / "r372_parameter_validation.json"
RESULT = EVIDENCE / "r372_result.md"
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _replace_members(
    base_scoring: dict[str, float],
    replacements: dict[str, str],
) -> dict[str, float]:
    scoring = dict(base_scoring)
    for old, new in replacements.items():
        if old not in scoring:
            raise ValueError(f"base membership missing replacement slot: {old}")
        weight = scoring.pop(old)
        if new in scoring:
            scoring[new] += weight
        else:
            scoring[new] = weight
    total = sum(scoring.values())
    if abs(total - 1.0) > 1e-12:
        raise ValueError(f"replacement scoring must sum to one: {total}")
    return scoring


def _direction_map(factor_ids: list[str]) -> dict[str, str]:
    # All existing multi_dimension_v1 members use the already-oriented high direction.
    return {factor_id: "high" for factor_id in factor_ids}


def _gate(stats: dict[str, Any], recent: dict[str, Any] | None, *, annual: float, sharpe: float, mdd: float, recent_min: float | None = None) -> dict[str, Any]:
    checks = {
        "annual": float(stats.get("annual_return") or -1.0) >= annual,
        "sharpe": float(stats.get("sharpe") or -1.0) >= sharpe,
        "mdd": float(stats.get("max_drawdown") or -1.0) >= mdd,
    }
    if recent_min is not None:
        checks["recent"] = float((recent or {}).get("total_return") or -1.0) > recent_min
    return {"pass": all(checks.values()), "checks": checks}


def _rolling_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(record["stats"]["total_return"]) for record in records if record.get("error") is None]
    sharpes = [float(record["stats"]["sharpe"]) for record in records if record.get("error") is None]
    mdd = [float(record["stats"]["max_drawdown"]) for record in records if record.get("error") is None]
    if not returns:
        return {"n": 0, "positive_rate": 0.0, "median_return": None, "worst_return": None, "median_sharpe": None, "worst_mdd": None}
    return {
        "n": len(returns),
        "positive_rate": sum(value > 0 for value in returns) / len(returns),
        "median_return": statistics.median(returns),
        "worst_return": min(returns),
        "median_sharpe": statistics.median(sharpes),
        "worst_mdd": min(mdd),
    }


def _core_platform(rows: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any]:
    evaluations = [row for row in rows if row.get("evaluation")]
    if not evaluations:
        raise ValueError("R371 grid has no evaluated rows")
    passed = sum(bool(row.get("joint_pass")) for row in evaluations)
    rate = passed / len(evaluations)
    annuals = [float(row["stats"]["annual"]) for row in evaluations]
    sharpes = [float(row["stats"]["sharpe"]) for row in evaluations]
    mdds = [float(row["stats"]["mdd"]) for row in evaluations]
    groups: dict[str, dict[str, float]] = {}
    for key in ("fade_days", "hold_days", "stop_loss"):
        values: dict[str, list[bool]] = {}
        for row in evaluations:
            values.setdefault(str(row[key]), []).append(bool(row.get("joint_pass")))
        groups[key] = {level: sum(bits) / len(bits) for level, bits in sorted(values.items())}
    minimum_marginal = min(value for group in groups.values() for value in group.values())
    threshold = prereg["evaluation"]["platform"]
    return {
        "source": R371.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(R371),
        "cell_count": len(evaluations),
        "pass_count": passed,
        "pass_rate": rate,
        "minimum_marginal_pass_rate": minimum_marginal,
        "marginal_pass_rates": groups,
        "plateau_pass": rate >= threshold["overall_pass_rate_min"] and minimum_marginal >= threshold["minimum_marginal_pass_rate_min"],
        "annual_range": [min(annuals), max(annuals)],
        "sharpe_range": [min(sharpes), max(sharpes)],
        "mdd_range": [min(mdds), max(mdds)],
    }


def _run_portfolio(prereg: dict[str, Any]) -> dict[str, Any]:
    portfolio = module(ROOT / "scripts/research_factor_set_portfolio.py", "r372_portfolio")
    service = portfolio._service()
    base = prereg["portfolio_line"]["base_weights"]
    runs: dict[str, Any] = {}
    for line in prereg["portfolio_line"]["lines"]:
        scoring = _replace_members(base, line["replacements"])
        directions = _direction_map(list(scoring))
        for window, start_key in (("full", "portfolio_full"), ("recent", "portfolio_recent")):
            start = date.fromisoformat(prereg["periods"][start_key][0])
            end = date.fromisoformat(prereg["periods"][start_key][1])
            for cost in prereg["portfolio_line"]["costs"]:
                trial_id = f"portfolio_{line['id']}_{window}_{cost}"
                record = portfolio._run(
                    service,
                    scoring,
                    directions,
                    label=trial_id,
                    start=start,
                    end=end,
                    max_positions=prereg["portfolio_line"]["max_positions"],
                    cost_label=cost,
                )
                runs[trial_id] = {
                    "trial_id": trial_id,
                    "line": line["id"],
                    "window": window,
                    "cost": cost,
                    "scoring": scoring,
                    "directions": directions,
                    "stats": record["stats"],
                }
    # Reuse the frozen 18 rolling windows for both lines. The baseline portfolio
    # has already been published, so only the MC line is needed here; the baseline
    # is read from that artifact and retained as a control.
    frozen = json.loads((DATA / "factor_research/research/multi_dimension_v1_portfolio_2026-11-10.json").read_text(encoding="utf-8"))
    base_rolling = frozen["rolling_year"]["p20_2x"]
    mc_scoring = _replace_members(base, next(line for line in prereg["portfolio_line"]["lines"] if line["id"] == "mc_recommended")["replacements"])
    mc_records = []
    for item in frozen["periods"]["rolling_year_windows"]:
        start = date.fromisoformat(item["start"])
        end = date.fromisoformat(item["end"])
        record = portfolio._run(
            service,
            mc_scoring,
            _direction_map(list(mc_scoring)),
            label=f"portfolio_mc_recommended_rolling_{start}_2x",
            start=start,
            end=end,
            max_positions=20,
            cost_label="2x",
        )
        mc_records.append(record)
    return {
        "runs": runs,
        "rolling": {
            "base_current_2x": base_rolling["summary"],
            "mc_recommended_2x": _rolling_summary(mc_records),
            "windows": mc_records,
        },
    }


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r371 = json.loads(R371.read_text(encoding="utf-8"))
    portfolio = _run_portfolio(prereg)
    platform = _core_platform(r371["rows"], prereg)
    base = portfolio["runs"]["portfolio_base_current_full_2x"]
    mc = portfolio["runs"]["portfolio_mc_recommended_full_2x"]
    base_recent = portfolio["runs"]["portfolio_base_current_recent_2x"]
    mc_recent = portfolio["runs"]["portfolio_mc_recommended_recent_2x"]
    base_3x = portfolio["runs"]["portfolio_base_current_full_3x"]
    mc_3x = portfolio["runs"]["portfolio_mc_recommended_full_3x"]
    limits = prereg["evaluation"]["portfolio_2x"]
    base_gate = _gate(base["stats"], base_recent["stats"], annual=limits["annual_min"], sharpe=limits["sharpe_min"], mdd=limits["mdd_min"], recent_min=0.0)
    mc_gate = _gate(mc["stats"], mc_recent["stats"], annual=limits["annual_min"], sharpe=limits["sharpe_min"], mdd=limits["mdd_min"], recent_min=0.0)
    base_stress = _gate(base_3x["stats"], None, annual=prereg["evaluation"]["portfolio_3x"]["annual_min"], sharpe=prereg["evaluation"]["portfolio_3x"]["sharpe_min"], mdd=prereg["evaluation"]["portfolio_3x"]["mdd_min"])
    mc_stress = _gate(mc_3x["stats"], None, annual=prereg["evaluation"]["portfolio_3x"]["annual_min"], sharpe=prereg["evaluation"]["portfolio_3x"]["sharpe_min"], mdd=prereg["evaluation"]["portfolio_3x"]["mdd_min"])
    rolling_base = portfolio["rolling"]["base_current_2x"]["positive_rate"]
    rolling_mc = portfolio["rolling"]["mc_recommended_2x"]["positive_rate"]
    rolling_pass = rolling_mc >= prereg["evaluation"]["portfolio_2x"]["rolling_positive_rate_min"]
    return {
        "round": "R372",
        "preregistration_sha256": sha256(PREREG),
        "portfolio_line": portfolio,
        "core_platform": platform,
        "evaluation": {
            "base_current_2x": base_gate,
            "mc_recommended_2x": mc_gate,
            "base_current_3x": base_stress,
            "mc_recommended_3x": mc_stress,
            "rolling_base_2x": rolling_base,
            "rolling_mc_2x": rolling_mc,
            "rolling_mc_pass": rolling_pass,
        },
        "trial_ledger": {
            "trial_count": len(portfolio["runs"]) + len(portfolio["rolling"]["windows"]),
            "trial_ids": sorted(portfolio["runs"]) + [f"portfolio_mc_recommended_rolling_{item['start']}_2x" for item in portfolio["rolling"]["windows"]],
            "pbo_ready": False,
            "deflated_sharpe_ready": False,
            "reason": "The real-backtest trial ledger is recorded, but the historical search ledger is not yet complete; no PBO or DSR claim is made.",
        },
        "decision": {
            "mc_replacement_accepted": mc_gate["pass"] and mc_stress["pass"] and rolling_pass,
            "core_platform_pass": platform["plateau_pass"],
            "selected": None,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    p = report["portfolio_line"]["runs"]
    lines = [
        "# R372 · 蒙特卡洛推荐参数与稳定平台真实回测",
        "",
        "结论只基于本次真实 `open_t+1`、无杠杆回测；没有把 IC 模拟当成策略通过。",
        "",
        "## 8因子组合",
        "",
        "| 组合 | 年化 | Sharpe | MDD | 近一年 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key in ("portfolio_base_current_full_2x", "portfolio_mc_recommended_full_2x", "portfolio_base_current_full_3x", "portfolio_mc_recommended_full_3x"):
        row = p[key]["stats"]
        recent = p.get(key.replace("full", "recent"))
        lines.append(f"| {key} | {row['annual_return']:.2%} | {row['sharpe']:.3f} | {row['max_drawdown']:.2%} | {recent['stats']['total_return']:.2%} |")
    platform = report["core_platform"]
    lines.extend([
        "",
        "## R360 核心平台",
        "",
        f"- R371 复用：{platform['pass_count']}/{platform['cell_count']} 通过，整体 {platform['pass_rate']:.2%}，最低边际通过率 {platform['minimum_marginal_pass_rate']:.2%}。",
        f"- 年化范围 {platform['annual_range'][0]:.2%} 至 {platform['annual_range'][1]:.2%}；Sharpe {platform['sharpe_range'][0]:.3f} 至 {platform['sharpe_range'][1]:.3f}；MDD {platform['mdd_range'][0]:.2%} 至 {platform['mdd_range'][1]:.2%}。",
        f"- 平台门槛：{platform['plateau_pass']}。这不是因为偏好中心点，而是相邻参数层均通过真实回测。",
        "",
        "## 判定",
        "",
        f"- 8因子基线 2x：{report['evaluation']['base_current_2x']['pass']}；MC 替换 2x：{report['evaluation']['mc_recommended_2x']['pass']}。",
        f"- MC 替换 3x 压力：{report['evaluation']['mc_recommended_3x']['pass']}；滚动一年正收益率：{report['evaluation']['rolling_mc_2x']:.2%}。",
        f"- 采纳 MC 替换：{report['decision']['mc_replacement_accepted']}；核心平台：{report['decision']['core_platform_pass']}。",
        "- PBO/DSR 仍未声明通过：本次虽然记录了真实试验，但历史搜索台账不完整。",
        "- 不加杠杆、不生成纸面目标或订单、不启用生产。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        prereg = json.loads(PREREG.read_text(encoding="utf-8"))
        print(json.dumps({"prereg": sha256(PREREG), "lines": len(prereg["portfolio_line"]["lines"])}, ensure_ascii=False))
        return 0
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R372 output exists; refusing overwrite: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
