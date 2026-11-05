from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import statistics as st
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
PREREG = EVIDENCE / "r327_gold_top20_preregistration.json"
R312_RESULT = EVIDENCE / "r312_dual_sleeve_vol15.json"
OUTPUT = EVIDENCE / "r327_gold_top20.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
GOLD_PRIMARY = "518880.SH"
GOLD_ROBUSTNESS = "159937.SZ"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
MAX_POSITIONS = 20
INITIAL_CAPITAL = 1_000_000.0


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R327 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R327 preregistration checksum mismatch")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r327")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r327")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r327")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r327")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r327")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r327")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r327")
    r324.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    before_r40 = r313.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r313.tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r327_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
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
        service = r324.StrategyBacktestService(
            r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine
        )

        def run(strategy_id: str, cost_factor: int, *, capture_trades: bool = False):
            cfg = r324.StrategyBacktestConfig(
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
                raise RuntimeError(f"{strategy_id} {cost_factor}x: {result.error}")
            curve = r324.curve_frame(result.equity_curve)
            trades = []
            if capture_trades:
                trades = [
                    {
                        "sleeve": "A" if strategy_id == A_STRATEGY else "B",
                        "symbol": trade.get("symbol"),
                        "name": trade.get("name"),
                        "entry_date": str(trade.get("entry_date")),
                        "exit_date": str(trade.get("exit_date")),
                        "pnl_pct": trade.get("pnl_pct"),
                        "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                        "exit_reason": trade.get("exit_reason"),
                    }
                    for trade in result.trades
                ]
            print(f"ran {strategy_id} top{MAX_POSITIONS} {cost_factor}x: {r112._stats(curve)}", flush=True)
            return curve, trades

        a2, a_trades = run(A_STRATEGY, 2, capture_trades=True)
        b2, b_trades = run(B_STRATEGY, 2, capture_trades=True)
        a3, _ = run(A_STRATEGY, 3)
        b3, _ = run(B_STRATEGY, 3)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r313.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r313.tree_hash(DATA / "kline_daily_enriched")
    risk2, rp_records = r112._combine(a2, b2, "risk")
    equity2 = r114._vol_target(risk2)["curve"]
    risk3, _ = r112._combine(a3, b3, "risk")
    equity3 = r114._vol_target(risk3)["curve"]
    gold = pl.read_parquet(DATA / "research" / "r325_gold_assets" / "gold_etf_qfq.parquet").select(["symbol", "date", "close", "amount"])
    primary_gold = gold.filter(pl.col("symbol") == GOLD_PRIMARY).select(["date", "close"]).sort("date")
    robustness_gold = gold.filter(pl.col("symbol") == GOLD_ROBUSTNESS).select(["date", "close"]).sort("date")
    primary = r324.build_ballast(equity2, primary_gold, round_trip_cost=0.001)
    robustness = r324.build_ballast(equity2, robustness_gold, round_trip_cost=0.001)
    stress = r324.build_ballast(equity3, primary_gold, round_trip_cost=0.003)
    primary_curve = primary["curve"]
    robustness_curve = robustness["curve"]
    stress_curve = stress["curve"]
    primary_values = primary_curve["value"].to_list()
    primary_daily_returns = [primary_values[i] / primary_values[i - 1] - 1.0 for i in range(1, len(primary_values))]
    primary_yearly = r315.year_returns(primary_curve)
    robustness_yearly = r315.year_returns(robustness_curve)
    baseline_r312 = json.loads(R312_RESULT.read_text(encoding="utf-8"))["full_path"]["combined_vol15"]
    bootstrap = r315.bootstrap(primary_daily_returns)
    rolling = r315.rolling_returns(primary_daily_returns, 252)
    rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
    primary_stats = r112._stats(primary_curve)
    robustness_stats = r112._stats(robustness_curve)
    stress_stats = r112._stats(stress_curve)
    recent_stats = r112._stats(r324.slice_curve(primary_curve, RECENT_START, END))
    performance_gates = {
        "D1_data": primary["coverage"] >= 0.95 and robustness["coverage"] >= 0.95,
        "G1_return": primary_stats["annual"] >= 0.06,
        "G2_drawdown": primary_stats["mdd"] >= -0.18,
        "G3_sharpe": primary_stats["sharpe"] >= 0.70,
        "G4_calendar_stability": primary_yearly.get("2022", -1.0) >= 0.0 and primary_yearly.get("2023", -1.0) >= 0.0,
        "G5_bootstrap": bootstrap["positive_rate"] >= 0.80 and bootstrap["return_p05"] >= -0.12,
        "G6_rolling_year": rolling_positive >= 0.70 and min(rolling) >= -0.15,
        "G7_recent_year": recent_stats["total_return"] > 0.0,
        "G8_stress": stress_stats["total_return"] > 0.0 and stress_stats["mdd"] >= -0.25,
        "G9_incremental": (
            (primary_stats["sharpe"] >= baseline_r312["sharpe"] + 0.05 or primary_stats["mdd"] >= baseline_r312["mdd"] + 0.03)
            and primary_stats["annual"] >= baseline_r312["annual"] - 0.01
        ),
        "G10_no_leverage": primary["weight_summary"]["max_weight_sum"] <= 1.0 and primary["weight_summary"]["min_target_weight"] >= 0.0,
        "R1_robustness": (
            robustness_yearly.get("2022", -1.0) >= 0.0
            and robustness_yearly.get("2023", -1.0) >= 0.0
            and robustness_stats["mdd"] >= -0.20
        ),
    }

    curve_dates = risk2["date"].to_list()
    allocation_records = primary["rebalances"]

    def holding_scale(trade: dict) -> float:
        entry = r326.parse_day(trade["entry_date"])
        raw_exit = trade.get("exit_date")
        exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
        days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
        scales = []
        for day in days:
            equity_weight = r326.weight_at(allocation_records, day, "target_equity_weight")
            a_share = r326.weight_at(rp_records, day, "weight_a")
            scales.append(equity_weight * (a_share if trade["sleeve"] == "A" else 1.0 - a_share))
        return sum(scales) / len(scales)

    scaled = []
    for trade in [*a_trades, *b_trades]:
        scale = holding_scale(trade)
        scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
    industry_frame = r313.load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    full = r239._aggregate(scaled, industry)
    recent_trades = [trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)
    stock_pnl = sum(trade["pnl_amount"] for trade in scaled)
    gold_pnl = r326.gold_monthly_pnl(primary_curve, primary_gold, allocation_records)
    total_approx_pnl = stock_pnl + gold_pnl["total"]
    gold_contribution_share = gold_pnl["total"] / total_approx_pnl if total_approx_pnl > 0 else None
    gold_weights = [float(record["target_bond_weight"]) for record in allocation_records]
    gold_amount = gold.filter(pl.col("symbol") == GOLD_PRIMARY).filter((pl.col("date") >= START) & (pl.col("date") <= END)).with_columns(pl.col("amount").cast(pl.Float64))
    median_amount = float(gold_amount["amount"].median())
    min_adv20 = float(gold_amount.with_columns(pl.col("amount").rolling_mean(20).alias("adv20"))["adv20"].drop_nulls().min())
    aligned_dates = set(equity2["date"].to_list())
    gold_dates = set(primary_gold.filter((pl.col("date") >= START) & (pl.col("date") <= END))["date"].to_list())
    gold_coverage = len(aligned_dates & gold_dates) / len(aligned_dates)
    concentration_gates = {
        "C1_stock_coverage": full["distinct_symbols"] >= 80,
        "C2_top1": full["total_pnl_amount"] > 0 and full["top1_net_share"] <= 0.25,
        "C3_top3": full["total_pnl_amount"] > 0 and full["top3_net_share"] <= 0.55,
        "C4_top5": full["total_pnl_amount"] > 0 and full["top5_net_share"] <= 0.75,
        "C5_recent_breadth": recent_positive >= 8,
        "C6_industry": full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45,
        "C7_loss": full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20,
        "C8_gold_contribution": gold_contribution_share is not None and gold_contribution_share <= 0.75,
        "C9_gold_weight": st.mean(gold_weights) <= 0.55 and max(gold_weights) <= 0.75,
        "C10_etf_liquidity": gold_coverage >= 0.95 and median_amount >= 100_000_000 and min_adv20 >= 50_000_000,
        "C11_turnover": primary["annual_turnover"] <= 1.0,
        "C12_no_leverage": primary["weight_summary"]["max_weight_sum"] <= 1.0 and primary["weight_summary"]["min_target_weight"] >= 0.0,
    }
    all_pass = all(performance_gates.values()) and all(concentration_gates.values())
    payload = {
        "round": "R327",
        "classification": "preregistered single-variant top20 diversification test; no TopN grid",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "recent_start": str(RECENT_START)},
        "intervention": {"max_positions_per_sleeve": MAX_POSITIONS},
        "data": {
            "primary_gold": GOLD_PRIMARY,
            "robustness_gold": GOLD_ROBUSTNESS,
            "primary_coverage": round(primary["coverage"], 6),
            "robustness_coverage": round(robustness["coverage"], 6),
        },
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
        },
        "robustness": {"stats": robustness_stats, "yearly": robustness_yearly},
        "stress_3x": stress_stats,
        "weights": primary["weight_summary"],
        "turnover": {"annual": round(primary["annual_turnover"], 6), "total": round(primary["total_turnover"], 6)},
        "concentration_full": full,
        "concentration_recent_12m": recent,
        "recent_positive_symbols": recent_positive,
        "pnl_attribution": {
            "scaled_stock_pnl": round(stock_pnl, 6),
            "approx_gold_pnl": gold_pnl["total"],
            "approx_total_pnl": round(total_approx_pnl, 6),
            "gold_contribution_share": round(gold_contribution_share, 6) if gold_contribution_share is not None else None,
        },
        "gold_weight": {
            "mean": round(st.mean(gold_weights), 6),
            "median": round(st.median(gold_weights), 6),
            "min": round(min(gold_weights), 6),
            "max": round(max(gold_weights), 6),
        },
        "gold_liquidity": {
            "coverage": round(gold_coverage, 6),
            "median_daily_amount_cny": round(median_amount, 2),
            "min_20day_average_amount_cny": round(min_adv20, 2),
        },
        "performance_gates": performance_gates,
        "concentration_gates": concentration_gates,
        "selected": "gold_top20" if all_pass else None,
        "decision": {
            "status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed",
            "production_write": False,
            "provider_call": False,
            "parameter_search": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": [
            "Top20 is a single post-R326 diversification response, not a grid-selected optimum.",
            "Gold ETF qfq data and the sample-period gold bull market remain major external dependencies.",
            "Attribution is an approximate contribution-share diagnostic.",
            "A pass only permits a separate paper-execution review, not production deployment.",
        ],
        "trades": scaled,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "primary": primary_stats,
        "primary_yearly": primary_yearly,
        "bootstrap": bootstrap,
        "rolling_positive": rolling_positive,
        "stress_3x": stress_stats,
        "concentration": {key: full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "pnl_attribution": payload["pnl_attribution"],
        "gold_weight": payload["gold_weight"],
        "gold_liquidity": payload["gold_liquidity"],
        "performance_gates": performance_gates,
        "concentration_gates": concentration_gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
