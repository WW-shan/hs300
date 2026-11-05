from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import statistics as st
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
R311 = EVIDENCE / "run_r311_r102_vol_target.py"
OUTPUT = EVIDENCE / "r311_full_start_verification.json"
STRATEGY_ID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)

sys.path.insert(0, str(ROOT / "backend"))
from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R311 full-start verification exists; refusing overwrite")
    r311 = module(R311, "r311")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112")
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r311_full_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        raw_target = engine_root / "kline_daily"
        if raw_target.is_symlink() or raw_target.is_file():
            raw_target.unlink()
        elif raw_target.exists():
            shutil.rmtree(raw_target)
        shutil.copytree(DATA / "kline_daily", raw_target)
        load_into_registry(DATA)
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai"],
            override_loader=lambda sid: strategy_config.load_override(DATA, sid),
            industry_loader=industry_loader_for(DATA),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)
        cfg = StrategyBacktestConfig(
            strategy_id=STRATEGY_ID,
            symbols=None,
            start=START,
            end=END,
            params={},
            overrides={},
            matching="open_t+1",
            fees_pct=0.0004,
            commission_pct=0.0004,
            stamp_tax_pct=0.0005,
            slippage_bps=10.0,
            max_positions=10,
            max_exposure_pct=1.0,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        with patched_membership(members):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(result.error)
        base = r311.curve_frame(result.equity_curve)
        r114.TARGET_VOL = 0.15
        overlay = r114._vol_target(base)
        weights = [float(row["weight"]) for row in overlay["weight_records"]]
        fixed = r311.fixed_exposure(base)["curve"]
        payload = {
            "round": "R311",
            "classification": "descriptive full-start verification of the frozen R311 rule; no parameter search",
            "preregistration_sha256": sha(EVIDENCE / "r311_r102_vol_target_preregistration.json"),
            "r311_result_sha256": sha(EVIDENCE / "r311_r102_vol_target.json"),
            "verification_runner_sha256": sha(Path(__file__).resolve()),
            "period": [str(START), str(END)],
            "strategy_id": STRATEGY_ID,
            "base_2x": r112._stats(base),
            "vol15_2x": r112._stats(overlay["curve"]),
            "fixed50_2x": r112._stats(fixed),
            "vol15_weights": {
                "records": len(weights),
                "mean": round(st.mean(weights), 6),
                "median": round(st.median(weights), 6),
                "min": round(min(weights), 6),
                "max": round(max(weights), 6),
                "pct_at_max": round(sum(abs(x - 1.0) < 1e-12 for x in weights) / len(weights), 6),
                "pct_at_min": round(sum(abs(x - 0.2) < 1e-12 for x in weights) / len(weights), 6),
                "total_turnover": round(float(overlay["total_turnover"]), 6),
                "annual_turnover": round(float(overlay["annual_turnover"]), 6),
            },
            "input_isolation": {
                "r40_before": before_r40,
                "r40_after": tree_hash(R40 / "kline_daily_enriched"),
                "production_before": before_prod,
                "production_after": tree_hash(DATA / "kline_daily_enriched"),
            },
            "limits": [
                "The R311 long field averages ten overlapping phase starts; this addendum reports the single full-start path.",
                "No leverage: all overlay weights are bounded at 1.0.",
                "R40 is a local research snapshot; this does not repair the canonical data drift or authorize production.",
            ],
        }
        payload["input_isolation"]["stable"] = (
            payload["input_isolation"]["r40_before"] == payload["input_isolation"]["r40_after"]
            and payload["input_isolation"]["production_before"] == payload["input_isolation"]["production_after"]
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
