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
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
GOLD_CACHE = DATA / "research" / "r325_gold_assets" / "gold_etf_qfq.parquet"
PREREG = EVIDENCE / "r326_gold_concentration_preregistration.json"
R325_RESULT = EVIDENCE / "r325_gold_ballast.json"
OUTPUT = EVIDENCE / "r326_gold_concentration.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
GOLD_SYMBOL = "518880.SH"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
INITIAL_CAPITAL = 1_000_000.0


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def weight_at(records: list[dict], day: date, key: str) -> float:
    value = float(records[0][key])
    for record in records:
        if parse_day(record["date"]) <= day:
            value = float(record[key])
        else:
            break
    return value


def gold_monthly_pnl(curve: pl.DataFrame, gold: pl.DataFrame, rebalances: list[dict]) -> dict:
    records = sorted(rebalances, key=lambda row: row["date"])
    curve_rows = {row["date"]: float(row["value"]) for row in curve.iter_rows(named=True)}
    gold_prices = {row["date"]: float(row["close"]) for row in gold.iter_rows(named=True)}
    total = 0.0
    rows = []
    for index, record in enumerate(records):
        start = parse_day(record["date"])
        next_start = parse_day(records[index + 1]["date"]) if index + 1 < len(records) else END
        start_values = [day for day in sorted(gold_prices) if day >= start and day < next_start]
        if len(start_values) < 2 or start not in curve_rows:
            continue
        first_day = start_values[0]
        last_day = start_values[-1]
        gold_return = gold_prices[last_day] / gold_prices[first_day] - 1.0
        nav_start = curve_rows[start]
        contribution = INITIAL_CAPITAL * nav_start * float(record["target_bond_weight"]) * gold_return
        total += contribution
        rows.append(
            {
                "start": str(start),
                "end": str(last_day),
                "nav_start": round(nav_start, 6),
                "gold_weight": round(float(record["target_bond_weight"]), 6),
                "gold_return": round(gold_return, 6),
                "contribution": round(contribution, 6),
            }
        )
    return {"total": round(total, 6), "months": len(rows), "rows": rows}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R326 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R326 preregistration checksum mismatch")
    if not json.loads(R325_RESULT.read_text(encoding="utf-8"))["selected"] == "primary_gold":
        raise RuntimeError("R325 did not produce the preregistered gold candidate")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r326")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r326")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r326")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r326")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r326")
    r324.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    before_r40 = r313.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r313.tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r326_"))
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

        def run(strategy_id: str) -> tuple[pl.DataFrame, list[dict]]:
            cfg = r324.StrategyBacktestConfig(
                strategy_id=strategy_id,
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
            with r324.patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id}: {result.error}")
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
            return r324.curve_frame(result.equity_curve), trades

        a_curve, a_trades = run(A_STRATEGY)
        b_curve, b_trades = run(B_STRATEGY)
        risk, rp_records = r112._combine(a_curve, b_curve, "risk")
        equity_curve = r114._vol_target(risk)["curve"]
        curve_dates = risk["date"].to_list()
        gold_frame = pl.read_parquet(GOLD_CACHE).filter(pl.col("symbol") == GOLD_SYMBOL).sort("date")
        gold_prices = gold_frame.select(["date", "close"])
        ballast = r324.build_ballast(equity_curve, gold_prices, round_trip_cost=0.001)
        allocation_records = ballast["rebalances"]

        def holding_scale(trade: dict) -> float:
            entry = parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_]
            if not days:
                days = [entry]
            scales = []
            for day in days:
                equity_weight = weight_at(allocation_records, day, "target_equity_weight")
                a_share = weight_at(rp_records, day, "weight_a")
                scales.append(equity_weight * (a_share if trade["sleeve"] == "A" else 1.0 - a_share))
            return sum(scales) / len(scales)

        scaled = []
        for trade in [*a_trades, *b_trades]:
            scale = holding_scale(trade)
            scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r313.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r313.tree_hash(DATA / "kline_daily_enriched")
    industry_frame = r313.load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    full = r239._aggregate(scaled, industry)
    recent_trades = [trade for trade in scaled if parse_day(trade["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)
    stock_pnl = sum(trade["pnl_amount"] for trade in scaled)
    gold_pnl = gold_monthly_pnl(ballast["curve"], gold_prices, allocation_records)
    total_approx_pnl = stock_pnl + gold_pnl["total"]
    gold_contribution_share = gold_pnl["total"] / total_approx_pnl if total_approx_pnl > 0 else None
    gold_weights = [float(record["target_bond_weight"]) for record in allocation_records]
    gold_amount = gold_frame.filter((pl.col("date") >= START) & (pl.col("date") <= END)).with_columns(
        pl.col("amount").cast(pl.Float64)
    )
    median_amount = float(gold_amount["amount"].median())
    min_adv20 = float(
        gold_amount.with_columns(pl.col("amount").rolling_mean(20).alias("adv20"))["adv20"].drop_nulls().min()
    )
    aligned_dates = set(equity_curve["date"].to_list())
    gold_dates = set(gold_prices.filter((pl.col("date") >= START) & (pl.col("date") <= END))["date"].to_list())
    gold_coverage = len(aligned_dates & gold_dates) / len(aligned_dates)

    gates = {
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
        "C11_turnover": ballast["annual_turnover"] <= 1.0,
        "C12_no_leverage": ballast["weight_summary"]["max_weight_sum"] <= 1.0 and ballast["weight_summary"]["min_target_weight"] >= 0.0,
    }
    payload = {
        "round": "R326",
        "classification": "preregistered concentration and ETF-liquidity audit of R325; no strategy changes",
        "preregistration_sha256": sha(PREREG),
        "r325_result_sha256": sha(R325_RESULT),
        "runner_sha256": sha(Path(__file__).resolve()),
        "window": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "sleeves": {
            "A": {"strategy": A_STRATEGY, "trades": len(a_trades)},
            "B": {"strategy": B_STRATEGY, "trades": len(b_trades)},
        },
        "portfolio": {
            "risk_parity_records": len(rp_records),
            "allocation_records": len(allocation_records),
            "annual_turnover": round(ballast["annual_turnover"], 6),
            "weight_summary": ballast["weight_summary"],
        },
        "pnl_attribution": {
            "scaled_stock_pnl": round(stock_pnl, 6),
            "approx_gold_pnl": gold_pnl["total"],
            "approx_total_pnl": round(total_approx_pnl, 6),
            "gold_contribution_share": round(gold_contribution_share, 6) if gold_contribution_share is not None else None,
            "gold_months": gold_pnl["months"],
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
        "concentration_full": full,
        "concentration_recent_12m": recent,
        "recent_positive_symbols": recent_positive,
        "gold_monthly_pnl": gold_pnl,
        "gates": gates,
        "selected": all(gates.values()),
        "decision": {
            "status": "candidate_for_paper_trading_review" if all(gates.values()) else "research_only_concentration_failed",
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
        "caveats": [
            "Attribution uses holding-period average weights and monthly gold PnL; it is a contribution-share diagnostic, not compounded accounting.",
            "Industry classification and factor versions are current snapshots, not historical PIT.",
            "Gold ETF prices and amounts are provider-reconstructed, not an official PIT archive.",
            "R40 local research data and canonical data drift remain unresolved.",
            "A pass only permits paper-execution review, not production deployment.",
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
        "full": {key: full.get(key) for key in ("n_trades", "distinct_symbols", "total_pnl_amount", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "recent": {key: recent.get(key) for key in ("n_trades", "distinct_symbols", "total_pnl_amount")},
        "recent_positive_symbols": recent_positive,
        "pnl_attribution": payload["pnl_attribution"],
        "gold_weight": payload["gold_weight"],
        "gold_liquidity": payload["gold_liquidity"],
        "gates": gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
