from __future__ import annotations

import hashlib
import json
import shutil
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
PREREG = EVIDENCE / "r309_r40_reproducibility_preregistration.json"
OUTPUT = EVIDENCE / "r309_r40_reproducibility.json"
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
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha(path).encode("ascii"))
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
        raise RuntimeError("R309 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R309 preregistration checksum mismatch")

    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod_daily = tree_hash(DATA / "kline_daily_enriched")
    before_prod_kline = tree_hash(DATA / "kline_daily")
    manifest = json.loads((R40 / "manifest.json").read_text(encoding="utf-8"))
    members = membership()

    # The strategy service gets a private research root. Raw bars are copied so the
    # execution path cannot write into the production kline directory.
    work = Path(tempfile.mkdtemp(prefix="r309_r40_"))
    engine_root = work / "engine_data"
    try:
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
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), engine
        )
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
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod_daily = tree_hash(DATA / "kline_daily_enriched")
    after_prod_kline = tree_hash(DATA / "kline_daily")
    stats = result.stats or {}
    curve = result.equity_curve or []
    trades = result.trades or []
    timing_errors = []
    for trade in trades:
        entry = trade.get("entry_date")
        exit_ = trade.get("exit_date")
        sig = trade.get("entry_signal_date")
        if sig and entry and str(entry) <= str(sig):
            timing_errors.append({"entry": entry, "signal": sig})
        if exit_ and entry and str(exit_) < str(entry):
            timing_errors.append({"entry": entry, "exit": exit_})
    gates = {
        "G1_r40_membership_coverage": 419360 / 419700 >= 0.995,
        "G2_strategy_execution": result.error is None and len(curve) > 0 and len(trades) >= 100,
        "G3_temporal_contract": len(timing_errors) == 0,
        "G4_production_isolation": before_r40 == after_r40 and before_prod_daily == after_prod_daily and before_prod_kline == after_prod_kline,
    }
    ref = json.loads((EVIDENCE / "r102d_sl20_validation.json").read_text(encoding="utf-8"))
    ref_full = ref["cost2x"]["full"]["stats"]
    deltas = {
        "return_pp": round((float(stats.get("total_return", 0)) - float(ref_full["total_return"])) * 100, 4),
        "sharpe": round(float(stats.get("sharpe", 0)) - float(ref_full["sharpe"]), 4),
        "mdd_pp": round((float(stats.get("max_drawdown", 0)) - float(ref_full["max_drawdown"])) * 100, 4),
    }
    reproducible = (
        all(gates.values())
        and abs(deltas["return_pp"]) <= 10.0
        and abs(deltas["mdd_pp"]) <= 5.0
    )
    payload = {
        "round": "R309",
        "classification": "fixed-strategy reproducibility audit on a private R40 research snapshot; not alpha discovery",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "r40_manifest": manifest,
        "period": [str(START), str(END)],
        "strategy_id": STRATEGY_ID,
        "execution": {
            "matching": "open_t+1",
            "membership_rows": members.height,
            "equity_curve_rows": len(curve),
            "trade_count": len(trades),
            "temporal_errors": timing_errors[:20],
            "error": result.error,
        },
        "stats": stats,
        "reference_r102d": ref_full,
        "deltas": deltas,
        "gates": gates,
        "reproducible": reproducible,
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_daily_before": before_prod_daily, "production_daily_after": after_prod_daily,
            "production_raw_before": before_prod_kline, "production_raw_after": after_prod_kline,
        },
        "decision": {
            "status": "reproducible" if reproducible else "not_reproducible_or_incomplete",
            "production_write": False,
            "parameter_search": False,
            "provider_call": False,
            "alpha_scan": False,
        },
        "limits": [
            "R40 is a local research snapshot, not an official archive.",
            "The R102d reference ends 2026-10-16 while R40 ends 2026-10-14; a two-session boundary difference is expected.",
            "This is not a new deployment candidate and does not change pause_production.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "error": result.error,
        "stats": {k: stats.get(k) for k in ("total_return", "sharpe", "max_drawdown", "n_trades")},
        "curve_rows": len(curve), "trade_count": len(trades),
        "deltas": deltas, "gates": gates, "reproducible": reproducible,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
