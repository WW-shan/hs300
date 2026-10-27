#!/usr/bin/env python3
"""R265: rebuild alt_hs300 with the R131-era builder and verify A/B parity."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXT = DATA / "ext_data" / "alt_hs300"
BACKUP = DATA / "research" / "r265_backup_alt_hs300"
FROZEN = DATA / "research" / "r265_frozen_alt_hs300"
FAILED = DATA / "research" / "r265_r131_rebuild_failed"
BUILDER = ROOT / "scripts" / "_r265_build_alt_factor_data_r131.py"
CACHE = DATA / "research" / "r265_cache"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r265_r131_snapshot_parity.json"
LOG = ROOT / "_tmp" / "logs" / "r265_builder.log"
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
    p = path.resolve()
    r = root.resolve()
    if p != r and r not in p.parents:
        raise RuntimeError(f"path escapes workspace root: {p} not under {r}")


def _signature(path: Path) -> dict:
    files = sorted(path.rglob("*.parquet"))
    total = sum(f.stat().st_size for f in files)
    h = hashlib.sha256()
    config = path / "config.json"
    if config.exists():
        h.update(config.read_bytes())
    if files:
        h.update(files[-1].read_bytes())
    return {"files": len(files), "bytes": total, "config_and_latest_hash": h.hexdigest()[:32]}


def _run_sleeve(service, membership, r112, strategy_id: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    curve_path = CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}.parquet"
    trades_path = CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}.trades.json"
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
    trades = [
        {
            "symbol": t.get("symbol"), "name": t.get("name"),
            "entry_date": str(t.get("entry_date")), "exit_date": str(t.get("exit_date")),
            "pnl_pct": t.get("pnl_pct"), "pnl_amount": t.get("pnl_amount"),
            "exit_reason": t.get("exit_reason"),
        }
        for t in result.trades
    ]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"ran {strategy_id}: days={curve.height} trades={len(trades)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    return curve, trades


def _parity(cached_path: Path, rebuilt: pl.DataFrame) -> dict:
    cached = pl.read_parquet(cached_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    c0 = float(cached["value"][0])
    r0 = float(rebuilt["value"][0])
    joined = cached.rename({"value": "cached"}).join(rebuilt.rename({"value": "rebuilt"}), on="date", how="inner").sort("date")
    joined = joined.with_columns(
        (pl.col("cached") / c0).alias("cached_rebased"),
        (pl.col("rebuilt") / r0).alias("rebuilt_rebased"),
    )
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
    for path in (EXT, BACKUP, FROZEN, FAILED):
        _assert_within(path, DATA)

    if not EXT.exists():
        raise RuntimeError(f"missing current ext data: {EXT}")
    if BACKUP.exists():
        raise RuntimeError(f"stale backup exists, refusing to overwrite: {BACKUP}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "round": "R265",
        "purpose": "Rebuild alt_hs300 with the R131-era builder and verify A/B parity.",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r265_preregistration.json",
        "builder": "scripts/_r265_build_alt_factor_data_r131.py (from commit 0c196090)",
        "backup": str(BACKUP.relative_to(ROOT)),
        "restored": False,
    }
    rebuilt = None
    a_curve = b_curve = None
    try:
        shutil.move(str(EXT), str(BACKUP))
        print(f"moved current ext data to {BACKUP}", flush=True)
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(BUILDER)],
            cwd=str(ROOT / "backend"),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        elapsed = time.perf_counter() - started
        LOG.write_text((proc.stdout or "") + "\n--- STDERR ---\n" + (proc.stderr or ""), encoding="utf-8")
        evidence["builder_run"] = {
            "exit_code": proc.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "log": str(LOG.relative_to(ROOT)),
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
        }
        p1 = proc.returncode == 0 and EXT.exists()
        evidence["P1"] = p1
        if not p1:
            raise RuntimeError(f"R131-era builder failed with exit code {proc.returncode}")

        evidence["rebuilt_signature_before_restore"] = _signature(EXT)

        r112 = _load(R112_SCRIPT, "r112_r265")
        membership = r112._historical_membership()
        service = r112._service()
        a_curve, _ = _run_sleeve(service, membership, r112, A_SID)
        b_curve, _ = _run_sleeve(service, membership, r112, B_SID)
        a_parity = _parity(CACHE_A, a_curve)
        b_parity = _parity(CACHE_B, b_curve)
        p2 = a_parity["pass"]
        p3 = b_parity["pass"]
        evidence["A_parity"] = a_parity
        evidence["B_parity"] = b_parity
        evidence["P2"] = p2
        evidence["P3"] = p3

        if p2 and p3:
            shutil.move(str(EXT), str(FROZEN))
            evidence["frozen_snapshot"] = str(FROZEN.relative_to(ROOT))
            evidence["frozen_signature"] = _signature(FROZEN)
        else:
            shutil.move(str(EXT), str(FAILED))
            evidence["failed_rebuild"] = str(FAILED.relative_to(ROOT))
        shutil.move(str(BACKUP), str(EXT))
        evidence["restored"] = True
    finally:
        if BACKUP.exists():
            if EXT.exists():
                suffix = int(time.time())
                shutil.move(str(EXT), str(FAILED.parent / f"r265_abandoned_{suffix}"))
            shutil.move(str(BACKUP), str(EXT))
            evidence["restored"] = True
    p4 = evidence["restored"]
    evidence["P4"] = p4
    evidence["all_pass"] = all(bool(evidence.get(k)) for k in ("P1", "P2", "P3", "P4"))
    evidence["decision"] = (
        "R131 reproduced from the R131-era builder; proceed to trade-level audit."
        if evidence["all_pass"]
        else "R131 not reproducible; retire it as a paper-trading candidate."
    )
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence.get(k) for k in ("P1", "P2", "P3", "P4", "all_pass", "A_parity", "B_parity")}, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
