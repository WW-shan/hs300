#!/usr/bin/env python3
"""R401: lagged-regime conditional beta activation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r401_regime_beta_preregistration.json"
REGIME = DATA / "regime_history" / "part.parquet"
TRAIN = E / "r401_train_2026-11-22.jsonl"
VALID = E / "r401_validation_2026-11-22.jsonl"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R401 preregistration checksum mismatch")
    return json.loads(PREREG.read_text(encoding="utf-8"))


def regime_frame() -> pl.DataFrame:
    frame = pl.read_parquet(REGIME).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    frame = frame.with_columns([
        pl.col("state").shift(1).alias("state_lag1"),
        pl.col("trend_score").shift(1).alias("trend_score_lag1"),
        pl.col("above_ma20_pct").shift(1).alias("breadth_lag1"),
        pl.col("score").rolling_mean(20).shift(1).alias("score_ma20_lag1"),
    ])
    return frame.select(["date", "state_lag1", "trend_score_lag1", "breadth_lag1", "score_ma20_lag1"])


def activation(frame: pl.DataFrame, rule: str) -> pl.DataFrame:
    if rule == "always_on":
        expression = pl.lit(True)
    elif rule == "strong_or_lean_strong":
        expression = pl.col("state_lag1").is_in(["strong", "lean_strong"])
    elif rule == "strong_only":
        expression = pl.col("state_lag1") == "strong"
    elif rule == "trend_score_50":
        expression = pl.col("trend_score_lag1") >= 50.0
    elif rule == "breadth_50":
        expression = pl.col("breadth_lag1") >= 0.5
    elif rule == "score_trend":
        expression = (pl.col("score_ma20_lag1") >= 55.0) & (pl.col("trend_score_lag1") >= 50.0)
    else:
        raise ValueError(f"unknown R401 rule: {rule}")
    return frame.with_columns(expression.fill_null(False).alias("beta_on"))


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r401_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index_return(start: str, end: str, symbol: str) -> float | None:
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA))
    frame = repo.get_index_daily(symbol, date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
    return None if frame.is_empty() else float(frame["close"][-1] / frame["close"][0] - 1.0)


def candidate_rows(prereg: dict[str, Any], on_days: set[str], rule: str) -> list[dict[str, Any]]:
    core = prereg["core"]
    beta = prereg["beta"]["factor_id"]
    rows = []
    for positions in prereg["portfolio"]["max_positions"]:
        for hold in prereg["portfolio"]["max_hold_days"]:
            for mode in ("on", "off"):
                members = list(core["members"])
                weights = {factor: float(prereg["beta"]["core_active_weight"]) / len(members) for factor in members}
                if mode == "on":
                    members.append(beta)
                    weights[beta] = float(prereg["beta"]["beta_active_weight"])
                rows.append({"rule": rule, "mode": mode, "members": members, "weights": weights, "max_positions": positions, "max_hold_days": hold})
    return rows


def run_window(name: str, rows: list[dict[str, Any]], path: Path, start: str, end: str, prereg: dict[str, Any]) -> None:
    module = portfolio_module()
    service = module._service()
    with path.open("x", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            config = module._backtest_config(
                row["weights"], {factor: "high" for factor in row["weights"]},
                start=date.fromisoformat(start), end=date.fromisoformat(end),
                max_positions=int(row["max_positions"]), cost=module._cost_parameters("2x"),
                max_hold_days=int(row["max_hold_days"]),
            )
            result = service.run_with_evidence(config)
            require_formal_evidence(result, label="research_r401_regime_beta.py")
            if result.error:
                raise RuntimeError(result.error)
            stats = result.stats
            metrics = {
                "annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"),
                "mdd": stats.get("max_drawdown"), "total_return": stats.get("total_return"),
                "trades": len(result.trades), "hs300_return": index_return(start, end, prereg["benchmark"]["symbol"]),
            }
            # Metric gate is applied only in finalize after state-composed curves.
            stream.write(json.dumps({**row, "index": index, "metrics": metrics}, ensure_ascii=False, sort_keys=True) + "\n")
            if index % 20 == 0:
                print(json.dumps({"window": name, "index": index, "rule": row["rule"], "positions": row["max_positions"], "hold": row["max_hold_days"], "annual": metrics["annual"], "sharpe": metrics["sharpe"]}), flush=True)


def main() -> int:
    prereg = load_prereg()
    for path in (TRAIN, VALID):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R401 output exists: {path.name}")
    # One row per rule/mode/execution point. The state-conditioned result is
    # composed later from the matching on/off curves.
    rows = []
    for rule in prereg["regime_rules"]:
        for positions in prereg["portfolio"]["max_positions"]:
            for hold in prereg["portfolio"]["max_hold_days"]:
                rows.append({"rule": rule, "members": list(prereg["core"]["members"]), "weights": {f: 1.0 / len(prereg["core"]["members"]) for f in prereg["core"]["members"]}, "max_positions": positions, "max_hold_days": hold})
    print(f"R401 grid={len(rows)}", flush=True)
    run_window("train", rows, TRAIN, prereg["windows"]["train_start"], prereg["windows"]["train_end"], prereg)
    # Validation only evaluates the training-stable rules later; initially run all for audit.
    run_window("validation", rows, VALID, prereg["windows"]["validation_start"], prereg["windows"]["validation_end"], prereg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
