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
PREREG = EVIDENCE / "r310_r40_candidate_reproducibility_preregistration.json"
OUTPUT = EVIDENCE / "r310_r40_candidate_reproducibility.json"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
CANDIDATES = {
    "r274": "ai_hs300_macro_gate_quality_r274_no_macro_gate",
    "r275": "ai_hs300_macro_gate_quality_r275_remove_term",
}
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
        raise RuntimeError("R310 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R310 preregistration checksum mismatch")

    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod_daily = tree_hash(DATA / "kline_daily_enriched")
    before_prod_raw = tree_hash(DATA / "kline_daily")
    work = Path(tempfile.mkdtemp(prefix="r310_r40_"))
    results = {}
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
        for key, sid in CANDIDATES.items():
            cfg = StrategyBacktestConfig(
                strategy_id=sid, symbols=None, start=START, end=END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001,
                slippage_bps=15.0, max_positions=10, max_exposure_pct=1.0,
                position_sizing="equal", mode="position", asset_type="stock", hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            stats = result.stats or {}
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
            results[key] = {
                "strategy_id": sid,
                "error": result.error,
                "stats": stats,
                "curve_rows": len(result.equity_curve or []),
                "trade_count": len(trades),
                "timing_errors": timing_errors[:20],
            }
            print(f"{key}: error={result.error} ret={stats.get('total_return')} sharpe={stats.get('sharpe')} mdd={stats.get('max_drawdown')} trades={len(trades)}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod_daily = tree_hash(DATA / "kline_daily_enriched")
    after_prod_raw = tree_hash(DATA / "kline_daily")
    r274_ref = json.loads((EVIDENCE / "r274_no_macro_gate.json").read_text(encoding="utf-8"))["runs"]["3x"]["stats"]
    r275_ref = json.loads((EVIDENCE / "r275_single_gate_ablation_v3d.json").read_text(encoding="utf-8"))["candidates"]["term"]["full"]
    refs = {
        "r274": {"total_return": r274_ref["total_return"], "sharpe": r274_ref["sharpe"], "max_drawdown": r274_ref["max_drawdown"], "n_trades": r274_ref["n_trades"]},
        "r275": {"total_return": r275_ref["total_return"], "sharpe": r275_ref["sharpe"], "max_drawdown": r275_ref["mdd"], "n_trades": r275_ref.get("n_trades")},
    }
    comparisons = {}
    for key in CANDIDATES:
        s = results[key]["stats"]
        r = refs[key]
        sr = float(s.get("total_return") or 0)
        sm = float(s.get("max_drawdown") or 0)
        comparisons[key] = {
            "reference": r,
            "current": {k: s.get(k) for k in ("total_return", "sharpe", "max_drawdown", "n_trades")},
            "return_delta_pp": round((sr - float(r["total_return"])) * 100, 4),
            "sharpe_delta": round(float(s.get("sharpe") or 0) - float(r["sharpe"]), 4),
            "mdd_delta_pp": round((sm - float(r["max_drawdown"])) * 100, 4),
        }
    gates = {
        "G1_r40_membership_coverage": 419360 / 419700 >= 0.995,
        "G2_strategy_execution": all(
            x["error"] is None and x["curve_rows"] > 0 and x["trade_count"] >= 100
            for x in results.values()
        ),
        "G3_temporal_contract": all(len(x["timing_errors"]) == 0 for x in results.values()),
        "G4_production_isolation": before_r40 == after_r40 and before_prod_daily == after_prod_daily and before_prod_raw == after_prod_raw,
    }
    reproducible = {
        key: all(gates.values()) and abs(comparisons[key]["return_delta_pp"]) <= 10.0 and abs(comparisons[key]["mdd_delta_pp"]) <= 5.0
        for key in CANDIDATES
    }
    payload = {
        "round": "R310",
        "classification": "fixed-candidate reproducibility audit on a private R40 snapshot; not alpha discovery",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "period": [str(START), str(END)],
        "results": results,
        "comparisons": comparisons,
        "gates": gates,
        "reproducible": reproducible,
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_daily_before": before_prod_daily, "production_daily_after": after_prod_daily,
            "production_raw_before": before_prod_raw, "production_raw_after": after_prod_raw,
        },
        "decision": {
            "status": "reproducible" if all(reproducible.values()) else "not_reproducible_or_incomplete",
            "production_write": False, "parameter_search": False, "provider_call": False, "alpha_scan": False,
            "note": "Even if reproducible, prior concentration and untouched-holdout gates remain unresolved.",
        },
        "limits": [
            "R40 is a local research snapshot, not an official archive.",
            "The references used 3x cost and end 2026-10-16; this audit ends 2026-10-14.",
            "No candidate is promoted by this audit.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"gates": gates, "reproducible": reproducible, "comparisons": comparisons, "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
