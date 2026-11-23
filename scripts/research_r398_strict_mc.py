#!/usr/bin/env python3
"""R398: strict train/validation/test factor-subset and execution Monte Carlo."""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r398_holdout_preregistration.json"
TRAIN_OUT = E / "r398_train_2026-11-22.jsonl"
VALIDATION_OUT = E / "r398_validation_2026-11-22.jsonl"
OUTPUT = E / "r398_strict_mc_2026-11-22.json"
RESULT = E / "r398_strict_mc_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R398 preregistration checksum mismatch")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R398":
        raise RuntimeError("R398 round mismatch")
    windows = payload["windows"]
    if not (windows["train_end"] < windows["validation_start"] and windows["validation_end"] < windows["test_start"]):
        raise RuntimeError("R398 windows overlap")
    return payload


def verify_outputs_absent(paths: list[Path]) -> None:
    for path in paths:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R398 output exists; refusing overwrite: {path.name}")


def subsets(ids: list[str], minimum: int = 3, maximum: int = 7) -> list[list[str]]:
    return [list(combo) for size in range(minimum, maximum + 1) for combo in itertools.combinations(ids, size)]


def grid(prereg: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    design = prereg["design"]
    return [{"members": members, "weights": {f: 1 / len(members) for f in members}, "max_positions": positions, "max_hold_days": hold}
            for members in subsets(ids)
            for positions in design["max_positions"]
            for hold in design["max_hold_days"]]


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r398_portfolio", path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def evaluate(module: Any, service: Any, row: dict[str, Any], start: str, end: str, cost: str) -> dict[str, Any]:
    from datetime import date
    config = module._backtest_config(
        row["weights"], {f: "high" for f in row["members"]},
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]), cost=module._cost_parameters(cost),
        max_hold_days=int(row["max_hold_days"]),
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r398_strict_mc.py")
    if result.error:
        raise RuntimeError(f"{row['members']} {row['max_positions']} {row['max_hold_days']}: {result.error}")
    stats = result.stats
    return {"annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"), "mdd": stats.get("max_drawdown"), "trades": len(result.trades)}


def gate(metrics: dict[str, Any], g: dict[str, Any]) -> bool:
    return bool(metrics["annual"] is not None and metrics["annual"] >= g["annual_min"] and metrics["sharpe"] is not None and metrics["sharpe"] >= g["sharpe_min"] and metrics["mdd"] is not None and metrics["mdd"] >= g["mdd_min"])


def run_window(window: str, rows: list[dict[str, Any]], output: Path, start: str, end: str, prereg: dict[str, Any]) -> dict[str, Any]:
    module = portfolio_module()
    service = module._service()
    gate_config = prereg["design"][f"{window}_gate"]
    with output.open("x", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            metrics = evaluate(module, service, row, start, end, "2x")
            record = {**row, "index": index, f"{window}_2x": metrics, "pass": gate(metrics, gate_config)}
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            if index % 100 == 0:
                print(json.dumps({"window": window, "index": index, "members": len(row["members"]), "positions": row["max_positions"], "hold": row["max_hold_days"], "annual": metrics["annual"], "sharpe": metrics["sharpe"], "pass": record["pass"]}), flush=True)
    return {"window": window, "rows": len(rows), "pass_count": None}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    prereg = verify_prereg()
    verify_outputs_absent([TRAIN_OUT, VALIDATION_OUT, OUTPUT])
    rows = grid(prereg, prereg["factor_ids"])
    print(f"R398 grid={len(rows)}", flush=True)
    run_window("train", rows, TRAIN_OUT, prereg["windows"]["train_start"], prereg["windows"]["train_end"], prereg)
    train_rows = load_jsonl(TRAIN_OUT)
    # Validation only sees candidates that are training-stable.
    by_members: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in train_rows:
        by_members.setdefault(tuple(row["members"]), []).append(row)
    stable_members = {members for members, group in by_members.items() if len(group) == 9 and all(row["pass"] for row in group)}
    validation_rows = [row for row in rows if tuple(row["members"]) in stable_members]
    print(f"R398 train_stable_subsets={len(stable_members)} validation_points={len(validation_rows)}", flush=True)
    run_window("validation", validation_rows, VALIDATION_OUT, prereg["windows"]["validation_start"], prereg["windows"]["validation_end"], prereg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
