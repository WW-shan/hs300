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
PREREG = EVIDENCE / "r356_core_earnings_preregistration.json"
PREFLIGHT = EVIDENCE / "r356_core_earnings_preflight.json"
ERRATUM = EVIDENCE / "r356_preflight_erratum_A1.json"
RUN_CACHE = DATA / "research" / "r356_cache"
OUTPUT = EVIDENCE / "r356_core_earnings_backtest.json"
CANDIDATE = "ai_hs300_macro_gate_quality_core_earnings_gate"
CONTROL = "ai_hs300_macro_gate_quality"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), RECENT_START, date(2026, 1, 5)]
POSITIONS = {CANDIDATE: 10, CONTROL: 10}
STOCK_COST = {
    2: {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
    3: {"fees": 0.0006, "commission": 0.0006, "stamp": 0.001, "slippage": 15.0},
    4: {"fees": 0.0008, "commission": 0.0008, "stamp": 0.001, "slippage": 20.0},
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


def segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    segment = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if segment.height < 2:
        return None
    values = segment["value"].to_list()
    return values[-1] / values[0] - 1.0


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R356 result exists; refusing overwrite")
    for path in (PREREG, PREFLIGHT, ERRATUM):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"R356 checksum mismatch: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight["all_checks"] or not preflight["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R356 preflight did not pass")
    if sha256(DATA / "strategies/ai" / f"{CANDIDATE}.py") != prereg["artifact_sha256"]["candidate_strategy"]:
        raise RuntimeError("R356 candidate strategy hash changed")
    if sha256(DATA / "strategies/ai" / f"{CONTROL}.py") != prereg["artifact_sha256"]["control_strategy"]:
        raise RuntimeError("R356 control strategy hash changed")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r356")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r356")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r356")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r356")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r356")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r356")
    r331.self_test()

    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r356_"))
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
    candidate_strategy = engine.get(CANDIDATE)
    control_strategy = engine.get(CONTROL)
    if candidate_strategy is None or control_strategy is None:
        raise RuntimeError("R356 required strategy failed to load")
    if candidate_strategy.max_hold_days != 35 or candidate_strategy.stop_loss is not None:
        raise RuntimeError("R356 candidate execution changed")
    if control_strategy.max_hold_days != 35 or control_strategy.stop_loss is not None:
        raise RuntimeError("R356 control execution changed")
    service = r324.StrategyBacktestService(
        r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))),
        engine,
    )

    def run(strategy_id: str, start: date, cost_factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        max_positions = POSITIONS[strategy_id]
        key = f"{strategy_id}_{start}_{cost_factor}x_p{max_positions}"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        if curve_path.exists():
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            if capture_trades:
                if not trades_path.exists():
                    raise RuntimeError(f"trade cache missing for {key}")
                trades = json.loads(trades_path.read_text(encoding="utf-8"))
            else:
                trades = []
        else:
            params = STOCK_COST[cost_factor]
            cfg = r324.StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
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
                raise RuntimeError(f"{strategy_id} {start} {cost_factor}x: {result.error}")
            curve = r315.curve_frame(result.equity_curve)
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
            print(f"ran {strategy_id} {start} {cost_factor}x: {r112._stats(curve)}", flush=True)
        if curve["date"].max() != END:
            raise RuntimeError(f"{key} does not reach END: {curve['date'].max()}")
        if start == START and curve["date"].min() != START:
            raise RuntimeError(f"{key} does not start at START: {curve['date'].min()}")
        return curve, trades

    try:
        curves = {CANDIDATE: {}, CONTROL: {}}
        trades = {CANDIDATE: {}, CONTROL: {}}
        for factor in (2, 3, 4, 5):
            for strategy_id in (CANDIDATE, CONTROL):
                curve, trade_rows = run(strategy_id, START, factor, capture_trades=factor in (2, 5))
                curves[strategy_id][factor] = curve
                if factor in (2, 5):
                    trades[strategy_id][factor] = trade_rows
        fresh = {CANDIDATE: {}, CONTROL: {}}
        for phase in PHASES:
            for strategy_id in (CANDIDATE, CONTROL):
                if phase == START:
                    curve = curves[strategy_id][2]
                else:
                    curve, _ = run(strategy_id, phase, 2, capture_trades=False)
                fresh[strategy_id][str(phase)] = segment_return(curve, phase)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    from app.services.industry_map import load_industry_l1

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }

    def summary(strategy_id: str) -> dict:
        metrics = {f"{factor}x": r112._stats(curves[strategy_id][factor]) for factor in (2, 3, 4, 5)}
        conc = {f"{factor}x": r239._aggregate(trades[strategy_id][factor], industry) for factor in (2, 5)}
        return {
            "metrics": metrics,
            "yearly_2x": r315.year_returns(curves[strategy_id][2]),
            "yearly_5x": r315.year_returns(curves[strategy_id][5]),
            "recent_2x": segment_return(curves[strategy_id][2], RECENT_START),
            "recent_5x": segment_return(curves[strategy_id][5], RECENT_START),
            "segment_2022_2x": segment_return(curves[strategy_id][2], START, date(2022, 12, 31)),
            "concentration": {
                factor: {key: value for key, value in conc[factor].items() if key not in {"per_symbol", "top1_symbol", "top3_symbols", "top5_symbols", "worst_symbols", "industries"}}
                for factor in conc
            },
            "fresh_starts_2x": fresh[strategy_id],
            "fresh_positive": sum(1 for value in fresh[strategy_id].values() if value is not None and value > 0),
        }

    candidate = summary(CANDIDATE)
    control = summary(CONTROL)
    c2 = candidate["metrics"]["2x"]
    b2 = control["metrics"]["2x"]
    c5 = candidate["metrics"]["5x"]
    conc5 = candidate["concentration"]["5x"]
    gates = {
        "G1_2x_total_return_not_below_control": c2["total_return"] >= b2["total_return"],
        "G2_2x_sharpe_not_below_control": c2["sharpe"] >= b2["sharpe"],
        "G3_2x_mdd_no_worse_than_control_by_more_than_0.02": c2["mdd"] >= b2["mdd"] - 0.02,
        "G4_2022_segment_return_positive": (candidate["segment_2022_2x"] or -1.0) > 0.0,
        "G5_2023_return_nonnegative": candidate["yearly_2x"].get("2023", -1.0) >= 0.0,
        "G6_recent_year_return_positive": (candidate["recent_2x"] or -1.0) > 0.0,
        "G7_3x_total_return_positive": candidate["metrics"]["3x"]["total_return"] > 0.0,
        "G8_4x_total_return_positive": candidate["metrics"]["4x"]["total_return"] > 0.0,
        "G9_5x_total_return_positive": c5["total_return"] > 0.0,
        "G10_5x_sharpe_at_least_0.50": c5["sharpe"] >= 0.50,
        "G11_5x_mdd_at_least_minus_0.25": c5["mdd"] >= -0.25,
        "G12_5x_top1_net_share_at_most_0.25": conc5["top1_net_share"] <= 0.25,
        "G13_5x_top3_net_share_at_most_0.55": conc5["top3_net_share"] <= 0.55,
        "G14_5x_top5_net_share_at_most_0.75": conc5["top5_net_share"] <= 0.75,
        "G15_distinct_symbols_not_below_control": conc5["distinct_symbols"] >= control["concentration"]["5x"]["distinct_symbols"],
        "G16_top_industry_gross_share_at_most_0.45": conc5["top_industry_gross_share"] is not None and conc5["top_industry_gross_share"] <= 0.45,
        "G17_max_symbol_loss_to_gross_at_most_0.20": conc5["max_symbol_loss_to_gross"] is not None and conc5["max_symbol_loss_to_gross"] <= 0.20,
        "G18_fresh_starts_at_least_5_of_6_positive": candidate["fresh_positive"] >= 5,
    }
    data_gates = {
        "D1_candidate_and_control_strategy_hashes_frozen": True,
        "D2_full_curves_start_and_end": all(
            curves[strategy_id][factor]["date"].min() == START and curves[strategy_id][factor]["date"].max() == END
            for strategy_id in (CANDIDATE, CONTROL)
            for factor in (2, 3, 4, 5)
        ),
        "D3_input_isolation": before_r40 == after_r40 and before_prod == after_prod,
    }
    all_pass = all(data_gates.values()) and all(gates.values())
    payload = {
        "round": "R356",
        "classification": "standalone A-sleeve core-earnings gate screen; no portfolio integration",
        "preregistration_sha256": sha256(PREREG),
        "preflight_sha256": sha256(PREFLIGHT),
        "preflight_erratum_sha256": sha256(ERRATUM),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "factor_sha256": sha256(DATA / "user_data/custom_factors/uf_core_earnings_quality.json"),
        "strategy_sha256": {
            "candidate": sha256(DATA / "strategies/ai" / f"{CANDIDATE}.py"),
            "control": sha256(DATA / "strategies/ai" / f"{CONTROL}.py"),
        },
        "period": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "candidate": candidate,
        "control": control,
        "data_gates": data_gates,
        "performance_gates": gates,
        "all_pass": all_pass,
        "selected": "r356_core_earnings_A_gate" if all_pass else None,
        "decision": {
            "status": "candidate_for_portfolio_integration" if all_pass else "no_candidate_passed",
            "portfolio_integration_run": False,
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": [
            "This is a post-hoc factor screen on an already researched A sleeve.",
            "Core earnings coverage is 92.0%-95.5% among A candidates; missing values fail the gate.",
            "Financial statements are current snapshots with restatement risk; announcement dates are normalized PIT dates.",
            "Industry classification uses the current snapshot backfilled through history.",
            "This standalone screen does not test the frozen three-sleeve portfolio or defensive assets.",
        ],
        "no_leverage": True,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "all_pass": all_pass,
        "selected": payload["selected"],
        "candidate_2x": c2,
        "control_2x": b2,
        "candidate_5x": c5,
        "candidate_5x_concentration": candidate["concentration"]["5x"],
        "fresh_positive": candidate["fresh_positive"],
        "performance_gates": gates,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
