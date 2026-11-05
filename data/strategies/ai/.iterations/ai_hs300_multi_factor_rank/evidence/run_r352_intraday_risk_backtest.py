from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import statistics as st
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
PREREG = EVIDENCE / "r352_intraday_risk_preregistration.json"
PREFLIGHT = EVIDENCE / "r352_intraday_risk_preflight.json"
R344_RESULT = EVIDENCE / "r344_extended_window.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
FACTOR_FILE = DATA / "user_data/custom_factors/uf_intraday_risk_share.json"
RUN_CACHE = DATA / "research" / "r352_cache"
REUSED_AB_CACHE = DATA / "research" / "r347_cache"
OUTPUT = EVIDENCE / "r352_intraday_risk_backtest.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_intraday_risk_h60"
CONTROL_C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
STRATEGIES = (A_STRATEGY, B_STRATEGY, C_STRATEGY)
SLEEVES = {A_STRATEGY: "A", B_STRATEGY: "B", C_STRATEGY: "C", CONTROL_C_STRATEGY: "C"}
POSITIONS = {A_STRATEGY: 10, B_STRATEGY: 10, C_STRATEGY: 50, CONTROL_C_STRATEGY: 50}
C_WEIGHT_CAP = 0.10
CAP_GRID = [0.05, 0.10, 0.15, 0.20]
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
LOOKBACKS = [40, 60, 90]
TARGETS = [0.12, 0.15, 0.18]
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


def _three_sleeve_targets(dates: list[date], padded_returns: list[list[float]], c_weight_cap: float) -> list[tuple[date, tuple[float, float, float]]]:
    targets = []
    current = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    previous_month = None
    for index, day in enumerate(dates):
        month = (day.year, day.month)
        if index == 0 or month != previous_month:
            if index < 60:
                weights = [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]
            else:
                inv = [1.0 / max(st.pstdev(values[index - 60:index]), 1e-6) for values in padded_returns]
                total = sum(inv)
                weights = [value / total for value in inv]
                for _ in range(10):
                    clipped = [min(0.60, max(0.20, value)) for value in weights]
                    clipped_total = sum(clipped)
                    weights = [value / clipped_total for value in clipped]
                    if all(0.20 - 1e-12 <= value <= 0.60 + 1e-12 for value in weights):
                        break
            if weights[2] > c_weight_cap:
                residual = 1.0 - c_weight_cap
                ab_total = weights[0] + weights[1]
                weights = [residual * weights[0] / ab_total, residual * weights[1] / ab_total, c_weight_cap]
            current = tuple(weights)
            targets.append((day, current))
            previous_month = month
    return targets


def _combine_three(curves: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame], round_trip_cost: float, c_weight_cap: float) -> tuple[pl.DataFrame, list[dict]]:
    joined = (
        curves[0].rename({"value": "a"})
        .join(curves[1].rename({"value": "b"}), on="date", how="inner")
        .join(curves[2].rename({"value": "c"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values = {key: joined[key].to_list() for key in ("a", "b", "c")}
    returns = {
        key: [values[key][index] / values[key][index - 1] - 1.0 for index in range(1, len(dates))]
        for key in ("a", "b", "c")
    }
    padded = [[0.0, *returns[key]] for key in ("a", "b", "c")]
    targets = _three_sleeve_targets(dates, padded, c_weight_cap)
    target_map = {day: weights for day, weights in targets}
    target = target_map[dates[0]]
    sleeve_values = [1.0, 1.0, 1.0]
    portfolio = 1.0
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight_a": target[0], "weight_b": target[1], "weight_c": target[2]}]
    for index in range(1, len(dates)):
        day = dates[index]
        daily = [returns[key][index - 1] for key in ("a", "b", "c")]
        sleeve_values = [sleeve_values[i] * (1.0 + daily[i]) for i in range(3)]
        portfolio *= 1.0 + sum(target[i] * daily[i] for i in range(3))
        next_month = index + 1 == len(dates) or (dates[index + 1].year, dates[index + 1].month) != (day.year, day.month)
        if next_month:
            gross = sum(target[i] * sleeve_values[i] for i in range(3))
            actual = [target[i] * sleeve_values[i] / gross if gross > 0 else target[i] for i in range(3)]
            next_target = target_map[dates[index + 1]] if index + 1 < len(dates) else target
            turnover = 0.5 * sum(abs(next_target[i] - actual[i]) for i in range(3))
            portfolio *= 1.0 - turnover * round_trip_cost
            sleeve_values = [1.0, 1.0, 1.0]
            target = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and index + 1 < len(dates):
            weight_records.append({"date": dates[index + 1], "weight_a": target[0], "weight_b": target[1], "weight_c": target[2]})
    return pl.DataFrame(rows), weight_records


def _corr(left: pl.DataFrame, right: pl.DataFrame) -> float:
    joined = left.rename({"value": "left"}).join(right.rename({"value": "right"}), on="date", how="inner").sort("date")
    lv = joined["left"].to_list()
    rv = joined["right"].to_list()
    lr = [lv[index] / lv[index - 1] - 1.0 for index in range(1, len(lv))]
    rr = [rv[index] / rv[index - 1] - 1.0 for index in range(1, len(rv))]
    if len(lr) < 20 or st.pstdev(lr) == 0 or st.pstdev(rr) == 0:
        return 1.0
    return st.correlation(lr, rr)


def gold_trend_proxy(gold: pl.DataFrame, treasury: pl.DataFrame, dividend: pl.DataFrame, round_trip_cost: float) -> pl.DataFrame:
    joined = (
        gold.rename({"close": "gold"})
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    gold_values = joined["gold"].to_list()
    treasury_values = joined["treasury"].to_list()
    dividend_values = joined["dividend"].to_list()
    gold_on = [
        True if index < 199 else gold_values[index] > sum(gold_values[index - 199:index + 1]) / 200.0
        for index in range(len(gold_values))
    ]
    prices = [100.0]
    composition_on = gold_on[0]
    for index in range(len(dates) - 1):
        switched = False
        if index > 0 and (dates[index].year, dates[index].month) != (dates[index - 1].year, dates[index - 1].month) and gold_on[index] != composition_on:
            composition_on = gold_on[index]
            switched = True
        gold_return = gold_values[index + 1] / gold_values[index] - 1.0
        treasury_return = treasury_values[index + 1] / treasury_values[index] - 1.0
        dividend_return = dividend_values[index + 1] / dividend_values[index] - 1.0
        daily_return = gold_return if composition_on else 0.5 * (treasury_return + dividend_return)
        if switched:
            daily_return -= round_trip_cost / 3.0
        prices.append(prices[-1] * (1.0 + daily_return))
    return pl.DataFrame({"date": dates, "close": prices})


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R352 result exists; refusing overwrite")
    for path in (PREREG, PREFLIGHT, R344_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight["all_checks"] or not preflight["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R352 preflight did not pass")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg["status"] != "pre_registered_before_r352_portfolio_backtest" or prereg["round"] != "R352":
        raise RuntimeError("R352 preregistration status is invalid")
    mutation = prereg["single_mutation"]
    if mutation["removed_component"]["factor"] != "uf_low_vol" or mutation["added_component"]["factor"] != "uf_intraday_risk_share":
        raise RuntimeError("R352 preregistration is not the frozen single-factor substitution")
    if sha256(FACTOR_FILE) != prereg["artifact_sha256"]["factor_definition"]:
        raise RuntimeError("R352 factor definition hash changed")
    r344_result = json.loads(R344_RESULT.read_text(encoding="utf-8"))
    if r344_result["all_pass"] or r344_result["selected"] is not None:
        raise RuntimeError("R352 requires R344 to remain the documented no-candidate control")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r352")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r352")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r352")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r352")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r352")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r352")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r352")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r352")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r352_"))
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
    a_strategy = engine.get(A_STRATEGY)
    b_strategy = engine.get(B_STRATEGY)
    c_strategy = engine.get(C_STRATEGY)
    control_c_strategy = engine.get(CONTROL_C_STRATEGY)
    if a_strategy is None or b_strategy is None or c_strategy is None or control_c_strategy is None:
        raise RuntimeError("R352 required strategy failed to load")
    if a_strategy.max_hold_days != 35 or a_strategy.stop_loss is not None:
        raise RuntimeError("R352 A strategy definition changed")
    if int(a_strategy.meta.get("limit", 0)) < POSITIONS[A_STRATEGY]:
        raise RuntimeError("R352 A strategy candidate limit is below Top10")
    if b_strategy.max_hold_days != 20 or b_strategy.stop_loss != -0.20:
        raise RuntimeError("R352 B strategy definition changed")
    if int(b_strategy.meta.get("limit", 0)) < POSITIONS[B_STRATEGY]:
        raise RuntimeError("R352 B strategy candidate limit is below Top10")
    expected_c_scoring = {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_intraday_risk_share": 0.15,
    }
    expected_neutralize = {
        "industry": "industry_l1",
        "styles": [],
        "exclude": ["momentum_60d", "uf_quality_value_defensive", "uf_cash_quality"],
        "shrink": 1.0,
    }
    if c_strategy.max_hold_days != 60 or c_strategy.stop_loss is not None:
        raise RuntimeError("R352 C strategy execution changed")
    if c_strategy.meta.get("scoring") != expected_c_scoring or c_strategy.meta.get("neutralize") != expected_neutralize:
        raise RuntimeError("R352 C factor substitution changed")
    if control_c_strategy.max_hold_days != 60 or control_c_strategy.meta.get("scoring") != {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    }:
        raise RuntimeError("R352 control C strategy changed")
    prereg_hashes = prereg["artifact_sha256"]
    hash_keys = {
        A_STRATEGY: "strategy_A",
        B_STRATEGY: "strategy_B",
        C_STRATEGY: "candidate_strategy",
        CONTROL_C_STRATEGY: "strategy_C_control",
    }
    for strategy_id, hash_key in hash_keys.items():
        if sha256(engine.get(strategy_id).file_path) != prereg_hashes[hash_key]:
            raise RuntimeError(f"R352 strategy hash changed: {strategy_id}")
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)

    def run(strategy_id: str, start: date, cost_factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        max_positions = POSITIONS[strategy_id]
        key = f"{strategy_id}_{start}_{cost_factor}x_p{max_positions}"
        cache_dir = RUN_CACHE if strategy_id == C_STRATEGY else REUSED_AB_CACHE
        curve_path = cache_dir / f"{key}.parquet"
        trades_path = cache_dir / f"{key}.trades.json"
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
            curve = r324.curve_frame(result.equity_curve)
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture_trades:
                trades = [{
                    "sleeve": SLEEVES[strategy_id],
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
            for strategy_id in STRATEGIES:
                capture = factor in (2, 5)
                curve, trades = run(strategy_id, START, factor, capture_trades=capture)
                full[factor][strategy_id] = curve
                if capture:
                    full_trades[(factor, strategy_id)] = trades
        control_c_curve, control_c_trades = run(CONTROL_C_STRATEGY, START, 2, capture_trades=True)
        phase_curves = {}
        for phase in PHASES:
            phase_curves[phase] = {}
            for strategy_id in STRATEGIES:
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
        risk[factor], rp[factor] = _combine_three(
            (full[factor][A_STRATEGY], full[factor][B_STRATEGY], full[factor][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        equity[factor] = r114._vol_target(risk[factor])["curve"]
    control_risk, _ = r112._combine(full[2][A_STRATEGY], full[2][B_STRATEGY], "risk")
    control_equity = r114._vol_target(control_risk)["curve"]

    def build(equity_curve: pl.DataFrame, lookback: int, overlay_target: float, overlay_cost: float, symbols: dict | None = None) -> dict:
        chosen = symbols or r331.PRIMARY
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            gold_asset = r331.asset_frame("gold", chosen["gold"])
            treasury_asset = r331.asset_frame("treasury", chosen["treasury"])
            dividend_asset = r331.asset_frame("dividend", chosen["dividend"])
            return r331.build_three_asset_bucket(
                equity_curve,
                gold_trend_proxy(gold_asset, treasury_asset, dividend_asset, overlay_cost),
                treasury_asset,
                dividend_asset,
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
    control_bucket = build(control_equity, 60, 0.15, OVERLAY_COST[2])
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    def concentration(factor: int) -> tuple[dict, dict, int]:
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(factor, strategy_id)]]
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
                sleeve_share = r326.weight_at(rp[factor], day, f"weight_{trade['sleeve'].lower()}")
                scales.append(equity_weight * sleeve_share)
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
    extension_2022 = r112._stats(
        core[2]["curve"].filter(pl.col("date").is_between(START, date(2022, 12, 31))).sort("date")
    )
    prewindow = r112._stats(
        core[2]["curve"].filter(pl.col("date").is_between(START, date(2023, 5, 3))).sort("date")
    )
    extension_gates = {
        "T1_2022_partial_return": extension_2022["total_return"] > 0,
        "T2_2022_partial_mdd": extension_2022["mdd"] >= -0.15,
        "T3_2023_full_year": center_metrics["yearly"].get("2023", -1.0) >= 0,
        "T4_prewindow_return": prewindow["total_return"] > 0,
        "T5_prewindow_sharpe": prewindow["sharpe"] >= 0.30,
        "T6_prewindow_mdd": prewindow["mdd"] >= -0.15,
    }
    conservative_metrics = metrics(core_conservative_3x["curve"])
    cost_metrics = {
        "4x": metrics(core[4]["curve"]),
        "5x": metrics(core[5]["curve"]),
    }
    c2_gates, c2_summary = concentration_gates(2)
    c5_gates, c5_summary = concentration_gates(5)

    def active_symbols(trades: list[dict], day: date) -> set[str]:
        active = set()
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            if entry <= day <= exit_ and trade.get("symbol"):
                active.add(str(trade["symbol"]))
        return active

    candidate_c_trades = full_trades[(2, C_STRATEGY)]
    overlap_values = []
    for day in full[2][C_STRATEGY]["date"].to_list():
        candidate_active = active_symbols(candidate_c_trades, day)
        control_active = active_symbols(control_c_trades, day)
        union = candidate_active | control_active
        if union:
            overlap_values.append(len(candidate_active & control_active) / len(union))
    candidate_c_stats = r112._stats(full[2][C_STRATEGY])
    control_c_stats = r112._stats(control_c_curve)
    c_sleeve_diagnostics = {
        "candidate": candidate_c_stats,
        "control": control_c_stats,
        "annual_delta": round(candidate_c_stats["annual"] - control_c_stats["annual"], 6),
        "sharpe_delta": round(candidate_c_stats["sharpe"] - control_c_stats["sharpe"], 6),
        "mdd_delta": round(candidate_c_stats["mdd"] - control_c_stats["mdd"], 6),
        "daily_return_correlation": round(_corr(full[2][C_STRATEGY], control_c_curve), 6),
        "average_active_holding_jaccard": round(sum(overlap_values) / len(overlap_values), 6) if overlap_values else None,
        "overlap_days": len(overlap_values),
        "candidate_trades": len(candidate_c_trades),
        "control_trades": len(control_c_trades),
    }
    correlations = {
        "AB": round(_corr(full[2][A_STRATEGY], full[2][B_STRATEGY]), 6),
        "AC": round(_corr(full[2][A_STRATEGY], full[2][C_STRATEGY]), 6),
        "BC": round(_corr(full[2][B_STRATEGY], full[2][C_STRATEGY]), 6),
    }
    mean_pairwise_correlation = round(sum(correlations.values()) / 3.0, 6)
    sleeve_weights = {
        key: round(sum(float(row[f"weight_{key.lower()}"]) for row in rp[2]) / len(rp[2]), 6)
        for key in ("A", "B", "C")
    }
    ab_control_stats = r112._stats(control_bucket["curve"])
    r344_control = json.loads(R344_RESULT.read_text(encoding="utf-8"))
    r344_control_stats = r344_control["center_2x"]["stats"]
    performance_gates = {
        "G1_return": center_metrics["stats"]["annual"] >= 0.12,
        "G2_drawdown": center_metrics["stats"]["mdd"] >= -0.15,
        "G3_sharpe": center_metrics["stats"]["sharpe"] >= 1.25,
        "G4_calendar": center_metrics["yearly"].get("2024", -1.0) >= 0.0 and center_metrics["yearly"].get("2025", -1.0) >= 0.0,
        "G5_recent": center_metrics["recent"]["total_return"] > 0,
        "G6_bootstrap": center_metrics["bootstrap"]["positive_rate"] >= 0.80 and center_metrics["bootstrap"]["return_p05"] >= -0.12,
        "G7_rolling": center_metrics["rolling"]["positive_rate"] >= 0.70 and center_metrics["rolling"]["return_worst"] >= -0.15,
        "G8_stress": conservative_metrics["stats"]["total_return"] > 0 and conservative_metrics["stats"]["mdd"] >= -0.25,
        "G9_annual": center_metrics["stats"]["annual"] >= r344_control_stats["annual"] - 0.01,
        "G9_sharpe": center_metrics["stats"]["sharpe"] >= r344_control_stats["sharpe"] - 0.10,
        "G9_mdd": center_metrics["stats"]["mdd"] >= r344_control_stats["mdd"] - 0.005,
        "G10_no_leverage": core[2]["weight_summary"]["max_weight_sum"] <= 1.0 and core[2]["weight_summary"]["min_target_weight"] >= 0.0,
        "G11_cap": sleeve_weights["C"] <= C_WEIGHT_CAP + 0.005,
    }
    robust = {}
    for name, symbols in r331.ROBUSTNESS.items():
        bucket = build(equity[2], 60, 0.15, OVERLAY_COST[2], symbols)
        robust[name] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    robust_gates = {
        name: (
            item["stats"]["annual"] >= 0.05
            and item["stats"]["mdd"] >= -0.20
            and item["yearly"].get("2024", -1.0) >= 0.0
            and item["yearly"].get("2025", -1.0) >= 0.0
        )
        for name, item in robust.items()
    }
    fresh = {}
    for phase in PHASES:
        phase_risk, _ = _combine_three(
            (phase_curves[phase][A_STRATEGY], phase_curves[phase][B_STRATEGY], phase_curves[phase][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        phase_equity = r114._vol_target(phase_risk)["curve"]
        bucket = build(phase_equity, 60, 0.15, OVERLAY_COST[2])
        fresh[str(phase)] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    fresh_gate = sum(1 for item in fresh.values() if item["stats"]["total_return"] > 0) >= 5 and all(item["stats"]["mdd"] >= -0.18 for item in fresh.values())
    fresh_extension_gate = fresh[str(START)]["stats"]["total_return"] > 0

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
            "G1": m["stats"]["annual"] >= 0.12,
            "G2": m["stats"]["mdd"] >= -0.15,
            "G3": m["stats"]["sharpe"] >= 1.25,
            "G4": m["yearly"].get("2024", -1.0) >= 0.0 and m["yearly"].get("2025", -1.0) >= 0.0,
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
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(2, strategy_id)]]
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
                sleeve = r326.weight_at(rp_records, day, f"weight_{trade['sleeve'].lower()}")
                scales.append(eqw * sleeve)
            scale = sum(scales) / len(scales)
            scaled.append({**trade, "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    def concentration_at_5x(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> dict:
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(5, strategy_id)]]
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
                sleeve = r326.weight_at(rp_records, day, f"weight_{trade['sleeve'].lower()}")
                scales.append(eqw * sleeve)
            scale = sum(scales) / len(scales)
            scaled.append({**trade, "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        return r239._aggregate(scaled, industry)

    def eval_cap(cap: float) -> dict:
        risk_2x, _ = _combine_three(
            (full[2][A_STRATEGY], full[2][B_STRATEGY], full[2][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            cap,
        )
        risk_5x, rp_5x = _combine_three(
            (full[5][A_STRATEGY], full[5][B_STRATEGY], full[5][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            cap,
        )
        equity_2x = r114._vol_target(risk_2x)["curve"]
        equity_5x = r114._vol_target(risk_5x)["curve"]
        bucket_2x = build(equity_2x, 60, 0.15, OVERLAY_COST[2])
        bucket_5x = build(equity_5x, 60, 0.15, OVERLAY_COST[5])
        m2 = metrics(bucket_2x["curve"])
        m5 = metrics(bucket_5x["curve"])
        conc_5x = concentration_at_5x(bucket_5x, rp_5x, equity_5x)
        fresh_positive = 0
        for phase in PHASES:
            phase_risk, _ = _combine_three(
                (phase_curves[phase][A_STRATEGY], phase_curves[phase][B_STRATEGY], phase_curves[phase][C_STRATEGY]),
                r112.ROUND_TRIP_COST,
                cap,
            )
            phase_equity = r114._vol_target(phase_risk)["curve"]
            phase_bucket = build(phase_equity, 60, 0.15, OVERLAY_COST[2])
            fresh_positive += r112._stats(phase_bucket["curve"])["total_return"] > 0
        gates = {
            "2x_annual": m2["stats"]["annual"] >= 0.12,
            "2x_sharpe": m2["stats"]["sharpe"] >= 1.25,
            "2x_mdd": m2["stats"]["mdd"] >= -0.15,
            "5x_annual": m5["stats"]["annual"] >= 0.10,
            "5x_sharpe": m5["stats"]["sharpe"] >= 1.10,
            "5x_mdd": m5["stats"]["mdd"] >= -0.15,
            "5x_C2": conc_5x["total_pnl_amount"] > 0 and conc_5x["top1_net_share"] <= 0.25,
            "5x_C3": conc_5x["total_pnl_amount"] > 0 and conc_5x["top3_net_share"] <= 0.55,
            "5x_C4": conc_5x["total_pnl_amount"] > 0 and conc_5x["top5_net_share"] <= 0.75,
            "fresh": fresh_positive >= 5,
        }
        return {
            "cap": cap,
            "metrics_2x": m2,
            "metrics_5x": m5,
            "concentration_5x": {key: conc_5x.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross", "total_pnl_amount")},
            "fresh_positive": fresh_positive,
            "gates": gates,
            "all_pass": all(gates.values()),
        }

    cap_results = {f"{cap:.2f}": eval_cap(cap) for cap in CAP_GRID}
    cap_passes = sum(1 for item in cap_results.values() if item["all_pass"])
    cap_plateau_pass = cap_passes >= 3 and cap_results[f"{C_WEIGHT_CAP:.2f}"]["all_pass"]

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
        "5x_performance": cost_metrics["5x"]["stats"]["total_return"] > 0 and cost_metrics["5x"]["stats"]["sharpe"] >= 0.50 and cost_metrics["5x"]["stats"]["mdd"] >= -0.25 and cost_metrics["5x"]["yearly"].get("2024", -1.0) >= 0.0 and cost_metrics["5x"]["yearly"].get("2025", -1.0) >= 0.0 and cost_metrics["5x"]["recent"]["total_return"] > 0,
        "5x_concentration": all(c5_gates.values()),
    }
    conservative_gate = conservative_metrics["stats"]["total_return"] > 0 and conservative_metrics["stats"]["mdd"] >= -0.25
    data_gates = {
        "D1_preflight": bool(preflight["all_checks"] and preflight["no_return_or_backtest_evaluated"]),
        "D2_curves": all(engine.has(strategy_id) for strategy_id in STRATEGIES) and all(full[2][strategy_id]["date"].min() == START and full[2][strategy_id]["date"].max() == END for strategy_id in STRATEGIES),
        "D3_input_isolation": before_r40 == after_r40 and before_prod == after_prod,
        "D4_correlation": mean_pairwise_correlation < 0.85,
    }
    all_pass = (
        all(data_gates.values())
        and all(performance_gates.values())
        and all(extension_gates.values())
        and all(c2_gates.values())
        and all(robust_gates.values())
        and fresh_gate
        and fresh_extension_gate
        and all(cost_gates.values())
        and conservative_gate
        and plateau_pass
        and cap_plateau_pass
    )
    payload = {
        "round": "R352",
        "candidate_id": prereg["candidate_id"],
        "classification": prereg["classification"],
        "hypothesis": prereg["hypothesis"],
        "preregistration_sha256": sha256(PREREG),
        "r352_preflight_sha256": sha256(PREFLIGHT),
        "r344_control_result_sha256": sha256(R344_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "factor_definition": {
            "file": str(FACTOR_FILE.relative_to(ROOT)),
            "sha256": sha256(FACTOR_FILE),
            "formula": prereg["single_mutation"]["added_component"]["formula"],
        },
        "runner_sha256": sha256(Path(__file__).resolve()),
        "strategy_files": {
            "A": {
                "id": A_STRATEGY,
                "sha256": sha256(engine.get(A_STRATEGY).file_path),
                "max_hold_days": engine.get(A_STRATEGY).max_hold_days,
                "stop_loss": engine.get(A_STRATEGY).stop_loss,
                "max_positions": POSITIONS[A_STRATEGY],
            },
            "B": {
                "id": B_STRATEGY,
                "sha256": sha256(engine.get(B_STRATEGY).file_path),
                "max_hold_days": engine.get(B_STRATEGY).max_hold_days,
                "stop_loss": engine.get(B_STRATEGY).stop_loss,
                "max_positions": POSITIONS[B_STRATEGY],
            },
            "C_candidate": {
                "id": C_STRATEGY,
                "sha256": sha256(engine.get(C_STRATEGY).file_path),
                "max_hold_days": engine.get(C_STRATEGY).max_hold_days,
                "stop_loss": engine.get(C_STRATEGY).stop_loss,
                "max_positions": POSITIONS[C_STRATEGY],
            },
            "C_control": {
                "id": CONTROL_C_STRATEGY,
                "sha256": sha256(engine.get(CONTROL_C_STRATEGY).file_path),
                "max_hold_days": engine.get(CONTROL_C_STRATEGY).max_hold_days,
                "stop_loss": engine.get(CONTROL_C_STRATEGY).stop_loss,
                "max_positions": POSITIONS[CONTROL_C_STRATEGY],
            },
        },
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)]},
        "data_gates": data_gates,
        "correlations": correlations,
        "mean_pairwise_correlation": mean_pairwise_correlation,
        "sleeve_weights": sleeve_weights,
        "center_2x": center_metrics,
        "c_sleeve_diagnostics": c_sleeve_diagnostics,
        "r344_control_2x": {
            "stats": r344_control_stats,
            "yearly": r344_control["center_2x"]["yearly"],
            "recent": r344_control["center_2x"]["recent"],
            "extension_2022": r344_control["extension_2022"],
            "prewindow": r344_control["prewindow"],
            "concentration_5x": r344_control["concentration_5x"]["concentration"],
        },
        "ab_control_2x": {"stats": ab_control_stats, "yearly": r315.year_returns(control_bucket["curve"])},
        "extension_2022": extension_2022,
        "prewindow": prewindow,
        "extension_gates": extension_gates,
        "fresh_extension_gate": fresh_extension_gate,
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
        "c_satellite_cap": C_WEIGHT_CAP,
        "gold_proxy": {"rule": "monthly close > 200-day MA uses gold; otherwise 50/50 treasury+dividend", "switch_cost_fraction": 1.0 / 3.0},
        "cap_plateau": cap_results,
        "cap_plateau_summary": {"passes": cap_passes, "required": 3, "center_pass": cap_results[f"{C_WEIGHT_CAP:.2f}"]["all_pass"], "plateau_pass": cap_plateau_pass},
        "cost_gates": cost_gates,
        "conservative_3x_gate": conservative_gate,
        "all_pass": all_pass,
        "selected": prereg["candidate_id"] if all_pass else None,
        "decision": {"status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed", "post_hoc": True, "paper_target_generated": False, "order_file_generated": False, "production_write": False, "provider_write": False, "parameter_search": False},
        "input_isolation": {"r40_before": before_r40, "r40_after": after_r40, "production_before": before_prod, "production_after": after_prod, "stable": before_r40 == after_r40 and before_prod == after_prod},
        "limits": prereg["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "center_2x": center_metrics["stats"],
        "center_yearly": center_metrics["yearly"],
        "extension_2022": extension_2022,
        "prewindow": prewindow,
        "extension_gates": extension_gates,
        "center_recent": center_metrics["recent"],
        "r344_control": r344_control_stats,
        "c_sleeve_diagnostics": c_sleeve_diagnostics,
        "correlations": correlations,
        "sleeve_weights": sleeve_weights,
        "data_gates": data_gates,
        "3x_conservative": conservative_metrics["stats"],
        "4x": cost_metrics["4x"]["stats"],
        "5x": cost_metrics["5x"]["stats"],
        "concentration_2x": c2_summary["concentration"],
        "concentration_5x": c5_summary["concentration"],
        "plateau": payload["plateau_summary"],
        "cap_plateau": payload["cap_plateau_summary"],
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
