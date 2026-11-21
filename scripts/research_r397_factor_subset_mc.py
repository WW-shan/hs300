#!/usr/bin/env python3
"""R397: training-only subset and portfolio grid Monte Carlo."""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r397_factor_subset_preregistration.json"
OUTPUT = EVIDENCE / "r397_factor_subset_mc_2026-11-22.json"
RESULT = EVIDENCE / "r397_factor_subset_mc_2026-11-22.md"
RUNS = EVIDENCE / "r397_runs" / "train_runs.jsonl"
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
            raise RuntimeError(f"R397 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R397":
        raise RuntimeError("R397 round mismatch")
    if payload["windows"]["train_end"] >= payload["windows"]["test_start"]:
        raise RuntimeError("R397 train/test overlap")
    return payload


def subsets(factor_ids: list[str]) -> list[list[str]]:
    prereg = load_prereg()
    minimum = int(prereg["subset_search"]["minimum_members"])
    maximum = int(prereg["subset_search"]["maximum_members"])
    return [list(combo) for size in range(minimum, maximum + 1) for combo in itertools.combinations(factor_ids, size)]


def equal_weights(members: list[str]) -> dict[str, float]:
    return {factor: 1.0 / len(members) for factor in members}


def combinations(prereg: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    grid = prereg["portfolio"]
    return [{"members": members, "weights": equal_weights(members), "max_positions": positions, "holding_days": holding}
            for members in subsets(ids) for positions in grid["max_positions"] for holding in grid["holding_days"]]


def _portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r397_portfolio_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(module: Any, service: Any, row: dict[str, Any], *, start: str, end: str, cost: str) -> dict[str, Any]:
    from datetime import date
    config = module._backtest_config(
        row["weights"],
        {f: "high" for f in row["members"]},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]),
        cost=module._cost_parameters(cost),
    )
    config.holding_days = int(row["holding_days"])
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {
        "annual": stats.get("annual_return"),
        "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"),
        "total_return": stats.get("total_return"),
        "trades": len(result.trades),
    }


def train_gate(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    g = prereg["selection_gate"]
    return bool(metrics["annual"] is not None and metrics["annual"] >= g["minimum_train_annual"] and metrics["sharpe"] is not None and metrics["sharpe"] >= g["minimum_train_sharpe"] and metrics["mdd"] is not None and metrics["mdd"] >= g["maximum_train_mdd"])


def build_report() -> dict[str, Any]:
    prereg = load_prereg()
    ids = list(prereg["factor_ids"])
    rows = combinations(prereg, ids)
    module = _portfolio_module()
    service = module._service()
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("x", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            metrics = evaluate(module, service, row, start=TRAIN[0], end=TRAIN[1], cost="2x")
            row.update({"index": index, "train_2x": metrics, "train_gate": train_gate(metrics, prereg)})
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            if index % 20 == 0:
                print(json.dumps({"index": index, "members": len(row["members"]), "positions": row["max_positions"], "holding": row["holding_days"], "annual": metrics["annual"], "sharpe": metrics["sharpe"], "pass": row["train_gate"]}), flush=True)
    eligible = [row for row in rows if row["train_gate"]]
    # Rank training-only; no test data is used here.
    eligible.sort(key=lambda row: (-(row["train_2x"]["annual"] or -99.0), -(row["train_2x"]["sharpe"] or -99.0), bool(row["train_gate"]), row["index"]))
    return {
        "round": "R397", "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG),
        "windows": {"train": TRAIN, "test": TEST}, "grid_count": len(rows),
        "eligible_count": len(eligible), "subsets": rows,
        "best_training": eligible[:20],
        "decision": {"status": "subset_mc_training_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }


def main() -> int:
    ensure_outputs_absent()
    payload = build_report()
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(f"grid={payload['grid_count']} eligible={payload['eligible_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
