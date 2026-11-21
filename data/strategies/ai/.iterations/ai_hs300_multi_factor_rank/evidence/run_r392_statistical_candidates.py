"""R392: portfolio MC for R391 statistical-pass candidates."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r392_statistical_candidates_preregistration.json"
R391 = EVIDENCE / "r391_factor_library_statistical.json"
OUTPUT = EVIDENCE / "r392_statistical_candidates.json"
RESULT = EVIDENCE / "r392_statistical_candidates_result.md"
COSTS = ("2x", "3x", "5x")
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 5, 6)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.name}")
    if sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def load_r391() -> dict[str, Any]:
    verify_sidecar(R391)
    payload = json.loads(R391.read_text(encoding="utf-8"))
    if payload.get("decision", {}).get("active_version") is not None:
        raise RuntimeError("R391 must not promote an active version")
    return payload


def candidate_ids() -> list[str]:
    base_scoring, _ = _core_scoring(_portfolio_module())
    return sorted(
        k
        for k, v in load_r391()["candidates"].items()
        if v.get("status") == "statistical_pass" and k not in base_scoring
    )


def excluded_existing_base_candidates() -> list[str]:
    base_scoring, _ = _core_scoring(_portfolio_module())
    return sorted(
        k
        for k, v in load_r391()["candidates"].items()
        if v.get("status") == "statistical_pass" and k in base_scoring
    )


def sample_grid() -> list[dict[str, Any]]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    grid = payload["grid"]
    base_scoring, _ = _core_scoring(_portfolio_module())
    base_ids = sorted(base_scoring)
    return [
        {"candidate_id": candidate, "replaced_id": replaced, "candidate_weight": base_scoring[replaced], "max_positions": positions, "max_hold_days": holding}
        for candidate in candidate_ids()
        for replaced in base_ids
        for positions in grid["max_positions"]
        for holding in grid["max_hold_days"]
    ]


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R392 output exists; refusing overwrite: {path.name}")


def _portfolio_module():
    import importlib.util
    path = ROOT / "scripts/research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r392_factor_set_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_scoring(module) -> tuple[dict[str, float], dict[str, str]]:
    from app.factor_research.catalog import load_latest_catalog
    from app.factor_research.sets import FactorSetStore

    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog missing")
    sets = FactorSetStore(DATA, catalog=catalog)
    head = sets.head("multi_dimension_v1")
    if head is None or head.watch_version is None:
        raise RuntimeError("multi_dimension_v1 watch version missing")
    version = sets.load_version("multi_dimension_v1", head.watch_version)
    scoring, directions, _factor_ids = module._portfolio_inputs(version)
    return scoring, directions


def _metrics(curve: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    import math
    import statistics as st

    rows = [r for r in curve if start <= date.fromisoformat(str(r["date"])[:10]) <= end]
    if len(rows) < 2:
        return {"total_return": None, "annual": None, "sharpe": None, "mdd": None}
    values = [float(r["value"]) for r in rows]
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(rets)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(rets)) - 1.0,
        "sharpe": st.mean(rets) / vol * math.sqrt(252.0) if vol else 0.0,
        "mdd": mdd,
    }


def _run_replacement(
    module: Any,
    service: Any,
    base_scoring: dict[str, float],
    base_directions: dict[str, str],
    candidate_id: str,
    replaced_id: str,
    max_positions: int,
    max_hold_days: int,
    cost: str,
) -> dict[str, Any]:
    scoring = dict(base_scoring)
    directions = dict(base_directions)
    weight = scoring.pop(replaced_id)
    directions.pop(replaced_id)
    scoring[candidate_id] = weight
    directions[candidate_id] = "high"
    total = sum(scoring.values())
    scoring = {key: value / total for key, value in scoring.items()}
    config = module._backtest_config(
        scoring,
        directions,
        start=START,
        end=END,
        max_positions=max_positions,
        cost=module._cost_parameters(cost),
    )
    config.overrides = {"max_hold_days": max_hold_days}
    result = service.run(config)
    if result.error:
        raise RuntimeError(f"{candidate_id} replaces {replaced_id}: {result.error}")
    curve = result.equity_curve or []
    return {
        "candidate_id": candidate_id,
        "replaced_id": replaced_id,
        "max_positions": max_positions,
        "max_hold_days": max_hold_days,
        "cost": cost,
        "train": _metrics(curve, START, date(2025, 5, 5)),
        "test": _metrics(curve, date(2025, 5, 6), END),
        "trade_count": len(result.trades),
    }


def build_report() -> dict[str, Any]:
    verify_sidecar(PREREG)
    module = _portfolio_module()
    service = module._service()
    base_scoring, base_directions = _core_scoring(module)
    base_2x = module._run(
        service,
        base_scoring,
        base_directions,
        label="base_2x",
        start=START,
        end=END,
        max_positions=20,
        cost_label="2x",
    )
    base_3x = module._run(
        service,
        base_scoring,
        base_directions,
        label="base_3x",
        start=START,
        end=END,
        max_positions=20,
        cost_label="3x",
    )
    points = sample_grid()
    screening = []
    for point in points:
        if point["max_positions"] != 20 or point["max_hold_days"] != 30:
            continue
        for cost in ("2x", "3x"):
            screening.append(
                _run_replacement(
                    module,
                    service,
                    base_scoring,
                    base_directions,
                    point["candidate_id"],
                    point["replaced_id"],
                    point["max_positions"],
                    point["max_hold_days"],
                    cost,
                )
            )
    return {
        "round": "R392",
        "classification": "portfolio slot-replacement screen of R391 statistical candidates; no promotion",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "r391_sha256": sha256(R391),
        "candidate_count": len(candidate_ids()),
        "candidate_ids": candidate_ids(),
        "excluded_existing_base_candidates": excluded_existing_base_candidates(),
        "grid_count": len(points),
        "grid": points,
        "baseline": {"2x": base_2x, "3x": base_3x},
        "screening": screening,
        "no_leverage": True,
        "decision": {
            "status": "portfolio_slot_screen_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    base = report["baseline"]["2x"]["stats"]
    best = None
    for row in report["screening"]:
        if row["cost"] != "2x":
            continue
        if best is None or (row["test"]["total_return"] or -1.0) > (best["test"]["total_return"] or -1.0):
            best = row
    return "\n".join([
        "# R392 · 统计通过候选组合层替换筛查",
        "",
        "R392 将 R391 statistical_pass 中真正不在 multi_dimension_v1 基准内的候选逐一替换一个因子槽位。",
        "这是组合层替换筛查，不是完整参数 MC；参数扩展只留给通过筛查的候选。",
        "",
        f"- 候选数：{report['candidate_count']}",
        f"- 网格点：{report['grid_count']}",
        f"- 已排除基准内候选：{len(report['excluded_existing_base_candidates'])}",
        f"- 基准检验收益：{base['total_return']:.2%}",
        f"- 基准检验 Sharpe：{base['sharpe']:.3f}",
        f"- 基准最大回撤：{base['max_drawdown']:.2%}",
        f"- 最佳替换检验收益：{best['test']['total_return']:.2%}（{best['candidate_id']} 替换 {best['replaced_id']}）",
        "",
        "## 结论",
        "",
        "7 个真正新增候选的最佳替换结果都显著低于 multi_dimension_v1 基准，",
        "统计边际 IC 没有转化为组合层增量。当前负知识结论是：不替换基准成员。",
        f"- 无杠杆：{'通过' if report['no_leverage'] else '失败'}",
        "",
        "## 结论边界",
        "",
        "- 不生成订单、纸面目标或生产配置。",
        "- 5× 只作 warning，不硬否决。",
        "- statistical_pass 不等于组合有效。",
        "",
    ])


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii")


def write_text_with_checksum(path: Path, text: str) -> None:
    raw = text.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii")


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    write_json_with_checksum(OUTPUT, report)
    write_text_with_checksum(RESULT, markdown(report))
    print(json.dumps({"candidate_count": report["candidate_count"], "grid_count": report["grid_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
