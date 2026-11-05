from __future__ import annotations

# ruff: noqa: E402
import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
ASSET_CACHE = DATA / "research" / "r331_defensive_assets"
RUN_CACHE = DATA / "research" / "r331_cache_v2"
PREREG = EVIDENCE / "r331_three_asset_defensive_bucket_preregistration.json"
AMENDMENT = EVIDENCE / "r331_preregistration_amendment_A1.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
OUTPUT = EVIDENCE / "r331_three_asset_bucket_v2.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
LOOKBACK = 60
TARGET_VOL = 0.15
EQUITY_WEIGHT_MIN = 0.20
EQUITY_WEIGHT_MAX = 0.80
GROSS_MIN = 0.20
GROSS_MAX = 1.00
MAIN_COST = 0.001
STRESS_COST = 0.003
PRIMARY = {"gold": "518880.SH", "treasury": "511260.SH", "dividend": "510880.SH"}
ROBUSTNESS = {
    "R1_dividend": {"gold": "518880.SH", "treasury": "511260.SH", "dividend": "515080.SH"},
    "R2_gold": {"gold": "159937.SZ", "treasury": "511260.SH", "dividend": "510880.SH"},
    "R3_treasury": {"gold": "518880.SH", "treasury": "511010.SH", "dividend": "510880.SH"},
}

sys.path.insert(0, str(ROOT / "backend"))
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def latest_partition_date(root: Path) -> date | None:
    paths = sorted(root.glob("date=*"))
    if not paths:
        return None
    return date.fromisoformat(paths[-1].name.split("=")[1])


def asset_frame(kind: str, symbol: str) -> pl.DataFrame:
    names = {"gold": "gold_etf_qfq.parquet", "treasury": "treasury_etf_qfq.parquet", "dividend": "dividend_etf_qfq.parquet"}
    path = ASSET_CACHE / names[kind]
    frame = pl.read_parquet(path)
    return frame.filter(pl.col("symbol") == symbol).select(["date", "close"]).sort("date")


def trailing_weights(
    equity_returns: list[float],
    gold_returns: list[float],
    treasury_returns: list[float],
    dividend_returns: list[float],
) -> tuple[float, float]:
    if len(equity_returns) < LOOKBACK:
        return 0.5, 0.5
    equity_window = equity_returns[-LOOKBACK:]
    gold_window = gold_returns[-LOOKBACK:]
    treasury_window = treasury_returns[-LOOKBACK:]
    dividend_window = dividend_returns[-LOOKBACK:]
    defensive_returns = [
        (gold + treasury + dividend) / 3.0
        for gold, treasury, dividend in zip(gold_window, treasury_window, dividend_window, strict=True)
    ]
    vol_equity = max(st.pstdev(equity_window) * math.sqrt(252.0), 1e-8)
    vol_defensive = max(st.pstdev(defensive_returns) * math.sqrt(252.0), 1e-8)
    equity_share = (1.0 / vol_equity) / ((1.0 / vol_equity) + (1.0 / vol_defensive))
    equity_share = min(EQUITY_WEIGHT_MAX, max(EQUITY_WEIGHT_MIN, equity_share))
    mean_equity = st.mean(equity_window)
    mean_defensive = st.mean(defensive_returns)
    covariance = sum(
        (equity - mean_equity) * (defensive - mean_defensive)
        for equity, defensive in zip(equity_window, defensive_returns, strict=True)
    ) / LOOKBACK
    variance = (
        equity_share**2 * vol_equity**2
        + (1.0 - equity_share) ** 2 * vol_defensive**2
        + 2.0 * equity_share * (1.0 - equity_share) * covariance * 252.0
    )
    portfolio_vol = math.sqrt(max(variance, 1e-12))
    gross = min(GROSS_MAX, max(GROSS_MIN, TARGET_VOL / portfolio_vol))
    return equity_share, gross


def build_three_asset_bucket(
    equity_curve: pl.DataFrame,
    gold: pl.DataFrame,
    treasury: pl.DataFrame,
    dividend: pl.DataFrame,
    *,
    round_trip_cost: float,
) -> dict:
    joined = (
        equity_curve.rename({"value": "equity"})
        .join(gold.rename({"close": "gold"}), on="date", how="inner")
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    if joined.height < LOOKBACK + 2:
        raise RuntimeError("not enough aligned defensive-asset observations")
    dates = joined["date"].to_list()
    equity_values = joined["equity"].to_list()
    gold_values = joined["gold"].to_list()
    treasury_values = joined["treasury"].to_list()
    dividend_values = joined["dividend"].to_list()
    equity_returns = [equity_values[i] / equity_values[i - 1] - 1.0 for i in range(1, len(equity_values))]
    gold_returns = [gold_values[i] / gold_values[i - 1] - 1.0 for i in range(1, len(gold_values))]
    treasury_returns = [treasury_values[i] / treasury_values[i - 1] - 1.0 for i in range(1, len(treasury_values))]
    dividend_returns = [dividend_values[i] / dividend_values[i - 1] - 1.0 for i in range(1, len(dividend_values))]

    equity_share, gross = trailing_weights([], [], [], [])
    equity_weight = equity_share * gross
    gold_weight = treasury_weight = dividend_weight = (1.0 - equity_share) * gross / 3.0
    cash_weight = 1.0 - gross
    equity_growth = gold_growth = treasury_growth = dividend_growth = 1.0
    segment_base = 1.0 * (1.0 - round_trip_cost * 0.5 * (equity_weight + gold_weight + treasury_weight + dividend_weight + cash_weight))
    rows = [{"date": dates[0], "value": 1.0}]
    rebalances = [{
        "date": str(dates[0]),
        "equity_share": equity_share,
        "gross_exposure": gross,
        "target_equity_weight": equity_weight,
        "target_gold_weight": gold_weight,
        "target_treasury_weight": treasury_weight,
        "target_dividend_weight": dividend_weight,
        "target_cash_weight": cash_weight,
        "turnover": 0.5 * (equity_weight + gold_weight + treasury_weight + dividend_weight + cash_weight),
    }]
    total_turnover = rebalances[0]["turnover"]
    total_cost = (segment_base < 1.0 and (1.0 - segment_base)) or 0.0
    contributions = {"equity": 0.0, "gold": 0.0, "treasury": 0.0, "dividend": 0.0}

    for i in range(1, len(dates)):
        if (dates[i].year, dates[i].month) != (dates[i - 1].year, dates[i - 1].month):
            growth = (
                equity_weight * equity_growth
                + gold_weight * gold_growth
                + treasury_weight * treasury_growth
                + dividend_weight * dividend_growth
                + cash_weight
            )
            total_before = segment_base * growth
            actual_equity = equity_weight * equity_growth / growth
            actual_gold = gold_weight * gold_growth / growth
            actual_treasury = treasury_weight * treasury_growth / growth
            actual_dividend = dividend_weight * dividend_growth / growth
            actual_cash = cash_weight / growth
            history_edge = max(0, i - 1)
            equity_share, gross = trailing_weights(
                equity_returns[:history_edge],
                gold_returns[:history_edge],
                treasury_returns[:history_edge],
                dividend_returns[:history_edge],
            )
            target_equity = equity_share * gross
            target_gold = target_treasury = target_dividend = (1.0 - equity_share) * gross / 3.0
            target_cash = 1.0 - gross
            turnover = 0.5 * (
                abs(target_equity - actual_equity)
                + abs(target_gold - actual_gold)
                + abs(target_treasury - actual_treasury)
                + abs(target_dividend - actual_dividend)
                + abs(target_cash - actual_cash)
            )
            total_turnover += turnover
            total_cost += total_before * round_trip_cost * turnover
            segment_base = total_before * (1.0 - round_trip_cost * turnover)
            equity_weight, gold_weight = target_equity, target_gold
            treasury_weight, dividend_weight, cash_weight = target_treasury, target_dividend, target_cash
            equity_growth = gold_growth = treasury_growth = dividend_growth = 1.0
            rebalances.append({
                "date": str(dates[i]),
                "equity_share": equity_share,
                "gross_exposure": gross,
                "target_equity_weight": equity_weight,
                "target_gold_weight": gold_weight,
                "target_treasury_weight": treasury_weight,
                "target_dividend_weight": dividend_weight,
                "target_cash_weight": cash_weight,
                "turnover": turnover,
            })

        previous_equity_growth = equity_growth
        previous_gold_growth = gold_growth
        previous_treasury_growth = treasury_growth
        previous_dividend_growth = dividend_growth
        equity_growth *= 1.0 + equity_returns[i - 1]
        gold_growth *= 1.0 + gold_returns[i - 1]
        treasury_growth *= 1.0 + treasury_returns[i - 1]
        dividend_growth *= 1.0 + dividend_returns[i - 1]
        contributions["equity"] += equity_weight * segment_base * (equity_growth - previous_equity_growth)
        contributions["gold"] += gold_weight * segment_base * (gold_growth - previous_gold_growth)
        contributions["treasury"] += treasury_weight * segment_base * (treasury_growth - previous_treasury_growth)
        contributions["dividend"] += dividend_weight * segment_base * (dividend_growth - previous_dividend_growth)
        value = segment_base * (
            equity_weight * equity_growth
            + gold_weight * gold_growth
            + treasury_weight * treasury_growth
            + dividend_weight * dividend_growth
            + cash_weight
        )
        rows.append({"date": dates[i], "value": value})

    target_keys = (
        "target_equity_weight",
        "target_gold_weight",
        "target_treasury_weight",
        "target_dividend_weight",
        "target_cash_weight",
    )
    weights = rebalances[1:] or rebalances
    return {
        "curve": pl.DataFrame(rows),
        "rebalances": rebalances,
        "aligned_dates": len(dates),
        "coverage": len(dates) / equity_curve.height,
        "total_turnover": total_turnover,
        "annual_turnover": total_turnover * 252.0 / max(len(dates) - 1, 1),
        "total_cost": total_cost,
        "contributions": contributions,
        "weight_summary": {
            "equity_weight_mean": round(st.mean(row["target_equity_weight"] for row in weights), 6),
            "gold_weight_mean": round(st.mean(row["target_gold_weight"] for row in weights), 6),
            "gold_weight_max": round(max(row["target_gold_weight"] for row in weights), 6),
            "treasury_weight_mean": round(st.mean(row["target_treasury_weight"] for row in weights), 6),
            "treasury_weight_max": round(max(row["target_treasury_weight"] for row in weights), 6),
            "dividend_weight_mean": round(st.mean(row["target_dividend_weight"] for row in weights), 6),
            "dividend_weight_max": round(max(row["target_dividend_weight"] for row in weights), 6),
            "gross_mean": round(st.mean(row["gross_exposure"] for row in weights), 6),
            "gross_min": round(min(row["gross_exposure"] for row in weights), 6),
            "gross_max": round(max(row["gross_exposure"] for row in weights), 6),
            "max_weight_sum": round(max(sum(row[key] for key in target_keys) for row in weights), 6),
            "min_target_weight": round(min(min(row[key] for key in target_keys) for row in weights), 6),
            "rebalances": len(rebalances),
        },
    }


def self_test() -> None:
    days = [date(2021, 1, 1) + timedelta(days=index) for index in range(180)]
    def curve(values: list[float], column: str) -> pl.DataFrame:
        return pl.DataFrame({"date": days, column: values})

    equity = curve([1.0 * 1.0004**index for index in range(180)], "value")
    gold = curve([100.0 * 1.0002**index for index in range(180)], "close")
    treasury = curve([100.0 * 1.00005**index for index in range(180)], "close")
    dividend = curve([100.0 * 1.0001**index for index in range(180)], "close")
    result = build_three_asset_bucket(equity, gold, treasury, dividend, round_trip_cost=MAIN_COST)
    assert result["curve"].height == 180
    assert all(math.isfinite(value) and value > 0 for value in result["curve"]["value"].to_list())
    assert result["coverage"] == 1.0
    assert result["weight_summary"]["max_weight_sum"] <= 1.0 + 1e-12
    assert result["weight_summary"]["min_target_weight"] >= 0.0
    assert set(result["contributions"]) == {"equity", "gold", "treasury", "dividend"}



def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R331 result exists; refusing overwrite")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R331 preregistration checksum mismatch")
    if sha256(AMENDMENT) != AMENDMENT.with_name(AMENDMENT.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R331 amendment checksum mismatch")
    if sha256(ASSET_MANIFEST) != ASSET_MANIFEST.with_name(ASSET_MANIFEST.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R331 asset manifest checksum mismatch")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("R331 asset manifest did not pass")
    for name, item in manifest["caches"].items():
        path = ROOT / item["path"]
        if sha256(path) != item["sha256"]:
            raise RuntimeError(f"R331 asset cache drift: {name}")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r331")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r331")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r331")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r331")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r331")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r331")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r331")
    self_test()
    r324.self_test()
    r114.TARGET_VOL = TARGET_VOL
    members = r313.membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="r331_"))
    service = None
    service_holder = None

    def service_context():
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        r40_latest = latest_partition_date(R40 / "kline_daily_enriched")
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
        load_into_registry(DATA)
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai"],
            override_loader=lambda sid: strategy_config.load_override(DATA, sid),
            industry_loader=industry_loader_for(DATA),
        )
        return StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

    def run(strategy_id: str, max_positions: int, cost_factor: int, *, capture_trades: bool):
        key = f"{strategy_id}_{max_positions}_{cost_factor}x_{START}_{END}"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        if curve_path.exists():
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            trades = json.loads(trades_path.read_text(encoding="utf-8")) if capture_trades and trades_path.exists() else []
            if capture_trades and not trades_path.exists():
                raise RuntimeError(f"trade cache missing for {key}")
            if curve["date"].min() != START or curve["date"].max() != END:
                raise RuntimeError(f"{key} curve coverage mismatch: {curve['date'].min()}..{curve['date'].max()}")
            return curve, trades
        nonlocal service, service_holder
        if service is None:
            service = service_context()
        cfg = StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=START,
            end=END,
            params={},
            overrides={},
            matching="open_t+1",
            fees_pct=0.0002 * cost_factor,
            commission_pct=0.0002 * cost_factor,
            stamp_tax_pct=0.0005 if cost_factor == 2 else 0.001,
            slippage_bps=5.0 * cost_factor,
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
            raise RuntimeError(f"{strategy_id} {max_positions} {cost_factor}x: {result.error}")
        curve = r324.curve_frame(result.equity_curve)
        if curve["date"].min() != START or curve["date"].max() != END:
            raise RuntimeError(f"{key} curve coverage mismatch: {curve['date'].min()}..{curve['date'].max()}")
        curve.write_parquet(curve_path, compression="zstd")
        trades = []
        if capture_trades:
            trades = [{
                "sleeve": "A" if strategy_id == A_STRATEGY else "B",
                "symbol": trade.get("symbol"),
                "name": trade.get("name"),
                "entry_date": str(trade.get("entry_date")),
                "exit_date": str(trade.get("exit_date")),
                "pnl_pct": trade.get("pnl_pct"),
                "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                "exit_reason": trade.get("exit_reason"),
            } for trade in result.trades]
            trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
        print(f"ran {strategy_id} top{max_positions} {cost_factor}x: {r112._stats(curve)}", flush=True)
        return curve, trades

    try:
        a20_2, a20_trades = run(A_STRATEGY, 20, 2, capture_trades=True)
        b20_2, b20_trades = run(B_STRATEGY, 20, 2, capture_trades=True)
        a20_3, _ = run(A_STRATEGY, 20, 3, capture_trades=False)
        b20_3, _ = run(B_STRATEGY, 20, 3, capture_trades=False)
        a10_2, a10_trades = run(A_STRATEGY, 10, 2, capture_trades=True)
        b10_2, b10_trades = run(B_STRATEGY, 10, 2, capture_trades=True)
    finally:
        service_holder = None
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    risk20_2, rp20_records = r112._combine(a20_2, b20_2, "risk")
    equity20_2 = r114._vol_target(risk20_2)["curve"]
    risk20_3, _ = r112._combine(a20_3, b20_3, "risk")
    equity20_3 = r114._vol_target(risk20_3)["curve"]
    risk10_2, rp10_records = r112._combine(a10_2, b10_2, "risk")
    equity10_2 = r114._vol_target(risk10_2)["curve"]

    primary = build_three_asset_bucket(
        equity20_2,
        asset_frame("gold", PRIMARY["gold"]),
        asset_frame("treasury", PRIMARY["treasury"]),
        asset_frame("dividend", PRIMARY["dividend"]),
        round_trip_cost=MAIN_COST,
    )
    stress = build_three_asset_bucket(
        equity20_3,
        asset_frame("gold", PRIMARY["gold"]),
        asset_frame("treasury", PRIMARY["treasury"]),
        asset_frame("dividend", PRIMARY["dividend"]),
        round_trip_cost=STRESS_COST,
    )
    diagnostic = build_three_asset_bucket(
        equity10_2,
        asset_frame("gold", PRIMARY["gold"]),
        asset_frame("treasury", PRIMARY["treasury"]),
        asset_frame("dividend", PRIMARY["dividend"]),
        round_trip_cost=MAIN_COST,
    )
    robust = {}
    for name, symbols in ROBUSTNESS.items():
        robust[name] = build_three_asset_bucket(
            equity20_2,
            asset_frame("gold", symbols["gold"]),
            asset_frame("treasury", symbols["treasury"]),
            asset_frame("dividend", symbols["dividend"]),
            round_trip_cost=MAIN_COST,
        )

    curve = primary["curve"]
    values = curve["value"].to_list()
    daily_returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    primary_stats = r112._stats(curve)
    primary_yearly = r315.year_returns(curve)
    recent_stats = r112._stats(r324.slice_curve(curve, RECENT_START, END))
    control_stats = r112._stats(equity20_2)
    control_yearly = r315.year_returns(equity20_2)
    stress_stats = r112._stats(stress["curve"])
    bootstrap = r315.bootstrap(daily_returns)
    rolling = r315.rolling_returns(daily_returns, 252)
    rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
    robust_stats = {name: r112._stats(item["curve"]) for name, item in robust.items()}
    robust_yearly = {name: r315.year_returns(item["curve"]) for name, item in robust.items()}
    diagnostic_stats = r112._stats(diagnostic["curve"])
    diagnostic_yearly = r315.year_returns(diagnostic["curve"])

    fin = {}
    for name in ("metrics", "income", "balance_sheet", "cash_flow"):
        frame = pl.read_parquet(DATA / "financials" / name / "part.parquet", columns=["period_end", "announce_date"])
        fin[name] = {
            "max_period_end": str(frame["period_end"].max()),
            "max_announce_date": str(frame["announce_date"].max()),
            "future_announce_rows": frame.filter(pl.col("announce_date") > str(END)).height,
        }
    membership = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))
    equity_latest = latest_partition_date(DATA / "kline_daily_enriched")
    alt_latest = latest_partition_date(DATA / "ext_data" / "alt_hs300" / "timeseries")
    industry_path = DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
    data_gates = {
        "D1_asset_integrity": manifest["gates"]["D1_asset_integrity"] and primary["coverage"] >= 0.95 and all(x["coverage"] >= 0.95 for x in robust.values()),
        "D2_strategy_inputs": (
            equity_latest == END
            and alt_latest == END
            and all(item["max_period_end"] >= "2026-09-30" for item in fin.values())
            and membership.get("as_of") == str(END)
            and (DATA / "strategies" / "ai" / f"{A_STRATEGY}.py").exists()
            and (DATA / "strategies" / "ai" / f"{B_STRATEGY}.py").exists()
            and industry_path.exists()
        ),
        "D3_primary_liquidity": manifest["gates"]["D3_primary_liquidity"],
        "D4_curve_coverage": curve["date"].min() == START and curve["date"].max() == END,
    }
    performance_gates = {
        "G1_return": primary_stats["annual"] >= 0.06,
        "G2_drawdown": primary_stats["mdd"] >= -0.18,
        "G3_sharpe": primary_stats["sharpe"] >= 0.70,
        "G4_calendar_stability": primary_yearly.get("2022", -1.0) >= 0.0 and primary_yearly.get("2023", -1.0) >= 0.0,
        "G5_bootstrap": bootstrap["positive_rate"] >= 0.80 and bootstrap["return_p05"] >= -0.12,
        "G6_rolling_year": rolling_positive >= 0.70 and min(rolling) >= -0.15,
        "G7_recent_year": recent_stats["total_return"] > 0,
        "G8_stress": stress_stats["total_return"] > 0 and stress_stats["mdd"] >= -0.25,
        "G9_incremental": (
            (
                primary_stats["sharpe"] >= control_stats["sharpe"] + 0.05
                or primary_stats["mdd"] >= control_stats["mdd"] + 0.03
            )
            and primary_stats["annual"] >= control_stats["annual"] - 0.01
        ),
        "G10_no_leverage": (
            primary["weight_summary"]["max_weight_sum"] <= 1.0
            and primary["weight_summary"]["min_target_weight"] >= 0.0
        ),
    }
    robustness_gates = {
        name: (
            robust_stats[name]["annual"] >= 0.05
            and robust_stats[name]["mdd"] >= -0.20
            and robust_yearly[name].get("2022", -1.0) >= 0.0
            and robust_yearly[name].get("2023", -1.0) >= 0.0
        )
        for name in robust
    }

    curve_dates = risk20_2["date"].to_list()
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    def scaled_trades(trades: list[dict], rp_records: list[dict], allocation_records: list[dict]) -> list[dict]:
        output = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                equity_weight = r326.weight_at(allocation_records, day, "target_equity_weight")
                sleeve_share = r326.weight_at(rp_records, day, "weight_a")
                scales.append(equity_weight * (sleeve_share if trade["sleeve"] == "A" else 1.0 - sleeve_share))
            scale = sum(scales) / len(scales)
            output.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        return output

    def concentration(trades: list[dict], rp_records: list[dict], allocation_records: list[dict]) -> tuple[dict, dict, int]:
        scaled = scaled_trades(trades, rp_records, allocation_records)
        full = r239._aggregate(scaled, industry)
        recent_trades = [trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START]
        recent = r239._aggregate(recent_trades, industry)
        recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)
        return full, recent, recent_positive

    primary_full, primary_recent, primary_recent_positive = concentration(
        [*a20_trades, *b20_trades], rp20_records, primary["rebalances"]
    )
    diagnostic_full, _, diagnostic_recent_positive = concentration(
        [*a10_trades, *b10_trades], rp10_records, diagnostic["rebalances"]
    )
    gross_contributions = sum(max(value, 0.0) for value in primary["contributions"].values())
    contribution_shares = {
        key: round(value / gross_contributions, 6) if gross_contributions > 0 else None
        for key, value in primary["contributions"].items()
    }
    weight_summary = primary["weight_summary"]
    concentration_gates = {
        "C1_stock_breadth": primary_full["distinct_symbols"] >= 80,
        "C2_top1": primary_full["total_pnl_amount"] > 0 and primary_full["top1_net_share"] <= 0.25,
        "C3_top3": primary_full["total_pnl_amount"] > 0 and primary_full["top3_net_share"] <= 0.55,
        "C4_top5": primary_full["total_pnl_amount"] > 0 and primary_full["top5_net_share"] <= 0.75,
        "C5_recent_breadth": primary_recent_positive >= 8,
        "C6_industry": primary_full["top_industry_gross_share"] is not None and primary_full["top_industry_gross_share"] <= 0.45,
        "C7_loss": primary_full["max_symbol_loss_to_gross"] is not None and primary_full["max_symbol_loss_to_gross"] <= 0.20,
        "C8_asset_contribution": all(value is not None and value <= 0.50 for value in contribution_shares.values()),
        "C9_asset_weight": (
            weight_summary["gold_weight_mean"] <= 0.40
            and weight_summary["gold_weight_max"] <= 0.50
            and weight_summary["treasury_weight_mean"] <= 0.40
            and weight_summary["treasury_weight_max"] <= 0.50
            and weight_summary["dividend_weight_mean"] <= 0.40
            and weight_summary["dividend_weight_max"] <= 0.50
        ),
        "C10_liquidity": manifest["gates"]["D3_primary_liquidity"],
        "C11_turnover": primary["annual_turnover"] <= 1.5,
        "C12_weights": weight_summary["max_weight_sum"] <= 1.0 and weight_summary["min_target_weight"] >= 0.0,
    }
    all_pass = all(data_gates.values()) and all(performance_gates.values()) and all(concentration_gates.values()) and all(robustness_gates.values())
    payload = {
        "round": "R331",
        "classification": "preregistered three-asset defensive bucket with Top20 concentration repair; no weight grid",
        "preregistration_sha256": sha256(PREREG),
        "amendment_sha256": sha256(AMENDMENT),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)]},
        "data": {
            "primary_assets": PRIMARY,
            "robustness_assets": ROBUSTNESS,
            "asset_manifest_sha256": sha256(ASSET_MANIFEST),
            "asset_caches": manifest["caches"],
            "equity_enriched_latest": str(equity_latest),
            "alt_factors_latest": str(alt_latest),
            "membership_as_of": membership.get("as_of"),
            "financials": fin,
            "industry_snapshot_mtime": industry_path.stat().st_mtime,
        },
        "weights": weight_summary,
        "turnover": {"annual": round(primary["annual_turnover"], 6), "total": round(primary["total_turnover"], 6)},
        "total_overlay_cost_diagnostic": round(primary["total_cost"], 6),
        "asset_contributions": {key: round(value, 6) for key, value in primary["contributions"].items()},
        "asset_contribution_shares": contribution_shares,
        "primary_stats": primary_stats,
        "primary_yearly": primary_yearly,
        "recent_year": recent_stats,
        "bootstrap": bootstrap,
        "rolling_252": {
            "windows": len(rolling),
            "positive_rate": round(rolling_positive, 6),
            "return_p05": round(sorted(rolling)[int(0.05 * (len(rolling) - 1))], 6),
            "return_median": round(st.median(rolling), 6),
            "return_worst": round(min(rolling), 6),
            "return_best": round(max(rolling), 6),
        },
        "equity_only_top20_control": {"stats": control_stats, "yearly": control_yearly},
        "stress_3x": {"stats": stress_stats, "weights": stress["weight_summary"], "turnover": round(stress["annual_turnover"], 6)},
        "robustness": {
            name: {"stats": robust_stats[name], "yearly": robust_yearly[name], "weights": robust[name]["weight_summary"]}
            for name in robust
        },
        "curve_dates": {"start": str(curve["date"].min()), "end": str(curve["date"].max()), "days": curve.height},
        "diagnostic_top10": {
            "stats": diagnostic_stats,
            "yearly": diagnostic_yearly,
            "weights": diagnostic["weight_summary"],
            "concentration": diagnostic_full,
            "recent_positive_symbols": diagnostic_recent_positive,
        },
        "concentration_full": primary_full,
        "concentration_recent_12m": primary_recent,
        "recent_positive_symbols": primary_recent_positive,
        "data_gates": data_gates,
        "performance_gates": performance_gates,
        "concentration_gates": concentration_gates,
        "robustness_gates": robustness_gates,
        "selected": "top20_three_asset_bucket" if all_pass else None,
        "decision": {
            "status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed",
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
            "provider_write": False,
            "parameter_search": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
        "trades": scaled_trades([*a20_trades, *b20_trades], rp20_records, primary["rebalances"]),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "primary_stats": primary_stats,
        "primary_yearly": primary_yearly,
        "recent_year": recent_stats,
        "control": control_stats,
        "stress_3x": stress_stats,
        "weights": weight_summary,
        "asset_contribution_shares": contribution_shares,
        "concentration": {key: primary_full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "curve_dates": {"start": str(curve["date"].min()), "end": str(curve["date"].max()), "days": curve.height},
        "diagnostic_top10": {"stats": diagnostic_stats, "top5_net_share": diagnostic_full.get("top5_net_share")},
        "data_gates": data_gates,
        "performance_gates": performance_gates,
        "concentration_gates": concentration_gates,
        "robustness_gates": robustness_gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
