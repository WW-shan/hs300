from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
PREREG = EVIDENCE / "r340_broad_quality_momentum_preregistration.json"
AMENDMENT = EVIDENCE / "r340_preregistration_amendment_A1.json"
R339_RESULT = EVIDENCE / "r339_c_sleeve_screen.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
RUN_CACHE = DATA / "research" / "r340_cache"
OUTPUT = EVIDENCE / "r340_broad_quality_momentum.json"
START = date(2023, 5, 4)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
MAX_POSITIONS = 50
STOCK_COST = {
    2: {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
    5: {"fees": 0.0010, "commission": 0.0010, "stamp": 0.00125, "slippage": 25.0},
}
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R340 result exists; refusing overwrite")
    for path in (PREREG, AMENDMENT, R339_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    if json.loads(R339_RESULT.read_text(encoding="utf-8"))["selected_c"] is not None:
        raise RuntimeError("R340 broad sleeve is only justified after the R339 no-selection result")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r340")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r340")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r340")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r340")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r340")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r340")
    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r340_"))
    engine_root = work / "engine_data"
    shutil.copytree(R40, engine_root, symlinks=True)
    r40_latest = r331.latest_partition_date(R40 / "kline_daily_enriched")
    if r40_latest is None:
        raise RuntimeError("R40 enriched history is empty")
    for source in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet")):
        partition_day = date.fromisoformat(source.parent.name.split("=")[1])
        if partition_day <= r40_latest:
            continue
        target = engine_root / "kline_daily_enriched" / source.parent.name / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    raw_target = engine_root / "kline_daily"
    if raw_target.is_symlink() or raw_target.is_file():
        raw_target.unlink()
    elif raw_target.exists():
        shutil.rmtree(raw_target)
    shutil.copytree(DATA / "kline_daily", raw_target)
    r324.load_into_registry(DATA)
    engine = r324.StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: r324.strategy_config.load_override(DATA, sid),
        industry_loader=r324.industry_loader_for(DATA),
    )
    strategy = engine.get(C_STRATEGY)
    if strategy.max_hold_days != 60 or strategy.stop_loss is not None:
        raise RuntimeError("R340 C strategy definition changed")
    factor_registry = importlib.import_module("app.factors.registry")
    missing = sorted(
        feature
        for feature in strategy.required_features - {"ma60"}
        if factor_registry.get_factor(feature) is None
    )
    if missing:
        raise RuntimeError(f"R340 required factors missing: {missing}")
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    def run(strategy_id: str, cost_factor: int, max_positions: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        key = f"{strategy_id}_{START}_{cost_factor}x_p{max_positions}"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        if curve_path.exists():
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            trades = json.loads(trades_path.read_text(encoding="utf-8")) if capture_trades else []
            if capture_trades and not trades_path.exists():
                raise RuntimeError(f"trade cache missing for {key}")
        else:
            params = STOCK_COST[cost_factor]
            cfg = r324.StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=START,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                fees_pct=params["fees"],
                commission_pct=params["commission"],
                stamp_tax_pct=params["stamp"],
                slippage_bps=params["slippage"],
                max_positions=max_positions,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            with r324.patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {cost_factor}x: {result.error}")
            curve = r324.curve_frame(result.equity_curve)
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture_trades:
                trades = [{
                    "symbol": trade.get("symbol"),
                    "name": trade.get("name"),
                    "entry_date": str(trade.get("entry_date")),
                    "exit_date": str(trade.get("exit_date")),
                    "pnl_pct": trade.get("pnl_pct"),
                    "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                    "exit_reason": trade.get("exit_reason"),
                } for trade in result.trades]
                trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            print(f"ran {strategy_id} {cost_factor}x p{max_positions}: {r112._stats(curve)}", flush=True)
        if curve["date"].min() != START or curve["date"].max() != END:
            raise RuntimeError(f"{key} coverage mismatch: {curve['date'].min()}..{curve['date'].max()}")
        return curve, trades

    def metrics(curve: pl.DataFrame) -> dict:
        return {
            "stats": r112._stats(curve),
            "recent": r112._stats(curve.filter(pl.col("date").is_between(RECENT_START, END)).sort("date")),
            "yearly": r315.year_returns(curve),
        }

    try:
        a_2x, _ = run(A_STRATEGY, 2, 10, capture_trades=False)
        b_2x, _ = run(B_STRATEGY, 2, 10, capture_trades=False)
        c_2x, _ = run(C_STRATEGY, 2, MAX_POSITIONS, capture_trades=False)
        c_5x, c_trades_5x = run(C_STRATEGY, 5, MAX_POSITIONS, capture_trades=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    concentration_5x = r239._aggregate(c_trades_5x, industry)
    corr_a = r112._corr(c_2x, a_2x)
    corr_b = r112._corr(c_2x, b_2x)
    result_2x = metrics(c_2x)
    result_5x = metrics(c_5x)
    concentration = {key: concentration_5x.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross", "total_pnl_amount")}
    gates = {
        "D1": True,
        "D2": c_2x["date"].min() == START and c_2x["date"].max() == END and c_5x["date"].max() == END,
        "E1": result_5x["stats"]["total_return"] >= 0.10 and result_5x["stats"]["sharpe"] >= 0.35,
        "E2": result_2x["recent"]["total_return"] > 0,
        "E3": result_2x["stats"]["sharpe"] >= 0.50,
        "E4": max(corr_a, corr_b) <= 0.85,
        "E5": concentration["distinct_symbols"] >= 100,
        "E6": concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25 and concentration["top3_net_share"] <= 0.55 and concentration["top5_net_share"] <= 0.75,
        "E7": concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45,
        "E8": concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20,
    }
    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    payload = {
        "round": "R340",
        "candidate_id": "r340_broad_quality_momentum_h60_p50_posthoc",
        "status": "candidate_ready_for_portfolio_retest" if all(gates.values()) else "standalone_screen_failed",
        "preregistration_sha256": sha256(PREREG),
        "amendment_sha256": sha256(AMENDMENT),
        "r339_result_sha256": sha256(R339_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "strategy": {"id": C_STRATEGY, "sha256": sha256(Path(strategy.file_path)), "max_hold_days": strategy.max_hold_days, "stop_loss": strategy.stop_loss, "max_positions": MAX_POSITIONS, "limit": strategy.meta["limit"]},
        "periods": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "metrics_2x": result_2x,
        "metrics_5x": result_5x,
        "correlation_A": round(corr_a, 6),
        "correlation_B": round(corr_b, 6),
        "max_correlation": round(max(corr_a, corr_b), 6),
        "concentration_5x": concentration,
        "gates": gates,
        "all_pass": all(gates.values()),
        "decision": {
            "status": "candidate_ready_for_portfolio_retest" if all(gates.values()) else "no_candidate_passed",
            "combined_portfolio_run": False,
            "post_hoc": True,
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
            "provider_write": False,
        },
        "input_isolation": {"r40_before": before_r40, "r40_after": after_r40, "production_before": before_prod, "production_after": after_prod, "stable": before_r40 == after_r40 and before_prod == after_prod},
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"metrics_2x": result_2x["stats"], "recent_2x": result_2x["recent"], "metrics_5x": result_5x["stats"], "yearly_5x": result_5x["yearly"], "correlations": {"A": round(corr_a, 6), "B": round(corr_b, 6)}, "concentration_5x": concentration, "gates": gates, "all_pass": all(gates.values()), "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
