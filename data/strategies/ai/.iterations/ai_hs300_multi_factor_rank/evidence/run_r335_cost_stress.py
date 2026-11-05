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
PREREG = EVIDENCE / "r335_cost_stress_preregistration.json"
R334_RESULT = EVIDENCE / "r334_equity_cap33.json"
R334_PREREG = EVIDENCE / "r334_equity_cap33_preregistration.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
RUN_CACHE = DATA / "research" / "r335_cache"
OUTPUT = EVIDENCE / "r335_cost_stress.json"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
MAIN_OVERLAY = 0.002
STRESS_OVERLAY = 0.0025
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
        raise RuntimeError("R335 result exists; refusing overwrite")
    for path in (PREREG, R334_RESULT, R334_PREREG, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    r334 = json.loads(R334_RESULT.read_text(encoding="utf-8"))
    if r334["summary"]["selected"] != "r334_top10_three_asset_equity_cap33_posthoc":
        raise RuntimeError("R334 candidate is not in the expected selected state")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r335")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r335")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r335")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r335")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r335")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r335")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r335")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r335")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r335_"))
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
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)
    stress_cfg = {
        4: {"fees": 0.0008, "commission": 0.0008, "stamp": 0.001, "slippage": 20.0},
        5: {"fees": 0.0010, "commission": 0.0010, "stamp": 0.00125, "slippage": 25.0},
    }

    def run(strategy_id: str, factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        key = f"{strategy_id}_{factor}x"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        if curve_path.exists():
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            trades = json.loads(trades_path.read_text(encoding="utf-8")) if capture_trades else []
            if capture_trades and not trades_path.exists():
                raise RuntimeError(f"trade cache missing for {key}")
        else:
            params = stress_cfg[factor]
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
                max_positions=10,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            with r324.patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {factor}x: {result.error}")
            curve = r324.curve_frame(result.equity_curve)
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture_trades:
                trades = [{
                    "sleeve": "A" if strategy_id == r331.A_STRATEGY else "B",
                    "symbol": trade.get("symbol"),
                    "name": trade.get("name"),
                    "entry_date": str(trade.get("entry_date")),
                    "exit_date": str(trade.get("exit_date")),
                    "pnl_pct": trade.get("pnl_pct"),
                    "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                    "exit_reason": trade.get("exit_reason"),
                } for trade in result.trades]
                trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            print(f"ran {strategy_id} {factor}x: {r112._stats(curve)}", flush=True)
        if curve["date"].min() != START or curve["date"].max() != END:
            raise RuntimeError(f"{key} curve coverage mismatch: {curve['date'].min()}..{curve['date'].max()}")
        return curve, trades

    try:
        a4, _ = run(r331.A_STRATEGY, 4, capture_trades=False)
        b4, _ = run(r331.B_STRATEGY, 4, capture_trades=False)
        a5, a_trades = run(r331.A_STRATEGY, 5, capture_trades=True)
        b5, b_trades = run(r331.B_STRATEGY, 5, capture_trades=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")

    def build(equity_curve: pl.DataFrame, overlay_cost: float) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = 60
            r331.TARGET_VOL = 0.15
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            return r331.build_three_asset_bucket(
                equity_curve,
                r331.asset_frame("gold", r331.PRIMARY["gold"]),
                r331.asset_frame("treasury", r331.PRIMARY["treasury"]),
                r331.asset_frame("dividend", r331.PRIMARY["dividend"]),
                round_trip_cost=overlay_cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    risk4, _rp4 = r112._combine(a4, b4, "risk")
    equity4 = r114._vol_target(risk4)["curve"]
    risk5, rp5 = r112._combine(a5, b5, "risk")
    equity5 = r114._vol_target(risk5)["curve"]
    primary4 = build(equity4, MAIN_OVERLAY)
    primary5 = build(equity5, STRESS_OVERLAY)
    results = {}
    for factor, item in ((4, primary4), (5, primary5)):
        curve = item["curve"]
        stats = r112._stats(curve)
        results[f"{factor}x"] = {
            "stats": stats,
            "yearly": r315.year_returns(curve),
            "recent_year": r112._stats(curve.filter(pl.col("date").is_between(RECENT_START, END)).sort("date")),
            "weights": item["weight_summary"],
            "turnover": round(item["annual_turnover"], 6),
            "asset_contribution_shares": {
                key: round(value / sum(max(v, 0.0) for v in item["contributions"].values()), 6)
                for key, value in item["contributions"].items()
            },
        }

    curve_dates = risk5["date"].to_list()
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    scaled = []
    for trade in [*a_trades, *b_trades]:
        entry = r326.parse_day(trade["entry_date"])
        raw_exit = trade.get("exit_date")
        exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
        days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
        scales = []
        for day in days:
            equity_weight = r326.weight_at(primary5["rebalances"], day, "target_equity_weight")
            sleeve_share = r326.weight_at(rp5, day, "weight_a")
            scales.append(equity_weight * (sleeve_share if trade["sleeve"] == "A" else 1.0 - sleeve_share))
        scale = sum(scales) / len(scales)
        scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
    full5 = r239._aggregate(scaled, industry)
    recent5 = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
    contributions5 = results["5x"]["asset_contribution_shares"]
    weights5 = primary5["weight_summary"]
    concentration_gates = {
        "C1_stock_breadth": full5["distinct_symbols"] >= 80,
        "C2_top1": full5["total_pnl_amount"] > 0 and full5["top1_net_share"] <= 0.25,
        "C3_top3": full5["total_pnl_amount"] > 0 and full5["top3_net_share"] <= 0.55,
        "C4_top5": full5["total_pnl_amount"] > 0 and full5["top5_net_share"] <= 0.75,
        "C5_recent_breadth": sum(1 for row in recent5["per_symbol"] if row["pnl_amount"] > 0) >= 8,
        "C6_industry": full5["top_industry_gross_share"] is not None and full5["top_industry_gross_share"] <= 0.45,
        "C7_loss": full5["max_symbol_loss_to_gross"] is not None and full5["max_symbol_loss_to_gross"] <= 0.20,
        "C8_asset_contribution": all(value is not None and value <= 0.50 for value in contributions5.values()),
        "C9_asset_weight": (
            weights5["gold_weight_mean"] <= 0.40 and weights5["gold_weight_max"] <= 0.50
            and weights5["treasury_weight_mean"] <= 0.40 and weights5["treasury_weight_max"] <= 0.50
            and weights5["dividend_weight_mean"] <= 0.40 and weights5["dividend_weight_max"] <= 0.50
        ),
        "C10_liquidity": manifest["gates"]["D3_primary_liquidity"],
        "C11_turnover": primary5["annual_turnover"] <= 1.5,
        "C12_weights": weights5["max_weight_sum"] <= 1.0 and weights5["min_target_weight"] >= 0.0,
    }
    gates = {
        "4x": results["4x"]["stats"]["total_return"] > 0 and results["4x"]["stats"]["mdd"] >= -0.25,
        "5x": (
            results["5x"]["stats"]["total_return"] > 0
            and results["5x"]["stats"]["sharpe"] >= 0.50
            and results["5x"]["stats"]["mdd"] >= -0.25
            and results["5x"]["yearly"].get("2022", -1.0) >= 0.0
            and results["5x"]["yearly"].get("2023", -1.0) >= 0.0
            and results["5x"]["recent_year"]["total_return"] > 0
        ),
        "concentration_5x": all(concentration_gates.values()),
        "curve_coverage": results["4x"]["stats"]["days"] == results["5x"]["stats"]["days"] == 1316,
    }
    all_pass = all(gates.values())
    payload = {
        "round": "R335",
        "classification": "post-hoc execution-cost stress; no parameter or weight search",
        "preregistration_sha256": sha256(PREREG),
        "r334_result_sha256": sha256(R334_RESULT),
        "r334_preregistration_sha256": sha256(R334_PREREG),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "results": results,
        "concentration_5x": full5,
        "concentration_recent_5x": recent5,
        "concentration_gates_5x": concentration_gates,
        "gates": gates,
        "all_pass": all_pass,
        "candidate_status": "candidate_cost_stress_confirmed" if all_pass else "research_only_cost_fragile",
        "selected": "r334_top10_three_asset_equity_cap33_posthoc" if all_pass else None,
        "decision": {
            "paper_review_status": "retain_candidate_for_paper_trading_review" if all_pass else "downgrade_to_research_only",
            "production_write": False,
            "provider_write": False,
            "parameter_search": False,
            "paper_target_generated": False,
            "order_file_generated": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "4x": {"stats": results["4x"]["stats"], "yearly": results["4x"]["yearly"]},
        "5x": {"stats": results["5x"]["stats"], "yearly": results["5x"]["yearly"], "recent": results["5x"]["recent_year"]},
        "concentration_5x": {key: full5.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "gates": gates,
        "candidate_status": payload["candidate_status"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
