#!/usr/bin/env python3
"""R400: defensive core plus preregistered beta factors, strict walk-forward."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r400_growth_beta_preregistration.json"
TRAIN_OUT = E / "r400_train_2026-11-22.jsonl"
VALID_OUT = E / "r400_validation_2026-11-22.jsonl"
OUTPUT = E / "r400_growth_beta_2026-11-22.json"
RESULT = E / "r400_growth_beta_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R400 preregistration checksum mismatch")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R400":
        raise RuntimeError("R400 round mismatch")
    return payload


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r400_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index_return(start: str, end: str, symbol: str) -> float | None:
    from app.tickflow.repository import DataStore, KlineRepository
    from datetime import date
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    if frame.is_empty():
        return None
    return float(frame["close"][-1] / frame["close"][0] - 1.0)


def evaluate(module: Any, service: Any, row: dict[str, Any], *, start: str, end: str, cost: str, benchmark: str) -> dict[str, Any]:
    from datetime import date
    config = module._backtest_config(
        row["weights"], {factor: "high" for factor in row["weights"]},
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]), cost=module._cost_parameters(cost),
        max_hold_days=int(row["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {
        "annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"), "total_return": stats.get("total_return"),
        "trades": len(result.trades),
        "hs300_return": _index_return(start, end, benchmark),
    }


def candidates(prereg: dict[str, Any]) -> list[dict[str, Any]]:
    beta = [item["factor_id"] for item in prereg["beta_factors"]]
    rows = []
    for core in prereg["core_candidates"]:
        core_members = core["members"]
        core_weight = float(prereg["weights"]["core_total"])
        for beta_id in beta:
            weights = {factor: core_weight / len(core_members) for factor in core_members}
            weights[beta_id] = float(prereg["weights"]["beta_total"])
            for positions in prereg["portfolio"]["max_positions"]:
                for hold in prereg["portfolio"]["max_hold_days"]:
                    rows.append({
                        "core_id": core["id"], "beta_id": beta_id,
                        "members": list(weights), "weights": weights,
                        "max_positions": positions, "max_hold_days": hold,
                    })
    return rows


def gate(metrics: dict[str, Any], minimum_annual: float, minimum_sharpe: float, minimum_excess: float) -> bool:
    return bool(
        metrics["annual"] is not None and metrics["annual"] >= minimum_annual
        and metrics["sharpe"] is not None and metrics["sharpe"] >= minimum_sharpe
        and metrics["hs300_return"] is not None
        and metrics["total_return"] - metrics["hs300_return"] >= minimum_excess
    )


def run_window(name: str, rows: list[dict[str, Any]], path: Path, boundary: str, prereg: dict[str, Any]) -> None:
    module = portfolio_module()
    service = module._service()
    start = prereg["windows"][f"{boundary}_start"]
    end = prereg["windows"][f"{boundary}_end"]
    minimum_annual = prereg["gate"][f"minimum_{boundary}_annual"]
    minimum_sharpe = prereg["gate"][f"minimum_{boundary}_sharpe"]
    minimum_excess = prereg["gate"]["minimum_hs300_excess"] if boundary == "validation" else -1.0
    with path.open("x", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            metrics = evaluate(module, service, row, start=start, end=end, cost="2x", benchmark=prereg["benchmark"]["symbol"])
            record = {**row, "index": index, "metrics": metrics, "pass": gate(metrics, minimum_annual, minimum_sharpe, minimum_excess)}
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            if index % 20 == 0:
                print(json.dumps({"window": name, "index": index, "core": row["core_id"], "beta": row["beta_id"], "positions": row["max_positions"], "hold": row["max_hold_days"], "annual": metrics["annual"], "sharpe": metrics["sharpe"], "hs300": metrics["hs300_return"], "pass": record["pass"]}), flush=True)


def main() -> int:
    prereg = load_prereg()
    for path in (TRAIN_OUT, VALID_OUT, OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R400 output exists: {path.name}")
    rows = candidates(prereg)
    print(f"R400 grid={len(rows)}", flush=True)
    run_window("train", rows, TRAIN_OUT, "train", prereg)
    train = [json.loads(line) for line in TRAIN_OUT.read_text(encoding="utf-8").splitlines() if line.strip()]
    stable_ids = {(row["core_id"], row["beta_id"]) for row in train if row.get("pass")}
    # Require at least 7/9 execution points for the core/beta pair.
    grouped: dict[tuple[int, str], int] = {}
    for row in train:
        key = (row["core_id"], row["beta_id"])
        grouped[key] = grouped.get(key, 0) + int(bool(row["pass"]))
    stable_ids = {key for key, count in grouped.items() if count >= 7}
    validation_rows = [row for row in rows if (row["core_id"], row["beta_id"]) in stable_ids]
    print(f"R400 training_stable_pairs={len(stable_ids)} validation_points={len(validation_rows)}", flush=True)
    run_window("validation", validation_rows, VALID_OUT, "validation", prereg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
