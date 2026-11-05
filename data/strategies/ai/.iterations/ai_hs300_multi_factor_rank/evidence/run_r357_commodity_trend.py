from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r357_commodity_trend_preregistration.json"
ASSET_MANIFEST = EVIDENCE / "r357_commodity_asset_manifest.json"
OUTPUT = EVIDENCE / "r357_commodity_trend_backtest.json"
COMMODITY_CACHE = DATA / "research" / "r357_commodity_assets" / "commodity_etfs_qfq.parquet"
STOCK_CACHE = DATA / "research" / "r344_cache"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), RECENT_START, date(2026, 1, 5)]
A = "ai_hs300_macro_gate_quality"
B = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C = "ai_hs300_quality_momentum_broad_h60"
POSITIONS = {A: 10, B: 10, C: 50}
SLEEVES = {A: "A", B: "B", C: "C"}
STRATEGIES = (A, B, C)
OVERLAY_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
C_WEIGHT_CAP = 0.10

sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def curve_from_cache(strategy_id: str, start: date, factor: int) -> pl.DataFrame:
    path = STOCK_CACHE / f"{strategy_id}_{start}_{factor}x_p{POSITIONS[strategy_id]}.parquet"
    if not path.exists():
        raise RuntimeError(f"R344 stock curve missing: {path.name}")
    curve = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    if curve["date"].max() != END:
        raise RuntimeError(f"R344 stock curve does not reach END: {path.name}")
    if start == START and curve["date"].min() != START:
        raise RuntimeError(f"R344 full stock curve does not start at START: {path.name}")
    return curve


def trades_from_cache(strategy_id: str, factor: int) -> list[dict]:
    path = STOCK_CACHE / f"{strategy_id}_{START}_{factor}x_p{POSITIONS[strategy_id]}.trades.json"
    if not path.exists():
        raise RuntimeError(f"R344 stock trades missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    segment = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if segment.height < 2:
        return None
    values = segment["value"].to_list()
    return values[-1] / values[0] - 1.0


def commodity_basket() -> pl.DataFrame:
    frame = pl.read_parquet(COMMODITY_CACHE).filter(pl.col("date").is_between(START, END))
    common = (
        frame.group_by("date")
        .agg(pl.col("symbol").n_unique().alias("symbols"))
        .filter(pl.col("symbols") == 3)
        .select("date")
    )
    prices = frame.join(common, on="date", how="inner").select("date", "symbol", "close")
    first = common["date"].min()
    first_prices = {
        row["symbol"]: float(row["close"])
        for row in prices.filter(pl.col("date") == first).iter_rows(named=True)
    }
    return (
        prices.with_columns((pl.col("close") / pl.col("symbol").replace_strict(first_prices)).alias("normalized"))
        .group_by("date")
        .agg(pl.col("normalized").mean().alias("close"))
        .sort("date")
    )


def dual_trend_proxy(
    gold: pl.DataFrame,
    commodity: pl.DataFrame,
    treasury: pl.DataFrame,
    dividend: pl.DataFrame,
    round_trip_cost: float,
) -> pl.DataFrame:
    joined = (
        gold.rename({"close": "gold"})
        .join(commodity.rename({"close": "commodity"}), on="date", how="inner")
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    gold_values = joined["gold"].to_list()
    commodity_values = joined["commodity"].to_list()
    treasury_values = joined["treasury"].to_list()
    dividend_values = joined["dividend"].to_list()
    states: list[str] = []
    for index in range(len(dates)):
        if index < 199:
            states.append("both_off")
            continue
        gold_on = gold_values[index] > sum(gold_values[index - 199:index + 1]) / 200.0
        commodity_on = commodity_values[index] > sum(commodity_values[index - 199:index + 1]) / 200.0
        states.append(
            "both_on" if gold_on and commodity_on
            else "gold_only" if gold_on
            else "commodity_only" if commodity_on
            else "both_off"
        )
    prices = [100.0]
    composition = states[0]
    for index in range(len(dates) - 1):
        switched = False
        if index > 0 and (dates[index].year, dates[index].month) != (dates[index - 1].year, dates[index - 1].month) and states[index] != composition:
            composition = states[index]
            switched = True
        gold_return = gold_values[index + 1] / gold_values[index] - 1.0
        commodity_return = commodity_values[index + 1] / commodity_values[index] - 1.0
        treasury_return = treasury_values[index + 1] / treasury_values[index] - 1.0
        dividend_return = dividend_values[index + 1] / dividend_values[index] - 1.0
        if composition == "both_on":
            daily_return = 0.5 * gold_return + 0.5 * commodity_return
        elif composition == "gold_only":
            daily_return = gold_return
        elif composition == "commodity_only":
            daily_return = commodity_return
        else:
            daily_return = 0.5 * (treasury_return + dividend_return)
        if switched:
            daily_return -= round_trip_cost / 3.0
        prices.append(prices[-1] * (1.0 + daily_return))
    return pl.DataFrame({"date": dates, "close": prices})


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R357 result exists; refusing overwrite")
    for path in (PREREG, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"R357 checksum mismatch: {path.name}")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_checks"] or not manifest["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R357 asset preflight did not pass")
    if manifest["cache"]["sha256"] != sha256(COMMODITY_CACHE):
        raise RuntimeError("R357 commodity cache hash changed")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r357")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r357")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r357")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r357")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r357")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r357")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r357")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r344_r357")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    before_r40 = r331.tree_hash(DATA / "research" / "r40_engine_data" / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")

    full_curves = {}
    full_trades = {}
    for factor in (2, 3, 4, 5):
        full_curves[factor] = tuple(curve_from_cache(strategy_id, START, factor) for strategy_id in STRATEGIES)
        if factor in (2, 5):
            full_trades[factor] = {
                strategy_id: trades_from_cache(strategy_id, factor)
                for strategy_id in STRATEGIES
            }
    phase_curves = {
        phase: tuple(curve_from_cache(strategy_id, phase, 2) for strategy_id in STRATEGIES)
        for phase in PHASES
    }

    risk = {}
    rp = {}
    equity = {}
    for factor in (2, 3, 4, 5):
        risk[factor], rp[factor] = r344._combine_three(full_curves[factor], r112.ROUND_TRIP_COST, C_WEIGHT_CAP)
        equity[factor] = r114._vol_target(risk[factor])["curve"]

    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    commodity = commodity_basket()

    def build(equity_curve: pl.DataFrame, round_trip_cost: float, *, candidate: bool) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = 60
            r331.TARGET_VOL = 0.15
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            if candidate:
                alt = dual_trend_proxy(gold, commodity, treasury, dividend, round_trip_cost)
            else:
                alt = r344.gold_trend_proxy(gold, treasury, dividend, round_trip_cost)
            return r331.build_three_asset_bucket(
                equity_curve,
                alt,
                treasury,
                dividend,
                round_trip_cost=round_trip_cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    candidate_core = {factor: build(equity[factor], OVERLAY_COST[factor], candidate=True) for factor in (2, 3, 4, 5)}
    control_core = {factor: build(equity[factor], OVERLAY_COST[factor], candidate=False) for factor in (2, 3, 4, 5)}
    candidate_3c = build(equity[3], OVERLAY_COST["3c"], candidate=True)
    control_3c = build(equity[3], OVERLAY_COST["3c"], candidate=False)

    industry_frame = r313.load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }

    def scaled_concentration(core: dict, factor: int) -> dict:
        curve_dates = candidate_core[factor]["curve"]["date"].to_list()
        scaled = []
        for strategy_id in STRATEGIES:
            for trade in full_trades[factor][strategy_id]:
                entry = r326.parse_day(trade["entry_date"])
                raw_exit = trade.get("exit_date")
                exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
                days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
                scales = []
                for day in days:
                    equity_weight = r326.weight_at(core["rebalances"], day, "target_equity_weight")
                    sleeve_share = r326.weight_at(rp[factor], day, f"weight_{SLEEVES[strategy_id].lower()}")
                    scales.append(equity_weight * sleeve_share)
                scale = sum(scales) / len(scales)
                scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(float(trade["pnl_amount"]) * scale, 6)})
        return r239._aggregate(scaled, industry)

    def asset_shares(core: dict) -> dict:
        contributions = core["contributions"]
        total = sum(max(float(value), 0.0) for value in contributions.values())
        return {
            key: (float(value) / total if total > 0 else None)
            for key, value in contributions.items()
        }

    def metrics(core: dict, conservative: bool = False) -> dict:
        curve = core["curve"]
        stats = r112._stats(curve)
        return {
            "stats": stats,
            "yearly": r315.year_returns(curve),
            "recent": segment_return(curve, RECENT_START),
            "segment_2022": segment_return(curve, START, date(2022, 12, 31)),
            "asset_contribution_shares": asset_shares(core),
            "weight_summary": core["weight_summary"],
            "annual_turnover": core["annual_turnover"],
        }

    candidate = {str(factor): metrics(candidate_core[factor]) for factor in (2, 3, 4, 5)}
    candidate["3x_conservative"] = metrics(candidate_3c, True)
    control = {str(factor): metrics(control_core[factor]) for factor in (2, 3, 4, 5)}
    control["3x_conservative"] = metrics(control_3c, True)
    candidate_conc = {"2x": scaled_concentration(candidate_core[2], 2), "5x": scaled_concentration(candidate_core[5], 5)}
    control_conc = {"2x": scaled_concentration(control_core[2], 2), "5x": scaled_concentration(control_core[5], 5)}

    fresh = {}
    for phase in PHASES:
        phase_risk, phase_rp = r344._combine_three(phase_curves[phase], r112.ROUND_TRIP_COST, C_WEIGHT_CAP)
        phase_equity = r114._vol_target(phase_risk)["curve"]
        core = build(phase_equity, OVERLAY_COST[2], candidate=True)
        fresh[str(phase)] = {
            "total_return": segment_return(core["curve"], phase),
            "stats": r112._stats(core["curve"]),
        }

    c2 = candidate["2"]["stats"]
    c3 = candidate["3"]["stats"]
    c4 = candidate["4"]["stats"]
    c5 = candidate["5"]["stats"]
    shares5 = asset_shares(candidate_core[5])
    gates = {
        "G1_2x_annual_at_least_0.12": c2["annual"] >= 0.12,
        "G2_2x_sharpe_at_least_1.10": c2["sharpe"] >= 1.10,
        "G3_2x_mdd_at_least_minus_0.12": c2["mdd"] >= -0.12,
        "G4_2022_segment_return_positive": (candidate["2"]["segment_2022"] or -1.0) > 0.0,
        "G5_2023_return_nonnegative": candidate["2"]["yearly"].get("2023", -1.0) >= 0.0,
        "G6_recent_year_return_positive": (candidate["2"]["recent"] or -1.0) > 0.0,
        "G7_3x_total_return_positive": c3["total_return"] > 0.0,
        "G8_4x_total_return_positive": c4["total_return"] > 0.0,
        "G9_5x_total_return_positive": c5["total_return"] > 0.0,
        "G10_5x_annual_at_least_0.10": c5["annual"] >= 0.10,
        "G11_5x_sharpe_at_least_0.90": c5["sharpe"] >= 0.90,
        "G12_5x_mdd_at_least_minus_0.15": c5["mdd"] >= -0.15,
        "G13_5x_top1_net_share_at_most_0.25": candidate_conc["5x"]["top1_net_share"] <= 0.25,
        "G14_5x_top3_net_share_at_most_0.55": candidate_conc["5x"]["top3_net_share"] <= 0.55,
        "G15_5x_top5_net_share_at_most_0.75": candidate_conc["5x"]["top5_net_share"] <= 0.75,
        "G16_5x_distinct_symbols_at_least_80": candidate_conc["5x"]["distinct_symbols"] >= 80,
        "G17_each_asset_contribution_at_most_0.50": all(value is not None and value <= 0.50 for value in shares5.values()),
        "G18_fresh_starts_at_least_5_of_6_positive": sum(1 for item in fresh.values() if (item["total_return"] or -1.0) > 0.0) >= 5,
    }
    all_pass = all(gates.values())
    payload = {
        "round": "R357",
        "classification": "portfolio-level dual-trend alt-slot screen using frozen R344 stock-sleeve curves",
        "preregistration_sha256": sha256(PREREG),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "commodity_cache_sha256": sha256(COMMODITY_CACHE),
        "stock_cache_dir": str(STOCK_CACHE.relative_to(ROOT)),
        "period": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "candidate": candidate,
        "control": control,
        "candidate_concentration": candidate_conc,
        "control_concentration": control_conc,
        "fresh_starts": fresh,
        "fresh_positive": sum(1 for item in fresh.values() if (item["total_return"] or -1.0) > 0.0),
        "gates": gates,
        "all_pass": all_pass,
        "selected": "r357_gold_commodity_dual_trend_alt_slot" if all_pass else None,
        "decision": {
            "status": "candidate_for_full_robustness" if all_pass else "no_candidate_passed",
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": r331.tree_hash(DATA / "research" / "r40_engine_data" / "kline_daily_enriched"),
            "production_before": before_prod,
            "production_after": r331.tree_hash(DATA / "kline_daily_enriched"),
        },
        "limits": [
            "Post-hoc portfolio screen using cached stock curves already studied in R344.",
            "The commodity basket is an equal-weight buy-and-hold ETF proxy, not a continuous futures or official total-return series.",
            "Industry and asset data retain the limitations documented in R344 and R331.",
            "This screen does not authorize paper trading; a pass would require separate plateau and replacement tests.",
        ],
        "no_leverage": True,
    }
    payload["input_isolation"]["stable"] = (
        payload["input_isolation"]["r40_before"] == payload["input_isolation"]["r40_after"]
        and payload["input_isolation"]["production_before"] == payload["input_isolation"]["production_after"]
    )
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
        "candidate_5x": c5,
        "candidate_concentration_5x": candidate_conc["5x"],
        "asset_shares_5x": shares5,
        "fresh_positive": payload["fresh_positive"],
        "gates": gates,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
