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
PREREG = EVIDENCE / "r337_low_turnover_b_preregistration.json"
R334_RESULT = EVIDENCE / "r334_equity_cap33.json"
R335_RESULT = EVIDENCE / "r335_cost_stress.json"
R336_RESULT = EVIDENCE / "r336_top20_cap33.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
RUN_CACHE = DATA / "research" / "r337_cache"
OUTPUT = EVIDENCE / "r337_b40.json"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h40_sl20"
B_STRATEGY_PATH = DATA / "strategies" / "ai" / f"{B_STRATEGY}.py"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [date(2022, 1, 4), date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5)]
LOOKBACKS = [40, 60, 90]
TARGETS = [0.12, 0.15, 0.18]
MAX_POSITIONS = 10
OVERLAY_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R337 result exists; refusing overwrite")
    for path in (PREREG, R334_RESULT, R335_RESULT, R336_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    if json.loads(R335_RESULT.read_text(encoding="utf-8"))["gates"]["concentration_5x"]:
        raise RuntimeError("R337 requires the documented R335 concentration failure")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg["fixed_strategy"]["B_strategy"] != B_STRATEGY or not B_STRATEGY_PATH.is_file():
        raise RuntimeError("R337 preregistration and B strategy file do not match")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r336")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r336")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r336")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r336")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r336")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r336")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r336")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r336")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r337_"))
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
    b_strategy = engine.get(B_STRATEGY)
    if b_strategy.max_hold_days != 40 or b_strategy.stop_loss != -0.20:
        raise RuntimeError("R337 must run the 40-day/20%-stop-loss B strategy")
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)

    def run(strategy_id: str, start: date, cost_factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        key = f"{strategy_id}_{start}_{cost_factor}x"
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
                start=start,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                fees_pct=params["fees"],
                commission_pct=params["commission"],
                stamp_tax_pct=params["stamp"],
                slippage_bps=params["slippage"],
                max_positions=MAX_POSITIONS,
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
            print(f"ran {strategy_id} {start} {cost_factor}x: {r112._stats(curve)}", flush=True)
        if curve["date"].max() != END:
            raise RuntimeError(f"{key} does not reach END: {curve['date'].max()}")
        if start == START and curve["date"].min() != START:
            raise RuntimeError(f"{key} does not start at START: {curve['date'].min()}")
        return curve, trades

    try:
        full = {}
        full_trades = {}
        for factor in (2, 3, 4, 5):
            full[factor] = {}
            for strategy_id in (r331.A_STRATEGY, B_STRATEGY):
                capture = factor in (2, 5)
                curve, trades = run(strategy_id, START, factor, capture_trades=capture)
                full[factor][strategy_id] = curve
                if capture:
                    full_trades[(factor, strategy_id)] = trades
        phase_curves = {}
        for phase in PHASES:
            phase_curves[phase] = {}
            for strategy_id in (r331.A_STRATEGY, B_STRATEGY):
                curve, _ = run(strategy_id, phase, 2, capture_trades=False)
                phase_curves[phase][strategy_id] = curve
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    equity = {}
    risk = {}
    rp = {}
    for factor in (2, 3, 4, 5):
        risk[factor], rp[factor] = r112._combine(full[factor][r331.A_STRATEGY], full[factor][B_STRATEGY], "risk")
        equity[factor] = r114._vol_target(risk[factor])["curve"]

    def build(equity_curve: pl.DataFrame, lookback: int, overlay_target: float, overlay_cost: float, symbols: dict | None = None) -> dict:
        chosen = symbols or r331.PRIMARY
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            return r331.build_three_asset_bucket(
                equity_curve,
                r331.asset_frame("gold", chosen["gold"]),
                r331.asset_frame("treasury", chosen["treasury"]),
                r331.asset_frame("dividend", chosen["dividend"]),
                round_trip_cost=overlay_cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    core = {
        factor: build(equity[factor], 60, 0.15, OVERLAY_COST[factor])
        for factor in (2, 3, 4, 5)
    }
    core_conservative_3x = build(equity[3], 60, 0.15, OVERLAY_COST["3c"])
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    def concentration(factor: int) -> tuple[dict, dict, int]:
        trades = [*full_trades[(factor, r331.A_STRATEGY)], *full_trades[(factor, B_STRATEGY)]]
        curve_dates = risk[factor]["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                equity_weight = r326.weight_at(core[factor]["rebalances"], day, "target_equity_weight")
                sleeve_share = r326.weight_at(rp[factor], day, "weight_a")
                scales.append(equity_weight * (sleeve_share if trade["sleeve"] == "A" else 1.0 - sleeve_share))
            scale = sum(scales) / len(scales)
            scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        recent_positive = sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)
        return full_conc, recent_conc, recent_positive

    def concentration_gates(factor: int) -> tuple[dict, dict]:
        full_conc, _recent_conc, recent_positive = concentration(factor)
        contributions = core[factor]["contributions"]
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        weights = core[factor]["weight_summary"]
        gates = {
            "C1_stock_breadth": full_conc["distinct_symbols"] >= 80,
            "C2_top1": full_conc["total_pnl_amount"] > 0 and full_conc["top1_net_share"] <= 0.25,
            "C3_top3": full_conc["total_pnl_amount"] > 0 and full_conc["top3_net_share"] <= 0.55,
            "C4_top5": full_conc["total_pnl_amount"] > 0 and full_conc["top5_net_share"] <= 0.75,
            "C5_recent_breadth": recent_positive >= 8,
            "C6_industry": full_conc["top_industry_gross_share"] is not None and full_conc["top_industry_gross_share"] <= 0.45,
            "C7_loss": full_conc["max_symbol_loss_to_gross"] is not None and full_conc["max_symbol_loss_to_gross"] <= 0.20,
            "C8_asset_contribution": all(value is not None and value <= 0.50 for value in shares.values()),
            "C9_asset_weight": (
                weights["gold_weight_mean"] <= 0.40 and weights["gold_weight_max"] <= 0.50
                and weights["treasury_weight_mean"] <= 0.40 and weights["treasury_weight_max"] <= 0.50
                and weights["dividend_weight_mean"] <= 0.40 and weights["dividend_weight_max"] <= 0.50
            ),
            "C10_liquidity": manifest["gates"]["D3_primary_liquidity"],
            "C11_turnover": core[factor]["annual_turnover"] <= 1.5,
            "C12_weights": weights["max_weight_sum"] <= 1.0 and weights["min_target_weight"] >= 0.0,
        }
        summary = {
            "concentration": {key: full_conc.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
            "recent_positive_symbols": recent_positive,
            "asset_contribution_shares": {key: round(value, 6) if value is not None else None for key, value in shares.items()},
            "weights": weights,
        }
        return gates, summary

    def metrics(curve: pl.DataFrame, stress_curve: pl.DataFrame | None = None) -> dict:
        values = curve["value"].to_list()
        returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
        bootstrap = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        output = {
            "stats": r112._stats(curve),
            "yearly": r315.year_returns(curve),
            "recent": r112._stats(curve.filter(pl.col("date").is_between(RECENT_START, END)).sort("date")),
            "bootstrap": bootstrap,
            "rolling": {
                "positive_rate": round(sum(value > 0 for value in rolling) / len(rolling), 6),
                "return_p05": round(sorted(rolling)[int(0.05 * (len(rolling) - 1))], 6),
                "return_worst": round(min(rolling), 6),
            },
        }
        if stress_curve is not None:
            output["stress"] = r112._stats(stress_curve)
        return output

    center_metrics = metrics(core[2]["curve"], core[3]["curve"])
    conservative_metrics = metrics(core_conservative_3x["curve"])
    cost_metrics = {
        "4x": metrics(core[4]["curve"]),
        "5x": metrics(core[5]["curve"]),
    }
    c2_gates, c2_summary = concentration_gates(2)
    c5_gates, c5_summary = concentration_gates(5)
    control_stats = r112._stats(equity[2])
    performance_gates = {
        "G1_return": center_metrics["stats"]["annual"] >= 0.06,
        "G2_drawdown": center_metrics["stats"]["mdd"] >= -0.18,
        "G3_sharpe": center_metrics["stats"]["sharpe"] >= 0.70,
        "G4_calendar": center_metrics["yearly"].get("2022", -1.0) >= 0.0 and center_metrics["yearly"].get("2023", -1.0) >= 0.0,
        "G5_recent": center_metrics["recent"]["total_return"] > 0,
        "G6_bootstrap": center_metrics["bootstrap"]["positive_rate"] >= 0.80 and center_metrics["bootstrap"]["return_p05"] >= -0.12,
        "G7_rolling": center_metrics["rolling"]["positive_rate"] >= 0.70 and center_metrics["rolling"]["return_worst"] >= -0.15,
        "G8_stress": center_metrics["stress"]["total_return"] > 0 and center_metrics["stress"]["mdd"] >= -0.25,
        "G9_incremental": (
            (
                center_metrics["stats"]["sharpe"] >= control_stats["sharpe"] + 0.05
                or center_metrics["stats"]["mdd"] >= control_stats["mdd"] + 0.03
            )
            and center_metrics["stats"]["annual"] >= control_stats["annual"] - 0.01
        ),
        "G10_no_leverage": core[2]["weight_summary"]["max_weight_sum"] <= 1.0 and core[2]["weight_summary"]["min_target_weight"] >= 0.0,
    }
    robust = {}
    for name, symbols in r331.ROBUSTNESS.items():
        bucket = build(equity[2], 60, 0.15, OVERLAY_COST[2], symbols)
        robust[name] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    robust_gates = {
        name: (
            item["stats"]["annual"] >= 0.05
            and item["stats"]["mdd"] >= -0.20
            and item["yearly"].get("2022", -1.0) >= 0.0
            and item["yearly"].get("2023", -1.0) >= 0.0
        )
        for name, item in robust.items()
    }
    fresh = {}
    for phase in PHASES:
        phase_risk, _ = r112._combine(phase_curves[phase][r331.A_STRATEGY], phase_curves[phase][B_STRATEGY], "risk")
        phase_equity = r114._vol_target(phase_risk)["curve"]
        bucket = build(phase_equity, 60, 0.15, OVERLAY_COST[2])
        fresh[str(phase)] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    fresh_gate = sum(1 for item in fresh.values() if item["stats"]["total_return"] > 0) >= 4 and all(item["stats"]["mdd"] >= -0.18 for item in fresh.values())

    def eval_plateau(lookback: int, overlay_target: float, equity_target: float, family: str, name: str) -> dict:
        target_equity = equity[2]
        target_stress = equity[3]
        if equity_target != 0.15:
            previous = r114.TARGET_VOL
            try:
                r114.TARGET_VOL = equity_target
                target_equity = r114._vol_target(risk[2])["curve"]
                target_stress = r114._vol_target(risk[3])["curve"]
            finally:
                r114.TARGET_VOL = previous
        bucket = build(target_equity, lookback, overlay_target, OVERLAY_COST[2])
        stress_bucket = build(target_stress, lookback, overlay_target, OVERLAY_COST["3c"])
        m = metrics(bucket["curve"], stress_bucket["curve"])
        contributions = bucket["contributions"]
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        conc_full, _, recent_positive = concentration_for_variant(bucket, rp[2], target_equity)
        weights = bucket["weight_summary"]
        gates = {
            "G1": m["stats"]["annual"] >= 0.06,
            "G2": m["stats"]["mdd"] >= -0.18,
            "G3": m["stats"]["sharpe"] >= 0.70,
            "G4": m["yearly"].get("2022", -1.0) >= 0.0 and m["yearly"].get("2023", -1.0) >= 0.0,
            "G5": m["recent"]["total_return"] > 0,
            "G6": m["bootstrap"]["positive_rate"] >= 0.80 and m["bootstrap"]["return_p05"] >= -0.12,
            "G7": m["rolling"]["positive_rate"] >= 0.70 and m["rolling"]["return_worst"] >= -0.15,
            "G8": m["stress"]["total_return"] > 0 and m["stress"]["mdd"] >= -0.25,
            "C1": conc_full["distinct_symbols"] >= 80,
            "C2": conc_full["total_pnl_amount"] > 0 and conc_full["top1_net_share"] <= 0.25,
            "C3": conc_full["total_pnl_amount"] > 0 and conc_full["top3_net_share"] <= 0.55,
            "C4": conc_full["total_pnl_amount"] > 0 and conc_full["top5_net_share"] <= 0.75,
            "C5": recent_positive >= 8,
            "C6": conc_full["top_industry_gross_share"] is not None and conc_full["top_industry_gross_share"] <= 0.45,
            "C7": conc_full["max_symbol_loss_to_gross"] is not None and conc_full["max_symbol_loss_to_gross"] <= 0.20,
            "C8": all(value is not None and value <= 0.50 for value in shares.values()),
            "C9": weights["gold_weight_mean"] <= 0.40 and weights["gold_weight_max"] <= 0.50 and weights["treasury_weight_mean"] <= 0.40 and weights["treasury_weight_max"] <= 0.50 and weights["dividend_weight_mean"] <= 0.40 and weights["dividend_weight_max"] <= 0.50,
            "C11": bucket["annual_turnover"] <= 1.5,
            "C12": weights["max_weight_sum"] <= 1.0 and weights["min_target_weight"] >= 0.0,
        }
        return {"name": name, "family": family, "lookback": lookback, "overlay_target": overlay_target, "equity_target": equity_target, "metrics": m, "gates": gates, "all_pass": all(gates.values()), "concentration": {key: conc_full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")}, "asset_contribution_shares": {key: round(value, 6) if value is not None else None for key, value in shares.items()}}

    def concentration_for_variant(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> tuple[dict, dict, int]:
        trades = [*full_trades[(2, r331.A_STRATEGY)], *full_trades[(2, B_STRATEGY)]]
        curve_dates = equity_curve["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                eqw = r326.weight_at(allocation["rebalances"], day, "target_equity_weight")
                sleeve = r326.weight_at(rp_records, day, "weight_a")
                scales.append(eqw * (sleeve if trade["sleeve"] == "A" else 1.0 - sleeve))
            scale = sum(scales) / len(scales)
            scaled.append({**trade, "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    overlay_results = {}
    for lookback in LOOKBACKS:
        for target in TARGETS:
            name = f"L{lookback}_T{round(target * 100)}"
            overlay_results[name] = eval_plateau(lookback, target, 0.15, "overlay_grid", name)
    joint_results = {}
    for target in TARGETS:
        name = f"joint_T{round(target * 100)}"
        joint_results[name] = overlay_results["L60_T15"] if target == 0.15 else eval_plateau(60, target, target, "joint_target_grid", name)
    overlay_passes = sum(1 for item in overlay_results.values() if item["all_pass"])
    joint_passes = sum(1 for item in joint_results.values() if item["all_pass"])
    plateau_pass = overlay_passes >= 7 and joint_passes >= 2 and overlay_results["L60_T15"]["all_pass"]
    cost_gates = {
        "4x": cost_metrics["4x"]["stats"]["total_return"] > 0 and cost_metrics["4x"]["stats"]["mdd"] >= -0.25,
        "5x_performance": cost_metrics["5x"]["stats"]["total_return"] > 0 and cost_metrics["5x"]["stats"]["sharpe"] >= 0.50 and cost_metrics["5x"]["stats"]["mdd"] >= -0.25 and cost_metrics["5x"]["yearly"].get("2022", -1.0) >= 0.0 and cost_metrics["5x"]["yearly"].get("2023", -1.0) >= 0.0 and cost_metrics["5x"]["recent"]["total_return"] > 0,
        "5x_concentration": all(c5_gates.values()),
    }
    conservative_gate = conservative_metrics["stats"]["total_return"] > 0 and conservative_metrics["stats"]["mdd"] >= -0.25
    all_pass = (
        all(performance_gates.values())
        and all(c2_gates.values())
        and all(robust_gates.values())
        and fresh_gate
        and all(cost_gates.values())
        and conservative_gate
        and plateau_pass
    )
    payload = {
        "round": "R337",
        "classification": "post-hoc low-turnover B-leg experiment; full cost, plateau, robustness and fresh-start validation",
        "preregistration_sha256": sha256(PREREG),
        "r334_result_sha256": sha256(R334_RESULT),
        "r335_result_sha256": sha256(R335_RESULT),
        "r336_result_sha256": sha256(R336_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "strategy_files": {
            "A": {"id": r331.A_STRATEGY, "sha256": sha256(engine.get(r331.A_STRATEGY).file_path)},
            "B": {"id": B_STRATEGY, "sha256": sha256(B_STRATEGY_PATH), "max_hold_days": b_strategy.max_hold_days, "stop_loss": b_strategy.stop_loss},
        },
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)]},
        "center_2x": center_metrics,
        "center_3x_conservative": conservative_metrics,
        "cost_metrics": cost_metrics,
        "concentration_2x": c2_summary,
        "concentration_5x": c5_summary,
        "performance_gates": performance_gates,
        "concentration_gates_2x": c2_gates,
        "concentration_gates_5x": c5_gates,
        "robustness": robust,
        "robustness_gates": robust_gates,
        "fresh_starts": fresh,
        "fresh_start_gate": fresh_gate,
        "overlay_grid": overlay_results,
        "joint_target_grid": joint_results,
        "plateau_summary": {"overlay_passes": overlay_passes, "joint_passes": joint_passes, "center_pass": overlay_results["L60_T15"]["all_pass"], "plateau_pass": plateau_pass},
        "cost_gates": cost_gates,
        "conservative_3x_gate": conservative_gate,
        "all_pass": all_pass,
        "selected": "r337_top10_b40_three_asset_cap33_posthoc" if all_pass else None,
        "decision": {"status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed", "post_hoc": True, "paper_target_generated": False, "order_file_generated": False, "production_write": False, "provider_write": False, "parameter_search": False},
        "input_isolation": {"r40_before": before_r40, "r40_after": after_r40, "production_before": before_prod, "production_after": after_prod, "stable": before_r40 == after_r40 and before_prod == after_prod},
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "center_2x": center_metrics["stats"],
        "center_yearly": center_metrics["yearly"],
        "center_recent": center_metrics["recent"],
        "3x_conservative": conservative_metrics["stats"],
        "4x": cost_metrics["4x"]["stats"],
        "5x": cost_metrics["5x"]["stats"],
        "concentration_2x": c2_summary["concentration"],
        "concentration_5x": c5_summary["concentration"],
        "plateau": payload["plateau_summary"],
        "cost_gates": cost_gates,
        "robustness_gates": robust_gates,
        "fresh_start_gate": fresh_gate,
        "all_pass": all_pass,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
