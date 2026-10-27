#!/usr/bin/env python3
"""R265: run A/B parity against the R131-era alt_hs300 snapshot."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXT = DATA / "ext_data" / "alt_hs300"
BACKUP = DATA / "research" / "r265_backup_alt_hs300"
SNAPSHOT = DATA / "research" / "r265_rebuild_partial"
FROZEN = DATA / "research" / "r265_frozen_alt_hs300"
CACHE = DATA / "research" / "r265_cache"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r265_r131_snapshot_parity.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
A_SID = "ai_hs300_macro_gate_quality"
B_SID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
CACHE_A = DATA / "research" / "r125_cache" / f"{A_SID}_2021-06-01_2026-10-16_3x.parquet"
CACHE_B = DATA / "research" / "r119_cache" / f"{B_SID}_2021-06-01_2026-10-16.parquet"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _assert_within(path: Path, root: Path) -> None:
    p = path.resolve(); r = root.resolve()
    if p != r and r not in p.parents:
        raise RuntimeError(f"path escapes workspace root: {p}")


def _signature(path: Path) -> dict:
    files = sorted(path.rglob("*.parquet"))
    total = sum(f.stat().st_size for f in files)
    h = hashlib.sha256()
    cfg = path / "config.json"
    if cfg.exists():
        h.update(cfg.read_bytes())
    if files:
        h.update(files[-1].read_bytes())
    return {"files": len(files), "bytes": total, "config_and_latest_hash": h.hexdigest()[:32]}


def _run_sleeve(service, membership, r112, strategy_id: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    curve_path = CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}.parquet"
    cfg = r112.StrategyBacktestConfig(
        strategy_id=strategy_id, symbols=None, start=START, end=END,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id}: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    curve.write_parquet(curve_path)
    print(f"ran {strategy_id}: days={curve.height} trades={len(result.trades)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    return curve


def _parity(cached_path: Path, rebuilt: pl.DataFrame) -> dict:
    cached = pl.read_parquet(cached_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    c0 = float(cached["value"][0]); r0 = float(rebuilt["value"][0])
    joined = cached.rename({"value": "cached"}).join(rebuilt.rename({"value": "rebuilt"}), on="date", how="inner").sort("date")
    joined = joined.with_columns((pl.col("cached") / c0).alias("cached_rebased"), (pl.col("rebuilt") / r0).alias("rebuilt_rebased"))
    diff = (joined["cached_rebased"] - joined["rebuilt_rebased"]).abs()
    return {
        "days": joined.height,
        "cached_total_return": round(float(cached["value"][-1] / cached["value"][0] - 1.0), 6),
        "rebuilt_total_return": round(float(rebuilt["value"][-1] / rebuilt["value"][0] - 1.0), 6),
        "max_abs_diff": round(float(diff.max() or 0.0), 12),
        "mean_abs_diff": round(float(diff.mean() or 0.0), 12),
        "pass": bool(joined.height > 0 and float(diff.max() or 0.0) <= 1e-6),
    }


def main() -> int:
    for p in (EXT, BACKUP, SNAPSHOT, FROZEN):
        _assert_within(p, DATA)
    if not EXT.exists() or not SNAPSHOT.exists():
        raise RuntimeError("missing EXT or SNAPSHOT")
    if BACKUP.exists() or FROZEN.exists():
        raise RuntimeError("stale backup/frozen dir exists")

    evidence = {"round": "R265", "purpose": "A/B parity against the R131-era alt_hs300 snapshot rebuilt from commit 0c196090."}
    try:
        shutil.move(str(EXT), str(BACKUP))
        shutil.move(str(SNAPSHOT), str(EXT))
        evidence["snapshot_signature"] = _signature(EXT)

        r112 = _load(R112_SCRIPT, "r112_r265b")
        membership = r112._historical_membership()
        service = r112._service()
        a_curve = _run_sleeve(service, membership, r112, A_SID)
        b_curve = _run_sleeve(service, membership, r112, B_SID)
        a_parity = _parity(CACHE_A, a_curve)
        b_parity = _parity(CACHE_B, b_curve)
        evidence["A_parity"] = a_parity
        evidence["B_parity"] = b_parity
        evidence["P1"] = True
        evidence["P2"] = a_parity["pass"]
        evidence["P3"] = b_parity["pass"]

        if a_parity["pass"] and b_parity["pass"]:
            shutil.move(str(EXT), str(FROZEN))
            evidence["frozen_snapshot"] = str(FROZEN.relative_to(ROOT))
            evidence["frozen_signature"] = _signature(FROZEN)
        else:
            failed = DATA / "research" / "r265_r131_rebuild_failed"
            if failed.exists():
                failed = DATA / "research" / f"r265_r131_rebuild_failed_{int(time.time())}"
            shutil.move(str(EXT), str(failed))
            evidence["failed_rebuild"] = str(failed.relative_to(ROOT))
        shutil.move(str(BACKUP), str(EXT))
        evidence["restored"] = True
    finally:
        if BACKUP.exists():
            if EXT.exists():
                abandoned = DATA / "research" / f"r265_abandoned_{int(time.time())}"
                shutil.move(str(EXT), str(abandoned))
            shutil.move(str(BACKUP), str(EXT))
            evidence["restored"] = True
    evidence["P4"] = evidence.get("restored", False)
    evidence["all_pass"] = all(bool(evidence.get(k)) for k in ("P1", "P2", "P3", "P4"))
    evidence["decision"] = (
        "R131 reproduced from the R131-era snapshot; proceed to trade-level audit."
        if evidence["all_pass"] else "R131 not reproducible; retire it as a paper-trading candidate."
    )
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence.get(k) for k in ("P1", "P2", "P3", "P4", "all_pass", "A_parity", "B_parity")}, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
