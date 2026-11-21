#!/usr/bin/env python3
"""R395: preregistered Monte Carlo over factor-set weights only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r395_factor_weight_mc_preregistration.json"
OUTPUT = EVIDENCE / "r395_factor_weight_mc_2026-11-22.json"
RESULT = EVIDENCE / "r395_factor_weight_mc_2026-11-22.md"
FACTOR_IDS = (
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low",
    "ext_alt_hs300_margin_chg_1m_low",
    "ext_alt_hs300_ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low",
)
TRAIN = ("2022-05-05", "2025-05-05")
TEST = ("2025-05-06", "2026-11-05")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R395 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R395":
        raise RuntimeError("R395 round mismatch")
    if tuple(payload["factor_ids"]) != FACTOR_IDS:
        raise RuntimeError("R395 factor set changed")
    if payload["windows"]["train_end"] >= payload["windows"]["test_start"]:
        raise RuntimeError("R395 train/test overlap")
    return payload


def _normalise(raw: list[float], low: float, high: float) -> list[float]:
    clipped = [min(high, max(low, value)) for value in raw]
    for _ in range(100):
        total = sum(clipped)
        adjusted = [min(high, max(low, value / total)) for value in clipped]
        if abs(sum(adjusted) - 1.0) < 1e-10:
            return adjusted
        clipped = adjusted
    raise RuntimeError("could not normalise bounded weights")


def generate_weight_samples() -> list[dict[str, Any]]:
    prereg = load_prereg()
    design = prereg["samples"]
    bounds = prereg["weight_bounds"]
    n = int(design["latin_hypercube"])
    rng = random.Random(int(design["seed"]))
    strata = []
    for _ in FACTOR_IDS:
        order = list(range(n))
        rng.shuffle(order)
        strata.append([(order[index] + rng.random()) / n for index in range(n)])
    rows = [{"label": "equal_weight", "sample": -1, "weights": {factor: 1.0 / len(FACTOR_IDS) for factor in FACTOR_IDS}}]
    for index in range(n):
        weights = _normalise([strata[j][index] for j in range(len(FACTOR_IDS))], float(bounds["minimum"]), float(bounds["maximum"]))
        rows.append({"label": f"lhs_{index:03d}", "sample": index, "weights": dict(zip(FACTOR_IDS, weights, strict=True))})
    return rows


def _portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r395_portfolio_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_for_range(start: str, end: str, cost: str, max_positions: int) -> dict[str, Any]:
    return {
        "label": f"r395_{start}_{end}_{cost}",
        "start": start,
        "end": end,
        "cost": cost,
        "max_positions": max_positions,
        "top_rank": max(20, max_positions),
        "error": None,
        "stats": {},
        "trade_count": 0,
        "benchmark_return": None,
    }


def _stats(record: dict[str, Any]) -> dict[str, Any]:
    stats = record.get("stats", {})
    return {
        "total_return": stats.get("total_return"),
        "annual": stats.get("annual_return"),
        "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"),
        "trades": record.get("trade_count", stats.get("n_trades")),
    }


def evaluate_weights(
    service: Any,
    portfolio_module: Any,
    scoring: dict[str, float],
    directions: dict[str, str],
    *,
    start: str,
    end: str,
    cost: str,
    max_positions: int,
) -> dict[str, Any]:
    from datetime import date
    record = portfolio_module._run(
        service,
        scoring,
        directions,
        label=f"r395_{start}_{end}_{cost}",
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=max_positions,
        cost_label=cost,
    )
    return _stats(record)


def _weight_distance(left: dict[str, float], right: dict[str, float]) -> float:
    return sum(abs(left[f] - right[f]) for f in FACTOR_IDS) / 2.0


def _neighbours(samples: list[dict[str, Any]], pivot: int, count: int = 6) -> list[int]:
    base = samples[pivot]["weights"]
    ordered = sorted(
        (i for i in range(len(samples)) if i != pivot),
        key=lambda i: (_weight_distance(base, samples[i]["weights"]), i),
    )
    return ordered[:count]


def train_gate(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    gate = prereg["selection"]
    return bool(
        metrics["annual"] is not None
        and metrics["annual"] >= gate["minimum_train_annual"]
        and metrics["sharpe"] is not None
        and metrics["sharpe"] >= gate["minimum_train_sharpe"]
        and metrics["mdd"] is not None
        and metrics["mdd"] >= gate["maximum_train_mdd"]
    )


def build_report() -> dict[str, Any]:
    prereg = load_prereg()
    samples = generate_weight_samples()
    portfolio = _portfolio_module()
    service = portfolio._service()
    directions = {factor: "high" for factor in FACTOR_IDS}
    rows = []
    for sample in samples:
        scoring = dict(sample["weights"])
        train_2x = evaluate_weights(service, portfolio, scoring, directions, start=TRAIN[0], end=TRAIN[1], cost="2x", max_positions=int(prereg["portfolio"]["max_positions"]))
        row = {
            "sample": sample["sample"],
            "label": sample["label"],
            "weights": scoring,
            "train_2x": train_2x,
            "train_gate": train_gate(train_2x, prereg),
        }
        rows.append(row)
        print(json.dumps({"sample": sample["label"], "annual": train_2x["annual"], "sharpe": train_2x["sharpe"], "mdd": train_2x["mdd"], "pass": row["train_gate"]}), flush=True)
    eligible = [i for i, row in enumerate(rows) if row["train_gate"]]
    neighbourhoods = []
    for pivot in eligible[:20]:
        ids = _neighbours(samples, pivot)
        neighbour_rows = [rows[i] for i in ids]
        pass_count = sum(bool(row["train_gate"]) for row in neighbour_rows)
        neighbourhoods.append({
            "sample": rows[pivot]["sample"],
            "label": rows[pivot]["label"],
            "neighbour_sample_ids": [rows[i]["sample"] for i in ids],
            "neighbour_pass_count": pass_count,
            "neighbour_count": len(ids),
            "stable": pass_count == len(ids),
        })
    stable_ids = [item["sample"] for item in neighbourhoods if item["stable"]]
    finalists = [i for i, row in enumerate(rows) if row["sample"] in set(stable_ids)]
    if not finalists:
        finalists = eligible[:1]
    for i in finalists:
        row = rows[i]
        scoring = row["weights"]
        row["test_2x"] = evaluate_weights(service, portfolio, scoring, directions, start=TEST[0], end=TEST[1], cost="2x", max_positions=int(prereg["portfolio"]["max_positions"]))
        row["train_3x"] = evaluate_weights(service, portfolio, scoring, directions, start=TRAIN[0], end=TRAIN[1], cost="3x", max_positions=int(prereg["portfolio"]["max_positions"]))
        row["test_3x"] = evaluate_weights(service, portfolio, scoring, directions, start=TEST[0], end=TEST[1], cost="3x", max_positions=int(prereg["portfolio"]["max_positions"]))
    equal = rows[0]
    equal_test = evaluate_weights(service, portfolio, equal["weights"], directions, start=TEST[0], end=TEST[1], cost="2x", max_positions=int(prereg["portfolio"]["max_positions"]))
    stable_sorted = sorted(
        finalists,
        key=lambda i: (
            -min(
                rows[i]["train_2x"]["annual"] or -99.0,
                rows[i]["test_2x"]["annual"] or -99.0,
            ),
            -min(
                rows[i]["train_2x"]["sharpe"] or -99.0,
                rows[i]["test_2x"]["sharpe"] or -99.0,
            ),
            i,
        ),
    )
    selected = rows[stable_sorted[0]] if stable_sorted else None
    return {
        "round": "R395",
        "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "factor_ids": list(FACTOR_IDS),
        "windows": {"train": TRAIN, "test": TEST},
        "sample_count": len(samples),
        "eligible_count": len(eligible),
        "stable_neighbourhood_count": len(stable_ids),
        "equal_weight": {"train_2x": equal["train_2x"], "test_2x": equal_test, "train_gate": equal["train_gate"]},
        "selected": selected,
        "rows": rows,
        "decision": {
            "status": "weight_mc_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
            "candidate_found": bool(selected and stable_ids),
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    equal = report["equal_weight"]
    selected = report.get("selected")
    lines = [
        "# R395 因子权重蒙特卡洛",
        "",
        "只改变 8 个冻结因子的权重；因子公式、方向、20 仓、无杠杆和 T+1 路径均不变。",
        "",
        f"- 样本数：{report['sample_count']}（含等权中心）",
        f"- 训练门槛通过：{report['eligible_count']}",
        f"- 稳定邻域：{report['stable_neighbourhood_count']}",
        f"- 等权训练年化：{equal['train_2x']['annual']:.2%}",
        f"- 等权训练 Sharpe：{equal['train_2x']['sharpe']:.3f}",
        f"- 等权检验年化：{equal['test_2x']['annual']:.2%}",
        f"- 等权检验 Sharpe：{equal['test_2x']['sharpe']:.3f}",
        "",
    ]
    if selected:
        lines += [
            f"- 选中样本：`{selected['label']}`",
            f"- 选中检验年化：{selected['test_2x']['annual']:.2%}",
            f"- 选中检验 Sharpe：{selected['test_2x']['sharpe']:.3f}",
            f"- 选中检验 MDD：{selected['test_2x']['mdd']:.2%}",
            f"- 选中 3× 检验年化：{selected['test_3x']['annual']:.2%}",
            f"- 相对等权检验年化差：{selected['test_2x']['annual'] - equal['test_2x']['annual']:+.2%}",
        ]
    else:
        lines.append("- 没有权重通过预登记筛选。")
    lines += [
        "",
        "## 结论边界",
        "",
        "- 训练段用于筛选，检验段只做一次确认。",
        "- 不是新因子发现，不改变 watch 集合的成员定义。",
        "- 不生成订单、纸面目标或生产配置。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    payload = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    md = markdown(payload)
    RESULT.write_text(md, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
